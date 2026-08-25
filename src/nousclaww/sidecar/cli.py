"""Command-line interface for NoUsClaWW.

This is the console entry point declared in pyproject.toml::

    [project.scripts]
    nousclaww = "nousclaww.sidecar.cli:main"

Commands:
    serve       Start the loopback-only sidecar HTTP server (default).
    check-dag   Validate the open-core import rules (no community ->
                proprietary_core imports). See AGENTS.md Section 5.
    doctor      Instantiate the sidecar subsystems and report health.

Usage (AGENTS.md Section 7)::

    nousclaww check-dag
    python -m nousclaww.sidecar.cli serve

SYNTH:
    purpose: Console entry point wiring the sidecar server, DAG validation, and health diagnostics behind one command.
    axioms: [local_first, open_process, epistemic_boundary, honest_failure_over_fake_success]
    objective: `nousclaww` on PATH starts the loopback sidecar; `check-dag`
        exits non-zero on any cross-zone import violation; `doctor` reports
        subsystem availability honestly without starting a server.
    anti_patterns:
        - Never bind anything except 127.0.0.1
        - Never report a clean DAG when violations exist
        - Never mask subsystem failures in doctor output
        - Never send data anywhere remote

#C CLI contract per AGENTS.md Section 7 and pyproject.toml entry point.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Sequence

# ── DAG validation ────────────────────────────────────────────────────────


def _iter_python_files(root: Path) -> list[Path]:
    """Return all .py files under *root*, skipping caches."""
    return [
        p for p in sorted(root.rglob("*.py"))
        if "__pycache__" not in p.parts and ".scratch" not in p.parts
    ]


def _imports_of(tree: ast.AST) -> list[str]:
    """Extract all imported module names from an AST."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.append(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _is_community_file(path: Path) -> bool:
    """A file belongs to the community zone if any part is 'community'."""
    return "community" in path.parts


def check_dag(root: Path | None = None) -> list[str]:
    """Validate the open-core dependency rules.

    Rules enforced (AGENTS.md Section 5):
        - Community code may NOT import proprietary_core.

    Args:
        root: Repository root to scan. Defaults to the repo this package
            lives in (two parents above ``src/nousclaww/sidecar``).

    Returns:
        A list of violation descriptions (empty means clean).
    """
    if root is None:
        # src/nousclaww/sidecar/cli.py -> repo root is three levels up.
        root = Path(__file__).resolve().parents[3]

    violations: list[str] = []
    for path in _iter_python_files(root):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue  # unparseable files are not import violations
        for name in _imports_of(tree):
            if "proprietary_core" in name.split("."):
                if _is_community_file(path):
                    violations.append(
                        f"DAG VIOLATION: {path}: imports {name}"
                    )
    return violations


# ── Commands ──────────────────────────────────────────────────────────────


def cmd_serve(_args: argparse.Namespace) -> int:
    """Start the loopback-only sidecar server."""
    from nousclaww.sidecar.server import main as server_main

    server_main()
    return 0


def cmd_check_dag(args: argparse.Namespace) -> int:
    """Run the open-core DAG validation."""
    root = Path(args.root).resolve() if args.root else None
    violations = check_dag(root)
    if violations:
        for v in violations:
            print(v)
        print(f"FAIL: {len(violations)} cross-zone import violation(s)")
        return 1
    print("OK: no cross-zone import violations")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Instantiate subsystems and report their availability honestly."""
    import tempfile

    from nousclaww.sidecar.server import SidecarConfig, SidecarService

    tmp = tempfile.mkdtemp(prefix="nousclaww-doctor-")
    config = SidecarConfig(
        db_path=str(Path(tmp) / "doctor_memory.db"),
        event_db_path=str(Path(tmp) / "doctor_events.db"),
        enable_mad_dog_loop=False,
    )
    service = SidecarService(config)
    health = service.health()
    payload = health.data or {}
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        status = payload.get("status", "unknown")
        print(f"NoUsClaWW doctor — status: {status}")
        checks = [
            ("observability", "observability_ready"),
            ("memory", "memory_ready"),
            ("self-reflection", "reflection_ready"),
            ("self-improvement", "improvement_ready"),
            ("epistemic-boundary", "epistemic_boundary_ready"),
            ("desktop-control", "desktop_control_ready"),
            ("mad-dog-loop", "mad_dog_ready"),
            ("egress-policy", None),
        ]
        for label, key in checks:
            if key is None:
                print(f"  [{label}] {payload.get('egress_policy', '?')}")
                continue
            state = "ready" if payload.get(key) else "unavailable"
            print(f"  [{label}] {state}")
    # Doctor exits 0 even with unavailable optional subsystems — that is a
    # diagnosis, not a failure. It exits 1 only if core health reports not-ok.
    return 0 if health.ok else 1


# ── Parser ────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="nousclaww",
        description=(
            "NoUsClaWW — local-first, LLM-agnostic autonomous agent stack "
            "with epistemic boundary."
        ),
    )
    sub = parser.add_subparsers(dest="command")

    p_dag = sub.add_parser(
        "check-dag",
        help="Validate open-core import rules (community must not import "
             "proprietary_core)",
    )
    p_dag.add_argument(
        "--root",
        default=None,
        help="Repository root to scan (default: this installation's repo)",
    )

    p_doc = sub.add_parser(
        "doctor",
        help="Report subsystem availability without starting a server",
    )
    p_doc.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON",
    )

    sub.add_parser(
        "serve",
        help="Start the loopback-only sidecar HTTP server (127.0.0.1)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.

    Args:
        argv: Argument list (defaults to sys.argv[1:]).

    Returns:
        Process exit code.
    """
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    handlers = {
        "serve": cmd_serve,
        "check-dag": cmd_check_dag,
        "doctor": cmd_doctor,
    }
    if args.command is None:
        # Default command: serve (matches the documented quick start).
        return cmd_serve(args)
    return handlers[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
