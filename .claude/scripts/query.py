"""Single CLI dispatcher for Phase 4 integrations.

Usage:
  uv run python .claude/scripts/query.py <integration> <subcmd> [args]

Each integration registers its own subparser. Modules are imported lazily
(inside `_register`) so a missing token for X doesn't break Y.
"""

from __future__ import annotations

import argparse
import importlib
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / ".claude" / "scripts"))

from shared import load_env  # noqa: E402
from integrations.registry import INTEGRATIONS  # noqa: E402


def _register(sub: argparse._SubParsersAction, module_path: str) -> None:
    """Import the integration module lazily and let it register its subparser.

    On ImportError (missing optional dep), register a stub that prints a clear
    error when invoked instead of failing dispatcher startup.
    """
    try:
        mod = importlib.import_module(module_path)
    except ImportError as e:
        name = module_path.rsplit(".", 1)[-1]
        p = sub.add_parser(name, help=f"{name} (import failed: {e})")
        p.set_defaults(
            _handler=lambda a, _e=e, _n=name: (
                print(f"[{_n}] import failed: {_e}", file=sys.stderr) or 2
            )
        )
        return
    if hasattr(mod, "add_subparser"):
        mod.add_subparser(sub)


def main(argv: list[str] | None = None) -> int:
    load_env()

    ap = argparse.ArgumentParser(
        prog="query.py",
        description="BrunOS Phase 4 integration dispatcher.",
    )
    sub = ap.add_subparsers(dest="integration", required=True, metavar="<integration>")
    for spec in INTEGRATIONS:
        _register(sub, spec.module)

    args = ap.parse_args(argv)
    handler = getattr(args, "_handler", None)
    if handler is None:
        ap.print_help(sys.stderr)
        return 2
    try:
        return int(handler(args) or 0)
    except RuntimeError as e:
        print(f"[{args.integration}] {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"[{args.integration}] unexpected: {type(e).__name__}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
