"""Command-line interface for local PaperSmith workflows."""

from __future__ import annotations

import json

import typer
from rich.console import Console

from papersmith.memory.episodic import PreferenceMemory
from papersmith.workflows.pipeline import PaperSmithWorkflow

app = typer.Typer(no_args_is_help=True)
console = Console()


@app.command()
def run(
    source: str,
    run_id: str | None = typer.Option(None, help="Stable identifier for this run"),
) -> None:
    """Ingest SOURCE and stop after producing a plan for approval."""
    state = PaperSmithWorkflow().start(source, run_id=run_id)
    _print_state(state)


@app.command()
def approve(run_id: str) -> None:
    """Approve the current plan and continue the run."""
    _print_state(PaperSmithWorkflow().approve(run_id))


@app.command()
def resume(run_id: str) -> None:
    """Resume an interrupted run from its last persisted checkpoint."""
    _print_state(PaperSmithWorkflow().resume(run_id))


@app.command()
def status(run_id: str) -> None:
    """Show persisted state for a run."""
    _print_state(PaperSmithWorkflow().store.load(run_id))


@app.command("set-preference")
def set_preference(name: str, value: str) -> None:
    """Store a preference shared by future runs."""
    PreferenceMemory().set(name, value)
    console.print(f"Saved preference [bold]{name}[/bold].")


def _print_state(state) -> None:
    console.print_json(json.dumps(state.model_dump(mode="json")))
    if state.status == "awaiting_plan_approval":
        console.print(
            f"Review {state.plan_path}, then run: "
            f"uv run python scripts/run.py approve {state.run_id}"
        )
    elif state.status == "done":
        console.print(f"Delivered: {state.output_dir}")
    elif state.status == "failed":
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
