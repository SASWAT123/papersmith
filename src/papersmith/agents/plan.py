"""Turn a compressed understanding artifact into an implementation plan."""

from __future__ import annotations

from papersmith.models.router import primary
from papersmith.models.budget import record_response_cost
from papersmith.memory.episodic import PreferenceMemory
from papersmith.prompts.plan import PLAN_SYSTEM_PROMPT
from papersmith.schemas.plan import Plan


def make_plan(understanding_path: str) -> Plan:
    """Read understanding.md (NOT the vector store — by this phase we work
    from the compressed artifact, per ARCHITECTURE.md 6) and produce an
    ordered milestone plan; serialize to data/artifacts/<run_id>/plan.md.

    Planning deliberately uses the primary model because decomposition and
    test design materially affect the quality of every later stage.
    """
    source_path = Path(understanding_path)
    if not source_path.is_file():
        raise FileNotFoundError(f"Understanding artifact not found: {source_path}")
    understanding = source_path.read_text(encoding="utf-8")
    agent = Agent(
        name="papersmith-planner",
        model=primary(),
        instructions=PLAN_SYSTEM_PROMPT,
        output_schema=Plan,
        structured_outputs=True,
        markdown=False,
    )
    preferences = PreferenceMemory().all()
    preference_context = (
        "\n\nPersisted user preferences:\n" + str(preferences) if preferences else ""
    )
    response = agent.run(
        "Create an implementation plan from this Understanding artifact:\n\n"
        + understanding
        + preference_context
    )
    record_response_cost(response)
    plan = _parse_plan(response.content)
    _validate_plan(plan)
    plan_path = source_path.with_name("plan.md")
    plan_path.write_text(render_plan_markdown(plan), encoding="utf-8")
    plan_path.with_suffix(".json").write_text(
        plan.model_dump_json(indent=2), encoding="utf-8"
    )
    return plan


def _parse_plan(content: Any) -> Plan:
    if isinstance(content, Plan):
        return content
    if isinstance(content, str):
        return Plan.model_validate_json(content)
    return Plan.model_validate(content)


def _validate_plan(plan: Plan) -> None:
    if not plan.milestones:
        raise ValueError("The implementation plan must contain at least one milestone")
    names = [item.name for item in plan.milestones]
    if len(names) != len(set(names)):
        raise ValueError("Milestone names must be unique")
    seen: set[str] = set()
    for milestone in plan.milestones:
        missing = set(milestone.depends_on) - seen
        if missing:
            raise ValueError(
                f"Milestone {milestone.name!r} depends on unavailable or later "
                f"milestones: {sorted(missing)}"
            )
        seen.add(milestone.name)


def render_plan_markdown(plan: Plan) -> str:
    sections = ["# Implementation Plan"]
    for index, milestone in enumerate(plan.milestones, start=1):
        dependencies = ", ".join(milestone.depends_on) or "None"
        sections.append(
            f"## {index}. {milestone.name}\n\n"
            f"{milestone.description}\n\n"
            f"**Interface**\n\n```python\n{milestone.interface}\n```\n\n"
            f"**Acceptance test:** {milestone.acceptance_test.description}\n\n"
            f"- Input: `{milestone.acceptance_test.test_input}`\n"
            f"- Expected: {milestone.acceptance_test.expected_property}\n"
            f"- Depends on: {dependencies}"
        )
    return "\n\n".join(sections) + "\n"
from pathlib import Path
from typing import Any

from agno.agent import Agent
