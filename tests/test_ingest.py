"""Local ingestion integration test."""

import lancedb

from papersmith.tools import ingest


def test_ingest_known_paper(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    paper = tmp_path / "paper.html"
    paper.write_text(
        "<h1>Simple method</h1><h2>Algorithm</h2>"
        "<p>Average every complete adjacent window in a sequence.</p>",
        encoding="utf-8",
    )
    raw = ingest.fetch(str(paper), run_id="test-paper")
    sections = ingest.extract_sections(raw)
    assert len(sections) > 0

    table_path = ingest.embed_and_store(sections, run_id="test-paper")
    table = lancedb.connect(table_path).open_table("paper_chunks")
    assert table.count_rows() > 0
