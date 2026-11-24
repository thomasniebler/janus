"""CI job selection by changed paths (scripts/ci_paths.py, JAN-309/JAN-313)."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = ROOT / "scripts" / "ci_paths.py"
_spec = importlib.util.spec_from_file_location("ci_paths", _SCRIPT)
ci_paths = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ci_paths
_spec.loader.exec_module(ci_paths)

B, M, F, E, D = ci_paths.BACKEND, ci_paths.MIGRATIONS, ci_paths.FRONTEND, ci_paths.E2E, ci_paths.DOCS
SG, DA, OA = ci_paths.SEMGREP, ci_paths.DEPENDENCY_AUDIT, ci_paths.OPENAPI


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        pytest.param(["docs/docs/quickstart.md", "TECH_SPEC.md"], {D}, id="docs-only"),
        pytest.param(["docs/zensical.toml"], {D}, id="docs-config"),
        pytest.param(
            [".claude/agents/x.md", ".codex/agents/x.toml", ".agents/skills/s/SKILL.md", "AGENTS.md"],
            set(),
            id="agents-only",
        ),
        pytest.param(
            ["README.md", "CHANGELOG.md", "src/janus/CODEMAP.md", "frontend/src/components/CODEMAP.md"],
            set(),
            id="markdown-only",
        ),
        pytest.param(["src/janus/services/email_service.py"], {B, E, SG, OA}, id="backend-only"),
        pytest.param(["tests/test_auth.py"], {B}, id="backend-tests-only"),
        pytest.param(["frontend/src/pages/Dashboard.tsx"], {F, E, SG}, id="frontend-only"),
        pytest.param(["frontend/e2e/chat.spec.ts"], {E}, id="e2e-specs"),
        pytest.param(["frontend/vite.config.ts"], {F, E}, id="frontend-tooling"),
        pytest.param(["src/janus/models/email.py"], {B, E, SG, M, OA}, id="model"),
        pytest.param(["src/janus/models/__init__.py"], {B, E, SG, M, OA}, id="model-package"),
        pytest.param(["alembic/versions/abc_add_table.py"], {B, M, E}, id="migration"),
        pytest.param(["tests/test_postgres_migrations.py"], {B, M}, id="migration-tests"),
        pytest.param(["uv.lock"], set(ci_paths.ALL_JOBS), id="python-lock"),
        pytest.param(["pyproject.toml"], set(ci_paths.ALL_JOBS), id="pyproject"),
        pytest.param(["Taskfile.yml"], set(ci_paths.ALL_JOBS), id="taskfile"),
        pytest.param([".github/workflows/ci.yml"], set(ci_paths.ALL_JOBS), id="ci-workflow"),
        pytest.param(["scripts/ci_paths.py"], set(ci_paths.ALL_JOBS), id="selection-itself"),
        pytest.param(["frontend/package-lock.json"], {F, E, DA, OA}, id="frontend-deps"),
        pytest.param(["frontend/src/generated/api.d.ts"], {F, E, SG, OA}, id="generated-api-types"),
        pytest.param(["frontend/src/generated/openapi.json"], {F, E, SG, OA}, id="generated-openapi-schema"),
        pytest.param(["scripts/export_openapi.py"], {B, SG, OA}, id="openapi-export"),
        pytest.param(["package-lock.json"], {DA}, id="root-npm-deps"),
        pytest.param(["modules/todo-chat/frontend/package-lock.json"], {DA, B}, id="module-deps"),
        pytest.param([".semgrep/janus.yml"], {SG, B}, id="semgrep-rules"),
        pytest.param([".github/workflows/semgrep.yml"], {SG, B}, id="semgrep-workflow"),
        pytest.param([".github/workflows/deploy-uberspace.yml"], {B}, id="deploy-workflow"),
        pytest.param(["scripts/release_validation.py"], {B, SG}, id="script"),
        pytest.param(["frontend/src/lib/domainChanges.ts"], {F, E, SG, B}, id="file-read-by-backend-test"),
        pytest.param(["some/new/thing.py"], set(ci_paths.ALL_JOBS), id="unknown-path"),
        pytest.param(["docs/docs/index.md", "unknown.cfg"], set(ci_paths.ALL_JOBS), id="unknown-wins-over-docs"),
    ],
)
def test_selection_matrix(paths, expected):
    """Each path category selects exactly the jobs that can be affected by it."""
    assert set(ci_paths.select(paths).jobs) == expected


def test_unmapped_paths_are_reported():
    """An unmapped path is named in the selection, so the summary can say why everything ran."""
    selection = ci_paths.select(["docs/docs/index.md", "brand-new.cfg"])
    assert selection.unmapped == ["brand-new.cfg"]
    assert "brand-new.cfg" in ci_paths.summary_markdown(selection, 2)


def test_every_tracked_file_is_mapped():
    """No file in the repository falls through to the select-everything default."""
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    assert [path for path in tracked if ci_paths.rule_for(path) is None] == []


def test_glob_semantics():
    """``*`` stays within a segment; ``**/`` also matches zero directories."""
    assert ci_paths.Rule("*.md", frozenset(), "").matches("README.md")
    assert not ci_paths.Rule("*.md", frozenset(), "").matches("docs/README.md")
    assert ci_paths.Rule("frontend/**/*.md", frozenset(), "").matches("frontend/README.md")
    assert ci_paths.Rule("frontend/**/*.md", frozenset(), "").matches("frontend/src/a/CODEMAP.md")
    assert not ci_paths.Rule("src/janus/models.py", frozenset(), "").matches("src/janus/models.pyc")


def test_docs_reading_tests_are_listed():
    """A backend test that reads the real docs tree must run in the docs job too."""
    reads_docs = re.compile(r"zensical\.toml|\"docs\"\s*/\s*\"docs\"|DOCS_DIR|TECH_SPEC\.md")
    readers = {
        f"tests/{path.name}"
        for path in (ROOT / "tests").glob("test_*.py")
        if path.name != "test_ci_paths.py" and reads_docs.search(path.read_text(encoding="utf-8"))
    }
    assert readers <= set(ci_paths.DOCS_TESTS)


def test_frontend_files_read_by_backend_tests_select_backend():
    """A backend test that reads a frontend file runs when that file changes."""
    # Only tracked files can change in a PR; a local, git-ignored build (frontend/dist) would
    # otherwise make this fail on a developer machine but never in CI.
    tracked = set(
        subprocess.run(
            ["git", "ls-files", "frontend"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.splitlines()
    )
    for path in (ROOT / "tests").glob("test_*.py"):
        for match in re.finditer(r'"frontend"((?:\s*/\s*"[^"]+")+)', path.read_text(encoding="utf-8")):
            parts = re.findall(r'"([^"]+)"', match.group(1))
            target = "/".join(["frontend", *parts])
            if target in tracked:
                assert B in ci_paths.jobs_for(target), f"{target} is read by {path.name}"


def test_nightly_run_selects_every_ci_job_but_not_the_security_scans():
    """Main keeps complete validation; the scans keep their own schedule there."""
    selection = ci_paths.full_selection("schedule event")
    assert set(selection.jobs) == set(ci_paths.CI_JOBS)
    assert "semgrep=false" in ci_paths.output_lines(selection)
    assert "full=true" in ci_paths.output_lines(selection)


def test_draft_defers_the_expensive_jobs_and_turns_the_test_steps_off():
    """A draft keeps the cheap jobs, defers migrations/e2e/semgrep, and sets full=false."""
    selection = ci_paths.for_draft(ci_paths.select(["src/janus/main.py", "frontend/src/App.tsx"]))
    assert ci_paths.DRAFT_SKIPPED
    assert selection.jobs.isdisjoint(ci_paths.DRAFT_SKIPPED)
    assert selection.jobs == {B, ci_paths.FRONTEND, ci_paths.OPENAPI}
    assert selection.deferred == {ci_paths.E2E, ci_paths.SEMGREP}
    assert "full=false" in ci_paths.output_lines(selection)
    summary = ci_paths.summary_markdown(selection, 2)
    assert "| e2e | skipped, draft (runs when ready for review) |" in summary
    assert "marked ready for review" in summary


def _pages(files, changed_files=None, page_size=ci_paths.PER_PAGE):
    """A fake GitHub API serving ``files`` in pages; returns the getter and the URLs it saw."""
    seen: list[str] = []

    def get_json(url, token):
        seen.append(url)
        if url.endswith("/pulls/7"):
            return {"changed_files": len(files) if changed_files is None else changed_files}
        page = int(re.search(r"page=(\d+)$", url).group(1))
        return files[(page - 1) * page_size : page * page_size]

    return get_json, seen


def test_fetch_paginates_and_counts_renames_and_deletes():
    """All pages are read; a rename contributes both names, a delete its old path."""
    files = [{"filename": f"docs/docs/p{i}.md", "status": "modified"} for i in range(150)]
    files.append({"filename": "src/janus/new.py", "status": "renamed", "previous_filename": "src/janus/old.py"})
    files.append({"filename": "frontend/src/gone.tsx", "status": "removed"})
    get_json, seen = _pages(files)

    _, paths = ci_paths.fetch_pr("o/r", 7, "t", get_json=get_json)

    assert len(seen) == 3  # the pull request, then two pages
    assert {"src/janus/new.py", "src/janus/old.py", "frontend/src/gone.tsx"} <= set(paths)
    assert set(ci_paths.select(paths).jobs) == {D, B, E, SG, F, OA}


def test_fetch_fails_closed_on_a_short_listing():
    """Fewer listed files than the pull request reports is an incomplete listing."""
    get_json, _ = _pages([{"filename": "README.md"}], changed_files=2)
    with pytest.raises(ci_paths.DetectionError, match="listed 1"):
        ci_paths.fetch_pr("o/r", 7, "t", get_json=get_json)


def test_fetch_fails_closed_at_the_api_limit():
    """The files endpoint stops at 3000 files, so a PR that large cannot be listed."""
    get_json, _ = _pages([], changed_files=ci_paths.GITHUB_FILES_LIMIT)
    with pytest.raises(ci_paths.DetectionError, match="limit"):
        ci_paths.fetch_pr("o/r", 7, "t", get_json=get_json)


def test_fetch_fails_closed_on_a_malformed_response():
    """A response without ``changed_files`` (an error body, say) fails detection."""
    with pytest.raises(ci_paths.DetectionError):
        ci_paths.fetch_pr("o/r", 7, "t", get_json=lambda url, token: {"message": "Not Found"})


def test_select_command_fails_without_pr_context(tmp_path, monkeypatch):
    """On a pull request, missing context fails the job instead of selecting nothing."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    output = tmp_path / "out"
    assert ci_paths.main(["select", "--event", "pull_request", "--github-output", str(output)]) == 1
    assert not output.exists()


