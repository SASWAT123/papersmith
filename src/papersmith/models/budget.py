"""Run-level budget guardrail helpers."""

import os
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

_active_run: ContextVar[str | None] = ContextVar("papersmith_run", default=None)
_spend_by_run: dict[str, float] = {}


class BudgetExceeded(RuntimeError):
    pass


def check_budget(spend_usd: float) -> None:
    limit = float(os.environ.get("PAPERSMITH_MAX_RUN_BUDGET_USD", "2.00"))
    if spend_usd >= limit:
        raise BudgetExceeded(f"Run spend ${spend_usd:.4f} exceeded limit ${limit:.2f}")


@contextmanager
def track_run(run_id: str, initial_spend: float = 0.0) -> Iterator[None]:
    _spend_by_run[run_id] = max(_spend_by_run.get(run_id, 0.0), initial_spend)
    token = _active_run.set(run_id)
    try:
        yield
    finally:
        _active_run.reset(token)


def record_response_cost(response: Any) -> float:
    run_id = _active_run.get()
    cost = float(getattr(getattr(response, "metrics", None), "cost", 0.0) or 0.0)
    if run_id:
        _spend_by_run[run_id] = _spend_by_run.get(run_id, 0.0) + cost
    return cost


def current_spend(run_id: str) -> float:
    return _spend_by_run.get(run_id, 0.0)
