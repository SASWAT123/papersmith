"""Retrieval-backed structured comprehension agent."""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import lancedb
from agno.agent import Agent

from papersmith.models.router import fast
from papersmith.models.budget import record_response_cost
from papersmith.prompts.comprehend import COMPREHEND_SYSTEM_PROMPT
from papersmith.schemas.understanding import Understanding
from papersmith.tools.ingest import LocalHashEmbedder

_TABLE_NAME = "paper_chunks"
_DEFAULT_TOP_K = 5
_MAX_TOP_K = 10
_TIMEOUT_SECONDS = float(os.environ.get("PAPERSMITH_COMPREHEND_TIMEOUT_SECONDS", "180"))


def comprehend(run_id: str, vectorstore_path: str) -> Understanding:
    """Retrieve relevant chunks per Understanding field and produce the
    structured artifact; serialize it to
    data/artifacts/<run_id>/understanding.md.

    `fast()` is a deliberate choice here: extraction/retrieval does not need
    the primary model's reasoning budget (ARCHITECTURE.md 2.3).

    Agno validates the model response against ``Understanding`` before the
    artifact is persisted.
    """
    safe_run_id = _safe_run_id(run_id)
    search_paper = _build_retrieval_tool(vectorstore_path)
    agent = Agent(
        name="papersmith-comprehension",
        model=fast(),
        instructions=COMPREHEND_SYSTEM_PROMPT,
        tools=[search_paper],
        tool_call_limit=12,
        output_schema=Understanding,
        structured_outputs=True,
        markdown=False,
    )
    response = _run_with_timeout(agent)
    record_response_cost(response)
    understanding = _parse_understanding(response.content)
    artifact_path = (
        Path(os.environ.get("PAPERSMITH_ARTIFACTS_DIR", "data/artifacts"))
        / safe_run_id
        / "understanding.md"
    )
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        render_understanding_markdown(understanding), encoding="utf-8"
    )
    return understanding


def _run_with_timeout(agent: Agent) -> Any:
    """Run the agent with a hard wall-clock cap.

    Agno's ``tool_call_limit`` only blocks a tool from *executing* once the
    cap is hit - it feeds the model a "limit reached" error instead and lets
    it try again. If the model keeps requesting tools anyway (observed with
    gpt-4o-mini on thin/short papers), the agent's while-loop has no other
    exit condition and runs indefinitely, one real - and billed - completion
    call after another. A per-request LLM timeout (models/router.py) doesn't
    help here since each individual call still succeeds quickly; it's the
    loop itself that never ends. This wraps the whole call in a thread with
    its own timeout so a stuck run fails fast instead of hanging the
    workflow (and the Streamlit request) forever.
    """
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(
        agent.run,
        "Build the structured understanding artifact for the indexed paper. "
        "Retrieve evidence separately for every target listed in your instructions.",
    )
    try:
        return future.result(timeout=_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError as exc:
        raise TimeoutError(
            f"Comprehension agent exceeded {_TIMEOUT_SECONDS:.0f}s without "
            "finalizing - it likely kept requesting tool calls past its "
            "tool_call_limit instead of returning an answer."
        ) from exc
    finally:
        executor.shutdown(wait=False)


def _build_retrieval_tool(vectorstore_path: str) -> Callable[[str, int], str]:
    """Create a read-only semantic-search tool scoped to one paper table."""
    path = Path(vectorstore_path)
    if not path.is_dir():
        raise FileNotFoundError(f"Vector store does not exist: {vectorstore_path}")

    db = lancedb.connect(path)
    table_names = db.list_tables().tables
    if _TABLE_NAME not in table_names:
        raise ValueError(
            f"Vector store {vectorstore_path} has no {_TABLE_NAME!r} table; "
            f"available tables: {table_names}"
        )
    table = db.open_table(_TABLE_NAME)
    if table.count_rows() == 0:
        raise ValueError(f"Vector store table {_TABLE_NAME!r} is empty")
    embedder = LocalHashEmbedder()

    def search_paper(query: str, top_k: int = _DEFAULT_TOP_K) -> str:
        """Search paper chunks for one focused topic.

        Args:
            query: A narrow semantic query for a single understanding field.
            top_k: Number of chunks to return, from 1 through 10.

        Returns:
            JSON containing ranked chunk text and section metadata.
        """
        query = query.strip()
        if not query:
            raise ValueError("query must not be empty")
        top_k = max(1, min(int(top_k), _MAX_TOP_K))
        rows = (
            table.search(embedder.get_embedding(query))
            .limit(top_k)
            .to_arrow()
            .to_pylist()
        )
        results: list[dict[str, Any]] = []
        for rank, row in enumerate(rows, start=1):
            payload = json.loads(row["payload"])
            metadata = payload.get("meta_data") or {}
            results.append(
                {
                    "rank": rank,
                    "section": metadata.get("section", "unknown"),
                    "chunk_index": metadata.get("chunk_index"),
                    "text": payload.get("content", ""),
                }
            )
        return json.dumps(results, ensure_ascii=False)

    return search_paper


def _parse_understanding(content: Any) -> Understanding:
    if isinstance(content, Understanding):
        return content
    if isinstance(content, str):
        return Understanding.model_validate_json(content)
    return Understanding.model_validate(content)


def render_understanding_markdown(understanding: Understanding) -> str:
    """Render the validated handoff schema as a stable, readable artifact."""
    equations = "\n\n".join(
        f"### {index}. {equation.description}\n\n"
        f"```latex\n{equation.latex}\n```"
        for index, equation in enumerate(understanding.equations, start=1)
    )
    if not equations:
        equations = "No implementation-essential equations were identified."

    toy = understanding.toy_example
    return (
        "# Paper Understanding\n\n"
        "## Problem Statement\n\n"
        f"{understanding.problem_statement.strip()}\n\n"
        "## Method Summary\n\n"
        f"{understanding.method_summary.strip()}\n\n"
        "## Key Algorithm\n\n"
        f"```text\n{understanding.key_algorithm.strip()}\n```\n\n"
        "## Equations\n\n"
        f"{equations}\n\n"
        "## Architecture Notes\n\n"
        f"{understanding.architecture_notes.strip()}\n\n"
        "## Toy Example\n\n"
        f"{toy.description.strip()}\n\n"
        "### Input\n\n"
        f"```text\n{toy.example_input.strip()}\n```\n\n"
        "### Expected Output\n\n"
        f"```text\n{toy.expected_output.strip()}\n```\n"
    )


def _safe_run_id(run_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", run_id).strip(".-")
    if not safe:
        raise ValueError("run_id must contain at least one letter or digit")
    return safe
