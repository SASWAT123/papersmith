# PaperSmith

PaperSmith converts a research paper or technical article into a small, tested Python reference implementation using retrieval-backed Agno agents, LiteLLM model routing, LanceDB, and durable local workflow state.

## Quick start

Copy `.env.example` to `.env` and configure provider credentials (required — every run calls real models), then:

```bash
uv sync
uv run python scripts/run.py run ./paper.pdf --run-id my-paper   # or a URL / arXiv id
uv run python scripts/run.py approve my-paper                    # review the plan first
uv run python output/my-paper/test_*.py
```

See [USER_MANUAL.md](USER_MANUAL.md) for complete operating instructions, [ARCHITECTURE.md](ARCHITECTURE.md) for the system design and Mermaid diagrams, and [WALKTHROUGH.md](WALKTHROUGH.md) for a worked, stage-by-stage tour of the durable workflow using a real paper.

## Streamlit UI

```bash
uv run streamlit run scripts/app.py
```

Paste a paper URL/arXiv ID or upload a PDF/HTML file, review the generated understanding and plan, then approve code generation to get a tested prototype. Requires provider credentials in `.env`.
