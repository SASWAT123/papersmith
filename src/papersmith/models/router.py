"""Central LiteLLM-backed model routing.

Every agent in PaperSmith gets its model from one of the three factories below
instead of hardcoding a model string. That's the whole point: swapping models
later (a cheaper fast-tier model, a different verifier family, a new provider)
means editing `.env`, not touching agent code.

We route through Agno's LiteLLM model wrapper (`agno.models.litellm.LiteLLM`)
rather than Agno's native `Claude`/`OpenAIChat` classes so that a single
LiteLLM `Router` config (declared here) can do provider fallback and unified
cost/usage accounting — see ARCHITECTURE.md 2.1.
"""

from __future__ import annotations

import os

from agno.models.litellm import LiteLLM
from dotenv import load_dotenv
from litellm import Router

load_dotenv()


def _model_id(env_var: str, default: str) -> str:
    return os.environ.get(env_var, default)


PRIMARY_MODEL = _model_id("PAPERSMITH_PRIMARY_MODEL", "anthropic/claude-sonnet-4-5")
FALLBACK_MODEL = _model_id("PAPERSMITH_FALLBACK_MODEL", "openai/gpt-4o")

# litellm's own default request timeout is 6000s (its library default, not a
# PaperSmith choice) - effectively unbounded for an interactive workflow. A
# single slow/stuck completion (e.g. a structured-output call made after an
# agent's tool_call_limit is reached) can then block the whole synchronous
# Streamlit request for up to that long with no feedback. Bound it here so a
# stuck call fails fast instead.
REQUEST_TIMEOUT_SECONDS = float(os.environ.get("PAPERSMITH_LLM_TIMEOUT_SECONDS", "90"))

router = Router(
    model_list=[
        {
            "model_name": PRIMARY_MODEL,
            "litellm_params": {"model": PRIMARY_MODEL, "timeout": REQUEST_TIMEOUT_SECONDS},
        },
        {
            "model_name": FALLBACK_MODEL,
            "litellm_params": {"model": FALLBACK_MODEL, "timeout": REQUEST_TIMEOUT_SECONDS},
        },
    ],
    fallbacks=[{PRIMARY_MODEL: [FALLBACK_MODEL]}],
)


# Agno's LiteLLM wrapper always sends both temperature and top_p (defaults
# 0.7 / 1.0) unless a field is explicitly set to None. OpenAI accepts both at
# once; Anthropic's API rejects any request that sets both ("temperature and
# top_p cannot both be specified"). Every factory below drops top_p so the
# same call shape works across providers instead of only working to work by
# accident on whichever provider happens to be configured.
def primary() -> LiteLLM:
    """Strong-reasoning model: planning and code generation."""
    return LiteLLM(
        id=PRIMARY_MODEL, client=router, supports_native_structured_outputs=True, top_p=None
    )


def fast() -> LiteLLM:
    """Cheap, high-volume model: extraction, summarization, ReAct chatter."""
    return LiteLLM(
        id=_model_id("PAPERSMITH_FAST_MODEL", "openai/gpt-4o-mini"),
        supports_native_structured_outputs=True,
        request_params={"timeout": REQUEST_TIMEOUT_SECONDS},
        top_p=None,
    )


def verifier() -> LiteLLM:
    """A different model family from primary, to reduce correlated mistakes."""
    return LiteLLM(
        id=_model_id("PAPERSMITH_VERIFIER_MODEL", "openai/gpt-4o"),
        supports_native_structured_outputs=True,
        request_params={"timeout": REQUEST_TIMEOUT_SECONDS},
        top_p=None,
    )

