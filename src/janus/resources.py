"""Locate the files Janus ships next to its code: the Alembic migrations, the frontend and the docs.

A wheel install carries them inside the package under ``janus/_data`` (copied in by
``hatch_build.py``). A git checkout keeps them at the repository root, which is the fallback.
"""

from pathlib import Path

PACKAGE_DATA_DIR = Path(__file__).resolve().parent / "_data"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _first_existing(*candidates: Path) -> Path:
    """Return the first candidate that exists, else the last one (the checkout location)."""
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[-1]


def alembic_ini_path() -> Path:
    """Path to ``alembic.ini``; its ``script_location`` is the ``alembic`` directory beside it."""
    return _first_existing(PACKAGE_DATA_DIR / "alembic.ini", REPO_ROOT / "alembic.ini")


def frontend_dist_dir() -> Path:
    """Directory holding the built frontend (``index.html`` and ``assets/``)."""
    return _first_existing(PACKAGE_DATA_DIR / "frontend", REPO_ROOT / "frontend" / "dist")


def docs_root() -> Path:
    """Directory holding ``docs/docs``, ``docs/zensical.toml``, ``TECH_SPEC.md`` and ``CHANGELOG.md``."""
    return _first_existing(PACKAGE_DATA_DIR / "docs_root", REPO_ROOT)


def is_packaged_install() -> bool:
    """True when Janus runs from an installed wheel rather than a source checkout."""
    return PACKAGE_DATA_DIR.is_dir()
