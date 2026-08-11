"""Structured implementation plans produced before code generation."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AcceptanceTest(BaseModel):
    """A concrete, assertable check defined before implementation."""

    description: str
    test_input: str
    expected_property: str


class Milestone(BaseModel):
    name: str
    description: str
    interface: str = Field(description="Expected function/class signatures")
    acceptance_test: AcceptanceTest
    depends_on: list[str] = Field(default_factory=list)
    status: str = "pending"


class Plan(BaseModel):
    milestones: list[Milestone]
