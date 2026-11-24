"""Export the Janus OpenAPI schema offline, without a server or workers (JAN-139).

The schema is built from the FastAPI app object (``janus.app_factory.create_fastapi_app``)
in-process: no Uvicorn server, no startup hooks (database upgrade, setup token, runtime
trigger worker), no IMAP, planner, or WhatsApp threads, and no network. FastAPI builds the
schema from the routes whether or not ``/openapi.json`` is served, so the export works with
``public_api_docs_enabled = false`` (the production default).

The committed schema, ``frontend/src/generated/openapi.json``, is the input for the frontend
types (``npm run generate:types`` writes ``frontend/src/generated/api.d.ts`` from it).
``task openapi:check`` fails when either file drifts from the backend.

Usage::

    uv run python scripts/export_openapi.py                 # write the committed schema
    uv run python scripts/export_openapi.py --output x.json # write elsewhere
    uv run python scripts/export_openapi.py --check         # exit 1 when the committed schema is stale
"""

from __future__ import annotations

import argparse
import difflib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "frontend" / "src" / "generated" / "openapi.json"
#: Diff lines printed by ``--check`` before the output is cut short.
MAX_DIFF_LINES = 80


def build_schema() -> dict:
    """Return the OpenAPI schema of a freshly created Janus app, without running its startup."""
    from janus.app_factory import create_fastapi_app

    return create_fastapi_app().openapi()


def render(schema: dict) -> str:
    """Serialize ``schema`` stably: route order kept, two-space indent, trailing newline."""
    return json.dumps(schema, indent=2, ensure_ascii=False) + "\n"


def check(expected: str, path: Path) -> list[str]:
    """Return a unified diff between ``path`` and ``expected``; empty when they match."""
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    if current == expected:
        return []
    return list(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=f"{path} (committed)",
            tofile=f"{path} (exported from the backend)",
        )
    )


def _display(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def main(argv: Sequence[str] | None = None) -> int:
    """Write or check the exported schema; return the process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="schema file (default: %(default)s)")
    parser.add_argument("--check", action="store_true", help="exit 1 instead of writing when the file is stale")
    args = parser.parse_args(argv)

    text = render(build_schema())
    if args.check:
        diff = check(text, args.output)
        if not diff:
            print(f"{_display(args.output)} is up to date.")
            return 0
        sys.stdout.writelines(diff[:MAX_DIFF_LINES])
        if len(diff) > MAX_DIFF_LINES:
            print(f"\n... {len(diff) - MAX_DIFF_LINES} more diff lines")
        print(
            f"\n{_display(args.output)} is out of date with the backend routes and schemas. "
            "Run `task openapi:generate` and commit the result.",
            file=sys.stderr,
        )
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8")
    print(f"Wrote {_display(args.output)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
