"""Guards for the import-linter contracts in pyproject.toml (JAN-263)."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "janus"
#: Modules allowed to import the HTTP and WebSocket layers (and the layers themselves).
COMPOSITION_ROOTS = {"janus.main", "janus.app_factory", "janus.cli", "janus.api", "janus.websocket"}
LAYER_CONTRACT = "Only the composition roots wire the HTTP and WebSocket layers"


def _top_level_modules() -> set[str]:
    modules = {f"janus.{path.stem}" for path in PACKAGE.glob("*.py") if path.stem != "__init__"}
    modules |= {f"janus.{path.name}" for path in PACKAGE.iterdir() if (path / "__init__.py").exists()}
    return modules


def test_every_python_package_has_an_init_marker():
    """Namespace packages are invisible to import-linter, so every directory with Python modules needs one."""
    missing = sorted(
        str(directory.relative_to(ROOT))
        for directory in {path.parent for path in PACKAGE.rglob("*.py") if "__pycache__" not in path.parts}
        if not (directory / "__init__.py").exists()
    )
    assert missing == [], "add an __init__.py so import-linter checks these packages"


def test_every_top_level_module_is_covered_by_the_layer_contract():
    """A new module is either listed as a source of the layer contract or is a composition root."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["importlinter"]
    (contract,) = [c for c in config["contracts"] if c["name"] == LAYER_CONTRACT]
    sources = set(contract["source_modules"])

    assert _top_level_modules() - COMPOSITION_ROOTS - sources == set(), "add new modules to the contract's sources"
    assert sources - _top_level_modules() == set(), "remove deleted modules from the contract's sources"
    assert not sources & COMPOSITION_ROOTS
