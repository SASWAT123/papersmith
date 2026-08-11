"""Execution and independent model-based faithfulness verification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agno.agent import Agent
from pydantic import BaseModel

from papersmith.models.router import verifier
from papersmith.models.budget import record_response_cost
from papersmith.prompts.verify import VERIFY_SYSTEM_PROMPT
from papersmith.schemas.plan import Milestone
from papersmith.tools.sandbox import run_python


class Verdict(BaseModel):
    faithful: bool
    concerns: list[str]


def verify_milestone(
    milestone: Milestone, code_path: str, understanding_path: str
) -> Verdict:
    """1) Run milestone.acceptance_test via tools/sandbox.run_python — cheap,
    no LLM needed. 2) If it passes, ask a `verifier()`-backed agent (a
    DIFFERENT model family than whatever wrote the code) whether the
    implementation is faithful to understanding.md, not just whether it runs.

    """
    implementation_path = Path(code_path)
    understanding_file = Path(understanding_path)
    if not implementation_path.is_file():
        return Verdict(faithful=False, concerns=[f"Code not found: {code_path}"])
    if not understanding_file.is_file():
        return Verdict(
            faithful=False,
            concerns=[f"Understanding artifact not found: {understanding_path}"],
        )

    slug = _slug(milestone.name)
    test_path = implementation_path.parent / f"test_{slug}.py"
    if not test_path.is_file():
        return Verdict(
            faithful=False,
            concerns=[f"Acceptance test not found: {test_path.name}"],
        )
    execution = run_python(test_path.name, cwd=str(test_path.parent))
    if execution.exit_code != 0 or execution.timed_out:
        detail = execution.stderr.strip() or execution.stdout.strip()
        return Verdict(
            faithful=False,
            concerns=[f"Acceptance test failed: {detail[-2000:]}"],
        )

    agent = Agent(
        name="papersmith-verifier",
        model=verifier(),
        instructions=VERIFY_SYSTEM_PROMPT,
        output_schema=Verdict,
        structured_outputs=True,
        markdown=False,
    )
    response = agent.run(
        f"MILESTONE\n{milestone.model_dump_json(indent=2)}\n\n"
        f"UNDERSTANDING\n{understanding_file.read_text(encoding='utf-8')}\n\n"
        f"IMPLEMENTATION\n{implementation_path.read_text(encoding='utf-8')}"
    )
    record_response_cost(response)
    return _parse_verdict(response.content)


def _parse_verdict(content: Any) -> Verdict:
    if isinstance(content, Verdict):
        return content
    if isinstance(content, str):
        return Verdict.model_validate_json(content)
    return Verdict.model_validate(content)


def _slug(value: str) -> str:
    import re

    return re.sub(r"[^A-Za-z0-9_.-]", "-", value).strip(".-").lower()
