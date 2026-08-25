"""Tests for the nousclaww.sidecar.cli module.

Covers the package entry point declared in pyproject.toml
(``nousclaww = "nousclaww.sidecar.cli:main"``) and the documented
``python -m nousclaww.sidecar.cli check-dag`` command (AGENTS.md Section 7).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_cli(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run the CLI as a subprocess the way a user would."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "nousclaww.sidecar.cli", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd or REPO_ROOT),
        env=env,
        timeout=60,
    )


class TestEntryPoint:
    """The declared console entry point must be importable."""

    def test_cli_module_importable(self):
        import nousclaww.sidecar.cli as cli  # noqa: F401

    def test_main_function_exists(self):
        from nousclaww.sidecar.cli import main

        assert callable(main)


class TestCheckDag:
    """check-dag validates the open-core import rules."""

    def test_check_dag_passes_on_clean_repo(self):
        result = _run_cli("check-dag")
        assert result.returncode == 0, f"stdout={result.stdout} stderr={result.stderr}"
        assert "OK" in result.stdout

    def test_check_dag_detects_violation(self, tmp_path):
        pkg = tmp_path / "src" / "community"
        pkg.mkdir(parents=True)
        bad = pkg / "bad_module.py"
        bad.write_text(
            "def broken():\n"
            "    from proprietary_core import secret_engine\n"
            "    return secret_engine\n",
            encoding="utf-8",
        )
        result = _run_cli("check-dag", "--root", str(tmp_path))
        assert result.returncode == 1
        assert "DAG VIOLATION" in result.stdout or "DAG VIOLATION" in result.stderr


class TestDoctor:
    """doctor reports subsystem availability without starting a server."""

    def test_doctor_exits_zero_and_reports(self):
        result = _run_cli("doctor")
        assert result.returncode == 0, f"stderr={result.stderr}"
        assert "healthy" in result.stdout.lower()


class TestHelp:
    def test_help_exits_zero(self):
        result = _run_cli("--help")
        assert result.returncode == 0
        assert "check-dag" in result.stdout
        assert "serve" in result.stdout
