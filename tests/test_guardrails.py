"""Tests for retry and budget ceilings."""

import pytest

from papersmith.agents import code as code_agent
from papersmith.agents.code import CodeBundle
from papersmith.models.budget import BudgetExceeded, check_budget
from papersmith.schemas.plan import AcceptanceTest, Milestone
from papersmith.tools.sandbox import ExecResult


def test_budget_halts_when_exceeded(monkeypatch):
    monkeypatch.setenv("PAPERSMITH_MAX_RUN_BUDGET_USD", "1.00")
    with pytest.raises(BudgetExceeded):
        check_budget(spend_usd=1.50)


def test_budget_ok_under_limit(monkeypatch):
    monkeypatch.setenv("PAPERSMITH_MAX_RUN_BUDGET_USD", "1.00")
    check_budget(spend_usd=0.10)  # should not raise


def test_milestone_retry_ceiling_halts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PAPERSMITH_MAX_RETRIES", "3")
    calls = 0

    class Response:
        content = CodeBundle(implementation="pass\n", acceptance_test="assert False\n")

    class AlwaysWrongAgent:
        def __init__(self, **_kwargs):
            pass

        def run(self, _message):
            nonlocal calls
            calls += 1
            return Response()

    monkeypatch.setattr(code_agent, "Agent", AlwaysWrongAgent)
    monkeypatch.setattr(code_agent, "primary", lambda: object())
    monkeypatch.setattr(
        code_agent,
        "run_generated_test",
        lambda _path: ExecResult(1, "", "deliberate failure", False),
    )
    milestone = Milestone(
        name="always-fails",
        description="Exercise retry ceiling",
        interface="def broken(): ...",
        acceptance_test=AcceptanceTest(
            description="Always fail", test_input="none", expected_property="failure"
        ),
    )

    result = code_agent.execute_milestone(milestone, "guardrail-test")

    assert result.status == "failed"
    assert result.attempts == 3
    assert calls == 3
