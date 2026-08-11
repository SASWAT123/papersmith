"""Streamlit entry point; run with `uv run streamlit run scripts/app.py`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from papersmith.ui.app import main

main()
