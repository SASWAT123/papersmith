"""Offline end-to-end validation of durable checkpoints and generated code.

Network-free by mocking each stage's `Agent` (and model factory) at the same
seam test_comprehend.py/test_guardrails.py already use, rather than a
demo_mode flag in production code - everything else (ingestion, artifact
writing, plan validation, sandboxed test execution, resume) runs for real.
"""

from pathlib import Path

from papersmith.workflows.pipeline import PaperSmithWorkflow, StateStore
from papersmith.tools.sandbox import run_python
from papersmith.schemas.plan import AcceptanceTest, Milestone, Plan
from papersmith.schemas.understanding import Equation, ToyExample, Understanding


def _fake_understanding() -> Understanding:
    return Understanding(
        problem_statement="Raw measurements are noisy; a local average should smooth short fluctuations while preserving the sequence's trend.",
        method_summary="For every complete window of width w, compute the arithmetic mean of its consecutive values. The valid output contains n-w+1 values.",
        key_algorithm="INPUT values, window\nVALIDATE 1 <= window <= len(values)\nFOR start FROM 0 TO len(values)-window\n  APPEND sum(values[start:start+window]) / window\nRETURN averages",
        equations=[Equation(description="Mean of the window beginning at i", latex="m_i = \\frac{1}{w}\\sum_{j=0}^{w-1} x_{i+j}")],
        architecture_notes="A single pure function accepts a numeric sequence and positive integer window. It returns floating-point means and raises ValueError for invalid widths.",
        toy_example=ToyExample(description="A width-two moving average.", example_input="values=[1, 3, 5, 7], window=2", expected_output="[2.0, 4.0, 6.0]"),
    )


def _fake_plan() -> Plan:
    return Plan(
        milestones=[
            Milestone(
                name="moving-average",
                description="Implement the valid one-dimensional moving average described by the paper.",
                interface="def moving_average(values: list[float], window: int) -> list[float]",
                acceptance_test=AcceptanceTest(
                    description="Compute adjacent width-two means and reject an invalid width.",
                    test_input="values=[1, 3, 5, 7], window=2",
                    expected_property="Returns [2.0, 4.0, 6.0]; window=0 raises ValueError",
                ),
            )
        ]
    )


def _fake_code_bundle():
    from papersmith.agents.code import CodeBundle

    return CodeBundle(
        implementation=(
            '"""Reference implementation generated from the moving-average paper."""\n\n'
            "def moving_average(values: list[float], window: int) -> list[float]:\n"
            "    if window < 1 or window > len(values):\n"
            "        raise ValueError(\"window must be between 1 and len(values)\")\n"
            "    return [sum(values[i:i + window]) / window for i in range(len(values) - window + 1)]\n"
        ),
        acceptance_test=(
            "from prototype import moving_average\n\n"
            "assert moving_average([1, 3, 5, 7], 2) == [2.0, 4.0, 6.0]\n"
            "try:\n    moving_average([1, 2], 0)\n"
            "except ValueError:\n    pass\n"
            "else:\n    raise AssertionError('invalid window must raise ValueError')\n"
        ),
    )


def _patch_agents(monkeypatch) -> None:
    import papersmith.agents.comprehend as comprehend_module
    import papersmith.agents.plan as plan_module
    import papersmith.agents.code as code_module
    import papersmith.agents.verify as verify_module
    from papersmith.agents.verify import Verdict

    class _FakeResponse:
        def __init__(self, content):
            self.content = content

    def _fake_agent(content):
        class _FakeAgent:
            def __init__(self, **_kwargs):
                pass

            def run(self, _message):
                return _FakeResponse(content)

        return _FakeAgent

    monkeypatch.setattr(comprehend_module, "Agent", _fake_agent(_fake_understanding()))
    monkeypatch.setattr(comprehend_module, "fast", lambda: object())
    monkeypatch.setattr(plan_module, "Agent", _fake_agent(_fake_plan()))
    monkeypatch.setattr(plan_module, "primary", lambda: object())
    monkeypatch.setattr(code_module, "Agent", _fake_agent(_fake_code_bundle()))
    monkeypatch.setattr(code_module, "primary", lambda: object())
    monkeypatch.setattr(verify_module, "Agent", _fake_agent(Verdict(faithful=True, concerns=[])))
    monkeypatch.setattr(verify_module, "verifier", lambda: object())


def test_workflow_end_to_end(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PAPERSMITH_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("PAPERSMITH_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    _patch_agents(monkeypatch)
    source = tmp_path / "paper.html"
    source.write_text(
        "<h1>Moving average</h1><h2>Method</h2>"
        "<p>Average every complete adjacent window.</p>",
        encoding="utf-8",
    )
    workflow = PaperSmithWorkflow(StateStore(str(tmp_path / "state.db")))

    planned = workflow.start(str(source), run_id="demo")
    assert planned.status == "awaiting_plan_approval"
    assert Path(planned.understanding_path).is_file()
    assert Path(planned.plan_path).is_file()

    complete = workflow.approve("demo")
    assert complete.status == "done"
    assert complete.milestone_statuses == {"moving-average": "passed"}
    output = Path(complete.output_dir)
    assert run_python("test_moving-average.py", str(output)).exit_code == 0
    assert (output / "README.md").is_file()

    assert workflow.resume("demo").status == "done"
