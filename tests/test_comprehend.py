"""Unit tests for retrieval and structured artifact generation."""

from __future__ import annotations

import json
from pathlib import Path

import lancedb

from papersmith.agents import comprehend as comprehend_module
from papersmith.schemas.understanding import Equation, ToyExample, Understanding
from papersmith.tools.ingest import embed_and_store
from papersmith.tools.pdf_reader import Section


def _understanding() -> Understanding:
    return Understanding(
        problem_statement="Existing systems cannot solve the target problem.",
        method_summary="The paper composes two small processing stages.",
        key_algorithm="INPUT x\ny = stage_two(stage_one(x))\nRETURN y",
        equations=[Equation(description="Core update", latex="y = f(x)")],
        architecture_notes="Stage one feeds stage two.",
        toy_example=ToyExample(
            description="A minimal constructed example.",
            example_input="x = 1",
            expected_output="y = 2",
        ),
    )


def test_retrieval_tool_returns_section_aware_chunks(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = embed_and_store(
        [
            Section(
                title="Method",
                text="Queries and keys produce attention scores; values are combined.",
            )
        ],
        run_id="retrieval-test",
    )

    search = comprehend_module._build_retrieval_tool(path)
    results = json.loads(search("attention queries keys values", top_k=3))

    assert results
    assert results[0]["section"] == "Method"
    assert "attention" in results[0]["text"]


def test_comprehend_writes_validated_markdown(tmp_path: Path, monkeypatch) -> None:
    vectorstore = tmp_path / "vectors"
    db = lancedb.connect(vectorstore)
    db.create_table(
        "paper_chunks",
        data=[{"vector": [0.0] * 384, "id": "1", "payload": "{}"}],
    )
    expected = _understanding()

    class FakeResponse:
        content = expected

    class FakeAgent:
        def __init__(self, **kwargs):
            assert kwargs["output_schema"] is Understanding
            assert len(kwargs["tools"]) == 1

        def run(self, _message: str) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr(comprehend_module, "Agent", FakeAgent)
    monkeypatch.setattr(comprehend_module, "fast", lambda: object())
    monkeypatch.chdir(tmp_path)

    result = comprehend_module.comprehend("phase-3", str(vectorstore))
    artifact = Path("data/artifacts/phase-3/understanding.md")

    assert result == expected
    assert artifact.is_file()
    text = artifact.read_text(encoding="utf-8")
    assert "# Paper Understanding" in text
    assert "## Toy Example" in text
    assert "y = f(x)" in text
