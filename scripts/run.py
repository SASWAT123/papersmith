"""Source-checkout entry point; installed users can invoke ``papersmith``."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from papersmith.cli import app


if __name__ == "__main__":
    app()
