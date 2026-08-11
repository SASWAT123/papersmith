"""Durable application workflow with explicit approval boundaries."""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from pathlib import Path

from papersmith.memory.state import PaperSmithState
from papersmith.models.budget import (
    BudgetExceeded,
    check_budget,
    current_spend,
    track_run,
)
from papersmith.schemas.plan import Plan
from papersmith.tools.ingest import embed_and_store, extract_sections, fetch


class StateStore:
    """Persist compact workflow checkpoints in SQLite."""

    def __init__(self, path: str | None = None) -> None:
        self.path = Path(
            path or os.environ.get("PAPERSMITH_SQLITE_PATH", "data/papersmith.db")
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS runs ("
                "run_id TEXT PRIMARY KEY, state_json TEXT NOT NULL, "
                "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )

    def save(self, state: PaperSmithState) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO runs(run_id, state_json) VALUES (?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET state_json=excluded.state_json, "
                "updated_at=CURRENT_TIMESTAMP",
                (state.run_id, state.model_dump_json()),
            )

    def load(self, run_id: str) -> PaperSmithState:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if not row:
            raise KeyError(f"Unknown run: {run_id}")
        return PaperSmithState.model_validate_json(row[0])

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


class PaperSmithWorkflow:
    def __init__(self, store: StateStore | None = None) -> None:
        self.store = store or StateStore()

    def start(self, source: str, run_id: str | None = None) -> PaperSmithState:
        run_id = _safe_run_id(run_id or f"run-{uuid.uuid4().hex[:10]}")
        state = PaperSmithState(
            run_id=run_id,
            source=source,
            output_dir=str(Path(os.environ.get("PAPERSMITH_OUTPUT_DIR", "output")) / run_id),
        )
        self.store.save(state)
        return self._prepare(state)

    def resume(self, run_id: str) -> PaperSmithState:
        state = self.store.load(run_id)
        if state.status in {"ingesting", "comprehending", "planning"}:
            return self._prepare(state)
        if state.status in {"executing", "verifying"}:
            return self._execute(state)
        # A "failed" run stopped mid-_prepare or mid-_execute; re-enter
        # whichever stage it failed in rather than no-op'ing. plan_path only
        # exists once planning has succeeded, so it's what distinguishes the
        # two (mirrors the artifact-presence checks _prepare already uses).
        if state.status == "failed":
            return self._execute(state) if state.plan_path else self._prepare(state)
        return state

    def approve(self, run_id: str) -> PaperSmithState:
        state = self.store.load(run_id)
        if state.status == "awaiting_plan_approval":
            state.status = "executing"
            self.store.save(state)
            return self._execute(state)
        raise ValueError(f"Run {run_id} is not awaiting approval ({state.status})")

    def _prepare(self, state: PaperSmithState) -> PaperSmithState:
        try:
            check_budget(state.spend_usd)
            if not state.vectorstore_path:
                state.status = "ingesting"
                self.store.save(state)
                raw = fetch(state.source, state.run_id)
                sections = extract_sections(raw)
                state.vectorstore_path = embed_and_store(sections, state.run_id)
                self.store.save(state)

            artifact_dir = Path(os.environ.get("PAPERSMITH_ARTIFACTS_DIR", "data/artifacts")) / state.run_id
            artifact_dir.mkdir(parents=True, exist_ok=True)
            understanding_path = artifact_dir / "understanding.md"
            if not state.understanding_path:
                state.status = "comprehending"
                self.store.save(state)
                from papersmith.agents.comprehend import comprehend

                with track_run(state.run_id, state.spend_usd):
                    comprehend(state.run_id, state.vectorstore_path)
                state.spend_usd = current_spend(state.run_id)
                check_budget(state.spend_usd)
                state.understanding_path = str(understanding_path)
                self.store.save(state)

            plan_path = artifact_dir / "plan.md"
            if not state.plan_path:
                state.status = "planning"
                self.store.save(state)
                from papersmith.agents.plan import make_plan

                with track_run(state.run_id, state.spend_usd):
                    make_plan(state.understanding_path)
                state.spend_usd = current_spend(state.run_id)
                check_budget(state.spend_usd)
                state.plan_path = str(plan_path)
                state.milestone_statuses = {
                    item.name: "pending" for item in _load_plan(plan_path).milestones
                }
            state.status = "awaiting_plan_approval"
            self.store.save(state)
            return state
        except Exception as exc:
            return self._fail(state, exc)

    def _execute(self, state: PaperSmithState) -> PaperSmithState:
        try:
            check_budget(state.spend_usd)
            if not state.plan_path or not state.understanding_path or not state.output_dir:
                raise ValueError("Run is missing required artifact paths")
            plan = _load_plan(Path(state.plan_path))
            output_dir = Path(state.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            max_verify_retries = int(os.environ.get("PAPERSMITH_MAX_VERIFY_RETRIES", "3"))
            for milestone in plan.milestones:
                if state.milestone_statuses.get(milestone.name) == "passed":
                    continue

                verdict_ok = False
                verdict_concerns: list[str] = []
                verifier_feedback = ""
                for verify_attempt in range(1, max_verify_retries + 1):
                    state.status = "executing"
                    state.milestone_statuses[milestone.name] = "in_progress"
                    self.store.save(state)
                    from papersmith.agents.code import execute_milestone

                    with track_run(state.run_id, state.spend_usd):
                        result_ok = execute_milestone(
                            milestone, state.run_id, verifier_feedback=verifier_feedback
                        ).status == "passed"
                    state.spend_usd = current_spend(state.run_id)
                    check_budget(state.spend_usd)
                    if not result_ok:
                        state.milestone_statuses[milestone.name] = "failed"
                        raise RuntimeError(f"Coding failed for milestone {milestone.name}")

                    state.status = "verifying"
                    self.store.save(state)
                    from papersmith.agents.verify import verify_milestone

                    with track_run(state.run_id, state.spend_usd):
                        verdict = verify_milestone(
                            milestone,
                            str(output_dir / "prototype.py"),
                            state.understanding_path,
                        )
                    verdict_ok = verdict.faithful
                    verdict_concerns = verdict.concerns
                    state.spend_usd = current_spend(state.run_id)
                    check_budget(state.spend_usd)

                    if verdict_ok:
                        break
                    # Feed the reviewer's specific objections into the next
                    # code-generation attempt instead of just retrying blind.
                    verifier_feedback = "\n".join(f"- {c}" for c in verdict_concerns)

                state.milestone_statuses[milestone.name] = (
                    "passed" if verdict_ok else "failed"
                )
                self.store.save(state)
                if not verdict_ok:
                    concerns_text = "; ".join(verdict_concerns) or "no concerns reported"
                    raise RuntimeError(
                        f"Faithfulness verification failed for {milestone.name} "
                        f"after {max_verify_retries} attempt(s): {concerns_text}"
                    )

            _write_delivery_readme(state, plan)
            state.status = "done"
            self.store.save(state)
            return state
        except (Exception, BudgetExceeded) as exc:
            return self._fail(state, exc)

    def _fail(self, state: PaperSmithState, exc: Exception) -> PaperSmithState:
        state.status = "failed"
        state.error = f"{type(exc).__name__}: {exc}"
        self.store.save(state)
        return state


def _load_plan(path: Path) -> Plan:
    json_path = path.with_suffix(".json")
    if not json_path.is_file():
        raise FileNotFoundError(f"Structured plan not found: {json_path}")
    return Plan.model_validate_json(json_path.read_text(encoding="utf-8"))


def _write_delivery_readme(state: PaperSmithState, plan: Plan) -> None:
    output_dir = Path(state.output_dir or "")
    milestone_lines = "\n".join(
        f"- {item.name}: {state.milestone_statuses.get(item.name, 'unknown')}"
        for item in plan.milestones
    )
    output_dir.joinpath("README.md").write_text(
        f"# Generated Paper Prototype\n\nSource: `{state.source}`\n\n"
        "## Paper-to-code mapping\n\n"
        "`prototype.py` implements the interfaces and behavior defined in the approved plan.\n\n"
        f"## Milestones\n\n{milestone_lines}\n\n"
        "## Run the generated checks\n\n```bash\n"
        "for test in test_*.py; do python \"$test\"; done\n```\n",
        encoding="utf-8",
    )


def _safe_run_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", value).strip(".-")
    if not safe:
        raise ValueError("run_id must contain a letter or digit")
    return safe
