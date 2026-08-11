# PaperSmith User Manual

## What PaperSmith produces

Given a paper or technical article, PaperSmith creates:

- a structured understanding document;
- an implementation plan for your approval;
- a small Python reference implementation;
- executable acceptance tests;
- a delivery README explaining the generated output.

## Requirements

- macOS or Linux
- Python 3.12 or newer
- [uv](https://docs.astral.sh/uv/)
- OpenAI and/or Anthropic API credentials — every run calls real models, there is no offline mode

## Install

From the repository root:

```bash
uv sync
cp .env.example .env
```

Edit `.env` and set valid provider keys and model names. Never commit `.env`.

## Run a paper

Supported inputs are local PDF/HTML paths, HTTP/HTTPS URLs, arXiv abstract/PDF URLs, and arXiv identifiers.

```bash
uv run python scripts/run.py run ./paper.pdf --run-id my-paper
```

Or:

```bash
uv run python scripts/run.py run https://arxiv.org/abs/1706.03762 --run-id attention
```

PaperSmith ingests the paper, builds LanceDB, creates the understanding and plan, and stops with `awaiting_plan_approval`.

Review:

```bash
cat data/artifacts/my-paper/understanding.md
cat data/artifacts/my-paper/plan.md
```

Approve the plan to generate and verify the code:

```bash
uv run python scripts/run.py approve my-paper
```

Once every milestone passes, the run finishes with status `done` directly — no further approval needed, since the code is already generated, tested, and independently verified. Inspect and execute the generated result:

```bash
cat output/my-paper/README.md
cat output/my-paper/prototype.py
uv run python output/my-paper/test_*.py
```

## Resume or inspect a run

Show persisted state:

```bash
uv run python scripts/run.py status my-paper
```

Resume an interrupted active stage:

```bash
uv run python scripts/run.py resume my-paper
```

Completed artifacts and passed milestones are skipped during resume.

## Set a cross-run preference

Preferences are stored in the local SQLite database and included in future planning prompts.

```bash
uv run python scripts/run.py set-preference framework numpy
```

## Inspect LanceDB

```bash
uv run python - <<'PY'
import json
import lancedb

table = lancedb.connect("data/vectorstore/my-paper").open_table("paper_chunks")
for row in table.to_arrow().to_pylist():
    payload = json.loads(row["payload"])
    print(payload["meta_data"]["section"], payload["content"])
PY
```

## Configuration

The main `.env` settings are:

| Setting | Purpose | Default |
|---|---|---|
| `PAPERSMITH_PRIMARY_MODEL` | Planning and coding model | `anthropic/claude-sonnet-4-5` |
| `PAPERSMITH_FAST_MODEL` | Comprehension model | `anthropic/claude-sonnet-4-5` |
| `PAPERSMITH_VERIFIER_MODEL` | Independent verification model (different family than primary) | `openai/gpt-4o` |
| `PAPERSMITH_MAX_RUN_BUDGET_USD` | Maximum recorded model cost | `2.00` |
| `PAPERSMITH_MAX_RETRIES` | Coding attempts per milestone | `4` |
| `PAPERSMITH_MAX_VERIFY_RETRIES` | Regeneration attempts after a failed faithfulness review | `2` |
| `PAPERSMITH_EXEC_TIMEOUT_SECONDS` | Generated-test timeout | `30` |
| `PAPERSMITH_LLM_TIMEOUT_SECONDS` | Per-call LLM request timeout | `90` |
| `PAPERSMITH_COMPREHEND_TIMEOUT_SECONDS` | Hard cap on the whole comprehension agent run | `180` |
| `PAPERSMITH_SQLITE_PATH` | Workflow database | `./data/papersmith.db` |

## Run validation

```bash
uv run pytest -q
```

The suite is network-free and covers ingestion, retrieval, structured artifacts, budget enforcement, retry bounds, approvals, resume behavior, generated code, and final delivery.

## Troubleshooting

`ModuleNotFoundError: papersmith` usually means a package file was invoked directly. Use `uv run python scripts/run.py ...` from the repository root.

If a real run fails, inspect its `error` field:

```bash
uv run python scripts/run.py status RUN_ID
```

Common causes are missing API keys, an unavailable model name, an inaccessible URL, budget exhaustion, or generated tests exceeding their retry/timeout limits.
