"""The structured paper artifact consumed by downstream agents."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Equation(BaseModel):
    description: str = Field(
        description="What the equation expresses and why it matters to the method"
    )
    latex: str = Field(description="The equation in valid LaTeX, without delimiters")


class ToyExample(BaseModel):
    """A concrete small input/output pair used to seed acceptance tests."""

    description: str = Field(
        description="What behavior of the paper's method this small example exercises"
    )
    example_input: str = Field(description="A concrete, minimal input")
    expected_output: str = Field(
        description="The concrete output or invariant an implementation should produce"
    )


class Understanding(BaseModel):
    problem_statement: str = Field(
        description="The precise problem, limitations of prior work, and paper objective"
    )
    method_summary: str = Field(
        description="A grounded explanation of the proposed method and its novelty"
    )
    key_algorithm: str = Field(
        description="Implementation-oriented pseudocode, represented as a string"
    )
    equations: list[Equation] = Field(
        default_factory=list,
        description="Only equations essential to implementing the core method",
    )
    architecture_notes: str = Field(
        description="Components, data flow, shapes, dependencies, and implementation details"
    )
    toy_example: ToyExample = Field(
        description="A small input/output example suitable for an acceptance test"
    )
