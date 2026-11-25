"""Hatch build hook: copy the Alembic migrations, the built frontend and the docs into the wheel.

They land under ``janus/_data`` (see ``src/janus/resources.py``), so an installed Janus can
migrate its database, serve the UI and read its documentation without the source tree. The frontend is optional: build
it first (``cd frontend && npm run build``) or the wheel serves the API only, with a warning.
"""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

_SKIP_PARTS = {"__pycache__", ".pytest_cache"}


class CustomBuildHook(BuildHookInterface):
    """Adds ``alembic.ini``, ``alembic/``, ``frontend/dist/`` and the in-app docs to the wheel."""

    def initialize(self, version, build_data):
        """Map each data file into ``janus/_data``; only for a regular (not editable) wheel."""
        if self.target_name != "wheel" or version == "editable":
            return
        root = Path(self.root)
        force_include = build_data["force_include"]
        force_include[str(root / "alembic.ini")] = "janus/_data/alembic.ini"
        # The in-app docs (services/docs_service.py) keep their repository layout under docs_root.
        for name in ("TECH_SPEC.md", "CHANGELOG.md", "docs/zensical.toml"):
            force_include[str(root / name)] = f"janus/_data/docs_root/{name}"
        for source_dir, target_dir in (
            (root / "alembic", "alembic"),
            (root / "frontend" / "dist", "frontend"),
            (root / "docs" / "docs", "docs_root/docs/docs"),
        ):
            if not source_dir.is_dir():
                self.app.display_warning(f"{source_dir} is missing; the wheel will not contain it")
                continue
            for path in sorted(source_dir.rglob("*")):
                relative = path.relative_to(source_dir)
                if path.is_file() and not _SKIP_PARTS.intersection(relative.parts) and path.suffix != ".pyc":
                    force_include[str(path)] = f"janus/_data/{target_dir}/{relative.as_posix()}"
