"""Bounded subprocess execution for generated Python code.

No exec() in-process, ever. Subprocess only, with a timeout and a cwd scoped
to the run's own output directory. See ARCHITECTURE.md 5 (guardrails) and 7.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool


def run_python(code_path: str, cwd: str, timeout: int | None = None) -> ExecResult:
    """Run a Python file in a scoped working directory with a hard timeout."""
    timeout = timeout or int(os.environ.get("PAPERSMITH_EXEC_TIMEOUT_SECONDS", "30"))
    workdir = os.path.realpath(cwd)
    target = os.path.realpath(os.path.join(workdir, code_path))
    if os.path.commonpath([workdir, target]) != workdir:
        raise ValueError("code_path must remain inside cwd")
    if not os.path.isfile(target):
        raise FileNotFoundError(target)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": workdir,
        "PYTHONDONTWRITEBYTECODE": "1",
        "PAPERSMITH_SANDBOX": "1",
    }
    try:
        result = subprocess.run(
            [sys.executable, target],
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return ExecResult(result.returncode, result.stdout, result.stderr, False)
    except subprocess.TimeoutExpired as exc:
        return ExecResult(
            exit_code=124,
            stdout=_decode_timeout_output(exc.stdout),
            stderr=_decode_timeout_output(exc.stderr) or f"Timed out after {timeout}s",
            timed_out=True,
        )


def _decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value
