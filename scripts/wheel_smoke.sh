#!/usr/bin/env bash
# Build the Janus wheel, install it as a uv tool in a throwaway location, and check that it works
# outside the repository: the CLI starts, a fresh database migrates from the packaged migrations,
# `janus update` recognises the tool install, and (with --require-frontend) the UI is inside.
#
#   scripts/wheel_smoke.sh [--require-frontend] [--dist DIR]
#
# --dist DIR checks the wheel already in DIR instead of building one into a temporary directory.
set -euo pipefail

require_frontend=false
dist=""
while [ $# -gt 0 ]; do
  case "$1" in
    --require-frontend) require_frontend=true ;;
    --dist) dist="$2"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

root=$(cd "$(dirname "$0")/.." && pwd)
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

if [ -z "$dist" ]; then
  dist="$work/dist"
  uv build --quiet --out-dir "$dist" "$root"
fi
wheel=$(ls "$dist"/janus_assistant-*-py3-none-any.whl)

export UV_TOOL_DIR="$work/tools" UV_TOOL_BIN_DIR="$work/bin"
export XDG_CONFIG_HOME="$work/config" XDG_DATA_HOME="$work/data" XDG_STATE_HOME="$work/state"
export JANUS_SECRET_KEY=wheel-smoke-test-key
uv tool install --quiet --python 3.12 "$wheel"

cd "$work"
"$work/bin/janus" --help >/dev/null
"$work/tools/janus-assistant/bin/python" - "$require_frontend" <<'PY'
import sys

from janus import database, resources
from janus.services import update_service

assert resources.is_packaged_install(), "janus/_data is missing from the wheel"
assert resources.alembic_ini_path().is_relative_to(resources.PACKAGE_DATA_DIR), resources.alembic_ini_path()
has_frontend = (resources.frontend_dist_dir() / "index.html").is_file()
if sys.argv[1] == "true":
    assert has_frontend, "the wheel has no frontend; build it before the wheel"
assert update_service.install_kind() == update_service.UV_TOOL, update_service.install_kind()
update_service.installed_version()  # reads the janus-assistant distribution metadata
from janus.services import docs_service

assert docs_service.resolve_doc_path("").is_file(), "the in-app docs are missing from the wheel"
assert docs_service.NAV_CONFIG.is_file(), "docs/zensical.toml is missing from the wheel"
database.ensure_schema_up_to_date()
print(f"PASS  wheel installs as a uv tool, migrates a fresh database, frontend={'yes' if has_frontend else 'no'}")
PY
