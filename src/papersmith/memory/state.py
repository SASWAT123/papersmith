"""Compact persisted workflow state; large content remains in artifacts."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PaperSmithState(BaseModel):
    run_id: str
    source: str
    status: str = "ingesting"
    vectorstore_path: str | None = None
    understanding_path: str | None = None
    plan_path: str | None = None
    milestone_statuses: dict[str, str] = Field(default_factory=dict)
    output_dir: str | None = None
    spend_usd: float = 0.0
    error: str | None = None
