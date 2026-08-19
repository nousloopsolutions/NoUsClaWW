"""Pytest configuration and shared fixtures for NoUsClaWW tests.

SYNTH:
    purpose: Configure pytest path resolution and provide shared fixtures
             for the test suite. Ensures src/ and red_queen_sentry/ are
             importable from any test.
    axioms: [open_process, scientific_method]
    objective: Every test can import every module without path issues.
    anti_patterns:
        - Hardcoding paths that break on different machines.
        - Fixtures that mask test failures by providing overly permissive setup.
"""
import sys
from pathlib import Path
import sys
from pathlib import Path

# Add src to Python path so we can import nousclaww and sovereign_sockets
src_path = Path(__file__).parent.parent / "src"
if str(src_path) not in sys.path:
    sys.path.insert(0, str(src_path))


import pytest


@pytest.fixture
def tmp_workspace(tmp_path):
    """Provide a temporary workspace directory for tests."""
    workspace = tmp_path / "test_workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def tmp_db(tmp_path):
    """Provide a temporary SQLite database path."""
    return str(tmp_path / "test.db")