def test_select_command_fails_closed_when_detection_fails(tmp_path, monkeypatch):
    """A detection error exits non-zero and writes no outputs, so no job is selected silently."""

    def broken(*args, **kwargs):
        raise ci_paths.DetectionError("boom")

    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(ci_paths, "fetch_pr", broken)
    output = tmp_path / "out"
    code = ci_paths.main(
        ["select", "--event", "pull_request", "--pr", "7", "--repo", "o/r", "--github-output", str(output)]
    )
    assert code == 1
    assert not output.exists()


def test_select_command_writes_outputs_and_summary(tmp_path, monkeypatch):
    """A docs-only pull request selects only the docs job and says why the rest skip."""
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(ci_paths, "fetch_pr", lambda repo, pr, token: ({}, ["docs/docs/index.md"]))
    output, summary = tmp_path / "out", tmp_path / "summary"

    code = ci_paths.main(
        [
            "select",
            "--event",
            "pull_request",
            "--pr",
            "7",
            "--repo",
            "o/r",
            "--github-output",
            str(output),
            "--summary",
            str(summary),
        ]
    )

    assert code == 0
    assert output.read_text().split() == [
        "backend=false",
        "migrations=false",
        "frontend=false",
        "e2e=false",
        "docs=true",
        "openapi=false",
        "semgrep=false",
        "dependency_audit=false",
        "full=true",
    ]
    assert "| backend | skipped, not affected |" in summary.read_text()


