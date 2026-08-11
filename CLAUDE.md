# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

PaperSmith turns a research paper (PDF/HTML/URL/arXiv id) into a small, tested Python reference implementation. It uses Agno agents routed through LiteLLM, LanceDB for retrieval, and SQLite for durable workflow state. Everything is local; no external infra is required. See [README.md](README.md), [ARCHITECTURE.md](ARCHITECTURE.md) (with Mermaid diagrams of the workflow), and [USER_MANUAL.md](USER_MANUAL.md) for the full narrative — this file only covers what's needed to work on the code itself.

## Commands

```bash
uv sync                                  # install deps
cp .env.example .env                     # configure provider keys (required - see below)

# Run a paper end to end
uv run python scripts/run.py run ./paper.pdf --run-id my-paper   # or a URL / arXiv id
uv run python scripts/run.py status my-paper
uv run python scripts/run.py approve my-paper   # plan -> code -> done
uv run python scripts/run.py resume my-paper

# Streamlit UI: paper in, tested prototype out
uv run streamlit run scripts/app.py

# Tests
uv run pytest -q
uv run pytest tests/test_workflow.py -q
uv run pytest tests/test_workflow.py::test_workflow_end_to_end -q
```

There is no separate lint/typecheck command configured in `pyproject.toml`. The test suite is network-free (`uv run pytest -q` should always be runnable without API keys, by mocking `Agent` per module — see "Keeping tests network-free" below) and is the primary verification tool — it covers ingestion, retrieval, structured artifacts, budget enforcement, retry bounds, approvals, resume behavior, generated code, and delivery. Running the app itself (`scripts/run.py`/`scripts/app.py`) always calls real models and needs API keys in `.env`; there is no offline/demo mode.

`ModuleNotFoundError: papersmith` means a module was invoked directly instead of via `uv run python scripts/run.py ...` from the repo root (package lives under `src/`, wired via `pythonpath = ["src"]` in `pyproject.toml`).

## Architecture

### Durable, resumable workflow with one human approval gate

`PaperSmithWorkflow` ([src/papersmith/workflows/pipeline.py](src/papersmith/workflows/pipeline.py)) drives everything through explicit states persisted to SQLite (`data/papersmith.db`) after every meaningful transition: `ingesting → comprehending → planning → awaiting_plan_approval → executing → verifying → done` (or `failed`). `resume()` re-enters at the right stage by checking which state fields (`vectorstore_path`, `understanding_path`, `plan_path`, `milestone_statuses`) are already populated — completed artifacts and passed milestones are never redone. `awaiting_plan_approval` is the only way execution pauses for a human (before any code-gen spend); `approve()` is what advances past it. Once every milestone passes, the run goes straight to `done` — there is no separate delivery-approval step, since by then the code is already generated, tested, and verified, and a second gate added a click without adding a check.

`PaperSmithState` ([src/papersmith/memory/state.py](src/papersmith/memory/state.py)) is intentionally tiny — it stores paths, statuses, and cumulative spend, never large content. Read it before assuming any workflow field carries prose or model output directly.

### Artifacts, not chat history, cross stage boundaries

Each stage writes its output to a file under `data/artifacts/<run_id>/` (`understanding.md`, `plan.md` + `plan.json`) rather than passing conversation state forward. The next stage reads from disk. This is deliberate — it's what makes `resume()` possible and keeps prompts bounded. When adding a new stage, follow this pattern: validate with a Pydantic schema, persist to disk, record only the path in `PaperSmithState`.

### Fresh-context coding loop (RALPH-style)

`execute_milestone()` in [src/papersmith/agents/code.py](src/papersmith/agents/code.py) is the one place where retries matter. Each retry constructs a **brand-new** `Agent` — no conversation history carries over. The only mutable context between attempts is what's on disk (`output/<run_id>/*.py`) and the previous `ExecResult` (stdout/stderr/exit code), both re-read from source each iteration. This keeps prompt size flat across retries instead of growing with a ReAct transcript. If you touch this loop, preserve that invariant — don't let it accumulate an `Agent` history or a growing message list across attempts.

