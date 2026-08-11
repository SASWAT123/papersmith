"""Streamlit UI: paper in, tested Python prototype out.

Thin UI layer over PaperSmithWorkflow/StateStore. Streamlit re-executes this
module top to bottom on every interaction; the only thing kept in
st.session_state is which run_id is active. All durable state already lives
in SQLite via StateStore, so this file must not duplicate workflow logic.
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from pathlib import Path

import streamlit as st

from papersmith.memory.state import PaperSmithState
from papersmith.workflows.pipeline import PaperSmithWorkflow

_ACTIVE_STATUSES = {"ingesting", "comprehending", "planning", "executing", "verifying"}


def main() -> None:
    st.set_page_config(page_title="PaperSmith", page_icon="🛠️", layout="wide")
    st.title("🛠️ PaperSmith")
    st.caption("Turn a paper into a small, tested Python prototype.")

    run_id = st.session_state.get("run_id")
    if run_id:
        with st.sidebar:
            st.caption(f"Active run: `{run_id}`")
            if st.button("Start a new run"):
                st.session_state.pop("run_id", None)
                st.rerun()
        _render_active_run(run_id)
    else:
        _render_intake_form()


def _render_intake_form() -> None:
    st.subheader("1. Give PaperSmith a paper")
    source_kind = st.radio("Source", ["Paper URL / arXiv ID", "Upload file"], horizontal=True)

    url = ""
    uploaded = None
    if source_kind == "Paper URL / arXiv ID":
        url = st.text_input(
            "URL or arXiv ID", placeholder="https://arxiv.org/abs/1706.03762"
        )
    else:
        uploaded = st.file_uploader("PDF or HTML file", type=["pdf", "html", "htm"])

    run_id_input = st.text_input("Run ID (optional)", placeholder="auto-generated if left blank")

    if st.button("Build prototype", type="primary"):
        try:
            source, cleanup_dir = _resolve_source(source_kind, url, uploaded)
        except ValueError as exc:
            st.error(str(exc))
            return

        run_id = run_id_input.strip() or uuid.uuid4().hex[:10]
        try:
            with st.spinner("Ingesting the paper and building an understanding + plan..."):
                state = PaperSmithWorkflow().start(source, run_id=run_id)
        finally:
            if cleanup_dir:
                shutil.rmtree(cleanup_dir, ignore_errors=True)

        st.session_state["run_id"] = state.run_id
        st.rerun()


def _resolve_source(
    source_kind: str, url: str, uploaded
) -> tuple[str, Path | None]:
    if source_kind == "Paper URL / arXiv ID":
        if not url.strip():
            raise ValueError("Enter a URL or arXiv ID.")
        return url.strip(), None

    if uploaded is None:
        raise ValueError("Upload a PDF or HTML file.")
    tmp_dir = Path(tempfile.mkdtemp(prefix="papersmith-upload-"))
    destination = tmp_dir / uploaded.name
    destination.write_bytes(uploaded.getvalue())
    return str(destination), tmp_dir


def _render_active_run(run_id: str) -> None:
    workflow = PaperSmithWorkflow()
    try:
        state = workflow.store.load(run_id)
    except KeyError:
        st.error(f"Unknown run: {run_id}")
        return

    st.subheader(f"Run `{state.run_id}`")
    st.caption(f"status: **{state.status}**  ·  spend: ${state.spend_usd:.4f}")

    if state.status == "failed":
        st.error(state.error or "Run failed.")
        if st.button("Resume"):
            with st.spinner("Resuming..."):
                workflow.resume(run_id)
            st.rerun()
        return

    if state.status in _ACTIVE_STATUSES:
        st.info("This run is mid-stage (likely interrupted). Resume it to continue.")
        if st.button("Resume"):
            with st.spinner("Resuming..."):
                workflow.resume(run_id)
            st.rerun()
        return

    if state.status == "awaiting_plan_approval":
        _render_plan_approval(workflow, state)
        return

    if state.status == "done":
        _render_delivery(state)
        return


def _render_plan_approval(workflow: PaperSmithWorkflow, state: PaperSmithState) -> None:
    st.subheader("2. Review the plan")
    with st.expander("Understanding", expanded=False):
        st.markdown(Path(state.understanding_path).read_text(encoding="utf-8"))
    st.markdown(Path(state.plan_path).read_text(encoding="utf-8"))

    if st.button("Approve plan & generate code", type="primary"):
        with st.spinner("Generating and verifying code for each milestone... this can take a while."):
            workflow.approve(state.run_id)
        st.rerun()


def _render_delivery(state: PaperSmithState) -> None:
    st.subheader("3. Generated prototype")

    if state.milestone_statuses:
        st.dataframe(
            [{"milestone": name, "status": status} for name, status in state.milestone_statuses.items()],
            hide_index=True,
        )

    output_dir = Path(state.output_dir)
    readme = output_dir / "README.md"
    if readme.is_file():
        with st.expander("README", expanded=False):
            st.markdown(readme.read_text(encoding="utf-8"))

    for path in sorted(output_dir.glob("*.py")):
        if path.name == "playground.py":
            continue
        with st.expander(path.name, expanded=path.name == "prototype.py"):
            st.code(path.read_text(encoding="utf-8"), language="python")

    st.success("Delivered.")