def test_select_command_on_a_draft_defers_the_test_suites(tmp_path, monkeypatch):
    """`--draft true` keeps the cheap selection and turns the test steps off."""
    monkeypatch.setenv("GH_TOKEN", "t")
    monkeypatch.setattr(ci_paths, "fetch_pr", lambda repo, pr, token: ({}, ["src/janus/main.py"]))
    monkeypatch.setattr(ci_paths, "fetch_refinements", lambda repo, pull, paths, token: {})
    output = tmp_path / "out"
    args = ["select", "--event", "pull_request", "--pr", "7", "--repo", "o/r", "--draft", "true"]
    assert ci_paths.main([*args, "--github-output", str(output)]) == 0
    lines = output.read_text().split()
    assert "backend=true" in lines
    assert "full=false" in lines
    assert all(f"{job}=false" in lines for job in ci_paths.DRAFT_SKIPPED)


def test_select_command_on_schedule_selects_everything(tmp_path):
    """The nightly run needs no API access and selects every CI job."""
    output = tmp_path / "out"
    assert ci_paths.main(["select", "--event", "schedule", "--github-output", str(output)]) == 0
    assert "backend=true" in output.read_text()
    assert "e2e=true" in output.read_text()


def test_every_semgrep_target_file_selects_the_scan():
    """Every tracked source file Semgrep scans selects the Semgrep job when it changes."""
    semgrep_spec = importlib.util.spec_from_file_location("semgrep_scan_targets", ROOT / "scripts" / "semgrep_scan.py")
    semgrep_scan = importlib.util.module_from_spec(semgrep_spec)
    sys.modules[semgrep_spec.name] = semgrep_scan  # its dataclasses resolve their module
    semgrep_spec.loader.exec_module(semgrep_scan)
    tracked = subprocess.run(
        ["git", "ls-files", *semgrep_scan.TARGETS], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert tracked
    assert [p for p in tracked if not p.endswith(".md") and SG not in ci_paths.jobs_for(p)] == []


PYPROJECT = """
[project]
name = "janus"
version = "1.6.0"
dependencies = ["fastapi>=0.1"]

[dependency-groups]
dev = ["pytest"]

[tool.mypy]
files = ["src/janus/a.py"]

[tool.janus.semgrep]
exceptions = []
"""


def _edit(text, old, new):
    assert old in text
    return text.replace(old, new)


def test_pyproject_tool_change_selects_only_its_readers():
    """A mypy scope change runs the backend job only; a Semgrep exception change adds the scan."""
    head = _edit(PYPROJECT, '"src/janus/a.py"', '"src/janus/a.py", "src/janus/b.py"')
    assert ci_paths.refine_toml("pyproject.toml", PYPROJECT, head) == {B}
    head = _edit(PYPROJECT, "exceptions = []", 'exceptions = [{rule = "r"}]')
    assert ci_paths.refine_toml("pyproject.toml", PYPROJECT, head) == {SG, B}


def test_pyproject_version_bump_selects_backend():
    """A release bumps only the version, which ``uv lock --check`` in the backend job reads."""
    assert ci_paths.refine_toml("pyproject.toml", PYPROJECT, _edit(PYPROJECT, '"1.6.0"', '"1.7.0"')) == {B}


def test_pyproject_dependency_or_unknown_change_keeps_full_selection():
    """Dependencies, dependency groups, new tables and unparsable files keep every job."""
    assert ci_paths.refine_toml("pyproject.toml", PYPROJECT, _edit(PYPROJECT, "fastapi>=0.1", "fastapi>=0.2")) is None
    assert ci_paths.refine_toml("pyproject.toml", PYPROJECT, _edit(PYPROJECT, '["pytest"]', '["pytest", "x"]')) is None
    assert ci_paths.refine_toml("pyproject.toml", PYPROJECT, PYPROJECT + "\n[tool.newtool]\nx = 1\n") is None
    assert ci_paths.refine_toml("pyproject.toml", PYPROJECT, "not = [toml") is None
    assert ci_paths.refine_toml("pyproject.toml", None, PYPROJECT) is None


LOCK = """
version = 1

[[package]]
name = "janus"
version = "1.6.0"
source = { editable = "." }

[[package]]
name = "fastapi"
version = "0.1"
"""


def test_uv_lock_project_version_only_selects_backend():
    """A lock change limited to Janus's own version is a release bump; any other change is full."""
    assert ci_paths.refine_toml("uv.lock", LOCK, _edit(LOCK, '"1.6.0"', '"1.7.0"')) == {B}
    assert ci_paths.refine_toml("uv.lock", LOCK, _edit(LOCK, 'version = "0.1"', 'version = "0.2"')) is None


def test_fetch_refinements_compares_merge_base_with_head():
    """Both sides are read at the merge base and the PR head; a failed read keeps the rule."""
    pull = {"head": {"sha": "HEAD"}, "base": {"sha": "BASE"}}
    texts = {
        "pyproject.toml@MB": PYPROJECT,
        "pyproject.toml@HEAD": _edit(PYPROJECT, '"1.6.0"', '"1.7.0"'),
        "uv.lock@HEAD": LOCK,  # base side unreadable
    }

    def get_json(url, token):
        assert url.endswith("/compare/BASE...HEAD")
        return {"merge_base_commit": {"sha": "MB"}}

    def get_text(url, token):
        path, ref = re.search(r"/contents/(.+)\?ref=(\w+)$", url).groups()
        return texts.get(f"{path}@{ref}")

    refined = ci_paths.fetch_refinements(
        "o/r", pull, ["pyproject.toml", "uv.lock", "README.md"], "t", get_json, get_text
    )

    assert refined == {"pyproject.toml": {B}}
    selection = ci_paths.select(["pyproject.toml", "uv.lock"], refined)
    assert set(selection.jobs) == set(ci_paths.ALL_JOBS)  # uv.lock could not be narrowed
    assert set(ci_paths.select(["pyproject.toml", "README.md"], refined).jobs) == {B}


def test_fetch_refinements_without_merge_base_keeps_rules():
    """A failed compare lookup narrows nothing."""

    def get_json(url, token):
        raise ci_paths.DetectionError("down")

    pull = {"head": {"sha": "H"}, "base": {"sha": "B"}}
    assert ci_paths.fetch_refinements("o/r", pull, ["pyproject.toml"], "t", get_json, lambda *a: PYPROJECT) == {}


def test_real_pyproject_tables_are_all_classified_or_full():
    """Every table in the real pyproject.toml is either narrowed on purpose or selects everything."""
    import tomllib

    data = tomllib.loads((ROOT / "pyproject.toml").read_text())
    narrowed = {key for key in ci_paths.PYPROJECT_TABLES if key.startswith("tool.")}
    tool_tables = {f"tool.{name}" for name in data["tool"] if name != "janus"} | {
        f"tool.janus.{n}" for n in data["tool"]["janus"]
    }
    # tool.uv (dependency sources) deliberately keeps the full selection.
    assert tool_tables - narrowed == {"tool.uv"}


def test_migration_selection_always_includes_the_backend_suite():
    """The SQLite migration tests run only in backend-quality, so migrations never run without it."""
    for rule in ci_paths.RULES:
        if M in rule.jobs:
            assert B in rule.jobs, rule.pattern
    for jobs in ci_paths.PYPROJECT_TABLES.values():
        if M in jobs:
            assert B in jobs


def test_files_read_by_backend_tests_select_backend():
    """Workflows are checked by tests/test_workflows.py, so changing one runs the backend suite."""
    for path in (ROOT / ".github" / "workflows").glob("*.yml"):
        assert B in ci_paths.jobs_for(f".github/workflows/{path.name}"), path.name