### Model routing is centralized, never hardcoded per-agent

[src/papersmith/models/router.py](src/papersmith/models/router.py) exposes three factories — `primary()`, `fast()`, `verifier()` — all backed by `agno.models.litellm.LiteLLM`, not Agno's native provider classes, specifically so a single LiteLLM `Router` can do provider fallback and unified cost accounting. Agents never construct a model string themselves; they call one of these three factories. `verifier()` is expected to be a different model family than `primary()` so verification errors aren't correlated with generation errors — respect that when changing defaults in `.env.example`.

### Structured handoffs via Pydantic + Agno `output_schema`

All agent outputs are typed: `Understanding`, `Plan`/`Milestone`/`AcceptanceTest` ([src/papersmith/schemas/](src/papersmith/schemas/)), `CodeBundle`, and `Verdict`. Agno validates these before they're allowed to reach the next stage. Comprehension gets read-only LanceDB search as its only tool; the coding agent gets no web or shell tool at all — don't widen either without checking [ARCHITECTURE.md](ARCHITECTURE.md)'s guardrails section for why.

### Execution boundary

Generated code/tests only ever run via `run_python()` in [src/papersmith/tools/sandbox.py](src/papersmith/tools/sandbox.py): a subprocess (never in-process `exec`), a hard timeout (`PAPERSMITH_EXEC_TIMEOUT_SECONDS`), a `cwd` restricted to `output/<run_id>/` (enforced by a `commonpath` check against the resolved target), and a minimal explicit env. This is a correctness/containment boundary, not an OS-level sandbox — treat untrusted papers/model output accordingly (see "Operational limitations" in ARCHITECTURE.md).

### Guardrails enforced in the workflow, not the agents

Budget (`PAPERSMITH_MAX_RUN_BUDGET_USD`) is checked via `check_budget()`/`track_run()`/`current_spend()` ([src/papersmith/models/budget.py](src/papersmith/models/budget.py)) before and after every model-backed stage, accumulated from Agno response metrics into `state.spend_usd`. Retry bounds (`PAPERSMITH_MAX_RETRIES`) live in the coding loop, not the workflow. Run ids and generated filenames are sanitized (`_safe_run_id`/`_safe_name`) before touching the filesystem — reuse those helpers rather than writing new sanitization.

### Keeping tests network-free

There is no demo/offline mode in the workflow itself — `PaperSmithWorkflow` always calls the four real model-backed agent functions (`comprehend`, `make_plan`, `execute_milestone`, `verify_milestone`). Tests stay network-free by monkeypatching `Agent` (and the relevant model factory, e.g. `fast`/`primary`/`verifier`) inside each agent module, exactly where `test_comprehend.py` and `tests/test_workflow.py::_patch_agents` already do it — a fake `Agent.run()` returns a fixed `.content` (an `Understanding`, `Plan`, `CodeBundle`, or `Verdict`), and everything downstream (file writes, Pydantic validation, the real sandboxed test execution in `execute_milestone`/`verify_milestone`) runs for real. If you change the shape of any stage's output, update the real agent and every test's fake payload for that stage together.

### Storage layout

```text
data/papers/<run_id>/         copied raw source (PDF/HTML)
data/vectorstore/<run_id>/    LanceDB table "paper_chunks"
data/artifacts/<run_id>/      understanding.md, plan.md, plan.json
data/papersmith.db            workflow state + cross-run preferences
output/<run_id>/              prototype.py, test_*.py, README.md
```

### Streamlit UI is a thin layer, not a second workflow

[src/papersmith/ui/app.py](src/papersmith/ui/app.py) only calls `PaperSmithWorkflow`'s existing public methods (`start`/`approve`/`resume`, `store.load`) — it holds no workflow logic of its own. Streamlit re-runs the whole module on every interaction; the only client-side state is which `run_id` is active (`st.session_state`), since durable state already lives in `data/papersmith.db`.
