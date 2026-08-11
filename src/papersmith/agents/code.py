"""Bounded, fresh-context implementation loop for one milestone."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agno.agent import Agent
from pydantic import BaseModel, Field

from papersmith.models.router import primary
from papersmith.models.budget import record_response_cost
from papersmith.prompts.code import CODE_SYSTEM_PROMPT_TEMPLATE
from papersmith.schemas.plan import Milestone
from papersmith.tools.sandbox import ExecResult


class CodeBundle(BaseModel):
    implementation: str = Field(description="Complete contents of prototype.py")
    acceptance_test: str = Field(description="Complete standalone Python test file")


@dataclass
class MilestoneResult:
    milestone_name: str
    status: str  # "passed" | "failed"
    attempts: int
    final_exec: ExecResult | None


def execute_milestone(
    milestone: Milestone, run_id: str, verifier_feedback: str = ""
) -> MilestoneResult:
    """Loop up to PAPERSMITH_MAX_RETRIES times. CRITICAL: each iteration must
    construct a BRAND NEW Agent instance, seeded only with:
      - the milestone spec (small, from plan.md)
      - current file contents (from disk, output/<run_id>/)
      - the last error (from the previous ExecResult, NOT conversation
        history)
      - verifier_feedback, if the caller is retrying after a *previous*
        execute_milestone() call passed its own test but was rejected by
        verify_milestone() for faithfulness. This is a fixed string supplied
        by the caller (pipeline.py), not state this function accumulates
        itself - it stays constant across every attempt in *this* call, same
        as milestone_spec.

    Nothing else carries over between attempts — this is what makes the loop
    RALPH-style instead of one ever-growing ReAct transcript. Verify by
    logging each attempt's prompt token count and confirming it stays flat.

    Files on disk and the most recent execution result are the only mutable
    context carried between attempts.
    """
    max_retries = int(os.environ.get("PAPERSMITH_MAX_RETRIES", "4"))
    if max_retries < 1:
        raise ValueError("PAPERSMITH_MAX_RETRIES must be at least 1")
    safe_run_id = _safe_name(run_id)
    output_dir = Path(os.environ.get("PAPERSMITH_OUTPUT_DIR", "output")) / safe_run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    implementation_path = output_dir / "prototype.py"
    test_name = f"test_{_safe_name(milestone.name).lower()}.py"
    test_path = output_dir / test_name
    last_exec: ExecResult | None = None

    for attempt in range(1, max_retries + 1):
        current_files = _current_files(output_dir)
        last_error = _format_error(last_exec)
        prompt = CODE_SYSTEM_PROMPT_TEMPLATE.format(
            milestone_spec=milestone.model_dump_json(indent=2),
            current_files=current_files,
            last_error=last_error,
            verifier_feedback=verifier_feedback or "(none)",
        )
        agent = Agent(
            name=f"papersmith-coder-{attempt}",
            model=primary(),
            instructions=prompt,
            output_schema=CodeBundle,
            structured_outputs=True,
            markdown=False,
        )
        response = agent.run(
            "Implement this milestone and its standalone acceptance test."
        )
        record_response_cost(response)
        bundle = _parse_bundle(response.content)
        implementation_path.write_text(bundle.implementation, encoding="utf-8")
        test_path.write_text(bundle.acceptance_test, encoding="utf-8")
        last_exec = run_generated_test(test_path)
        if last_exec.exit_code == 0 and not last_exec.timed_out:
            return MilestoneResult(milestone.name, "passed", attempt, last_exec)

    return MilestoneResult(milestone.name, "failed", max_retries, last_exec)


def run_generated_test(test_path: Path) -> ExecResult:
    from papersmith.tools.sandbox import run_python

    return run_python(test_path.name, cwd=str(test_path.parent))


def _parse_bundle(content: Any) -> CodeBundle:
    if isinstance(content, CodeBundle):
        return content
    if isinstance(content, str):
        return CodeBundle.model_validate_json(content)
    return CodeBundle.model_validate(content)


def _current_files(output_dir: Path) -> str:
    files: list[str] = []
    for path in sorted(output_dir.glob("*.py")):
        files.append(f"--- {path.name} ---\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(files) if files else "(no files yet)"


def _format_error(result: ExecResult | None) -> str:
    if result is None:
        return ""
    return (
        f"exit_code={result.exit_code}; timed_out={result.timed_out}\n"
        f"stdout:\n{result.stdout[-4000:]}\n"
        f"stderr:\n{result.stderr[-4000:]}"
    )


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", value).strip(".-")
    if not safe:
        raise ValueError("name must contain a letter or digit")
    return safe
