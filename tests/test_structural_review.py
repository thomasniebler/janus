"""Monthly structural review (JAN-271): window, bundle, review checks, and the review ledger.

Every test runs against a throwaway git repository; the ledger tests push to a local bare remote.
Nothing here calls an LLM, Linear, or the network.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


history = _load("history_branch")
qr = _load("quality_report")
sr = _load("structural_review")

NOW = datetime(2026, 10, 12, 5, 0, tzinfo=UTC)

ROUTER = """
from fastapi import APIRouter

router = APIRouter()


def _helper():
    return 1


@router.get("/items")
def list_items(db):
    return db.query(object).all()


@router.post("/items")
async def create_item(db, service):
    db.add(object())
    db.commit()
    return service.create()
"""


def _git(cwd: Path, *args: str, when: str | None = None) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid"}
    env |= {"GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    if when:
        env |= {"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=env).stdout


def _commit(repo: Path, files: dict[str, str], message: str, when: str) -> str:
    for path, text in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        _git(repo, "add", path)
    _git(repo, "commit", "--quiet", "-m", message, when=when)
    return _git(repo, "rev-parse", "HEAD").strip()


class _Repo(type(Path())):
    """A repository path that also carries its commit shas, oldest first."""

    shas: list[str]


@pytest.fixture
def repo(tmp_path):
    """A clone of a bare remote with three main commits spread over two months."""
    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--quiet", "--bare", "-b", "main", str(remote))
    work = tmp_path / "work"
    _git(tmp_path, "clone", "--quiet", str(remote), str(work))
    shas = [
        _commit(work, {"CODEMAP.md": "root\n", "README": "x\n"}, "init", "2026-08-01T10:00:00Z"),
        _commit(
            work,
            {"src/janus/CODEMAP.md": "janus\n", "src/janus/api/__init__.py": "", "src/janus/api/items.py": ROUTER},
            "feat: items router",
            "2026-09-01T10:00:00Z",
        ),
        _commit(
            work, {"src/janus/api/items.py": ROUTER + "\n# more\n", "docs.md": "d\n"}, "fix: x", "2026-10-10T10:00:00Z"
        ),
    ]
    _git(work, "push", "--quiet", "origin", "HEAD:main")
    clone = _Repo(work)
    clone.shas = shas
    return clone


def _bundle(repo: Path, out: Path, ledger=(), base=None, records=(), error=None) -> dict:
    window = sr.review_window(repo, "HEAD", list(ledger), base)
    return sr.build_bundle(repo, window, None if records is None else list(records), error, out, now=NOW)


def _review(bundle: dict, **overrides) -> dict:
    review = {
        "schema": sr.REVIEW_SCHEMA,
        "reviewed_at": "2026-10-12T06:00:00Z",
        "reviewer": "claude-code",
        "bundle_sha256": sr.digest(bundle),
        "window": {key: bundle["window"][key] for key in sr.WINDOW_KEYS},
        "findings": [
            {
                "id": "SR-1",
                "title": "items router writes to the database itself",
                "category": "router-logic",
                "priority": "medium",
                "path": "src/janus/api/items.py",
                "line": 18,
                "evidence": "create_item calls db.add and db.commit instead of a service",
                "action": "follow-up",
                "reconciliation": {"status": "existing", "issue": "JAN-137"},
            },
            {
                "id": "SR-2",
                "title": "helper in a router",
                "category": "responsibilities",
                "priority": "low",
                "path": "src/janus/api/items.py",
                "line": 7,
                "evidence": "_helper is a top-level helper in a router module",
                "action": "cleanup",
                "reconciliation": {"status": "proposed", "issue": None},
            },
        ],
        "blockers": [],
        "notes": "",
    }
    review.update(overrides)
    return review


# --- window ------------------------------------------------------------------------------------


def test_window_without_a_recorded_review_goes_back_the_given_days(repo):
    """Window without a recorded review goes back the given days."""
    window = sr.review_window(repo, "HEAD", [])
    assert window["base_source"] == "days"
    assert window["base"] == repo.shas[1]  # the last commit at least 31 days before the head
    assert window["head"] == repo.shas[2]
    assert window["base_time"] == "2026-09-01T10:00:00Z"
    assert window["head_time"] == "2026-10-10T10:00:00Z"


def test_window_falls_back_to_the_root_commit(repo):
    """Window falls back to the root commit."""
    window = sr.review_window(repo, "HEAD", [], days=365)
    assert (window["base_source"], window["base"]) == ("root", repo.shas[0])


def test_window_starts_at_the_last_recorded_review(repo):
    """Window starts at the last recorded review."""
    ledger = [
        {"schema": sr.REVIEW_SCHEMA, "window": {"head": repo.shas[0]}},
        {"schema": sr.REVIEW_SCHEMA, "window": {"head": repo.shas[1]}},
        {"schema": "other/1", "window": {"head": repo.shas[2]}},
    ]
    window = sr.review_window(repo, "HEAD", ledger)
    assert (window["base_source"], window["base"]) == ("ledger", repo.shas[1])


def test_window_starts_at_the_furthest_recorded_descendant_whatever_the_ledger_order(repo):
    """Reviews recorded out of order never move the window start backwards."""
    ledger = [
        {"schema": sr.REVIEW_SCHEMA, "window": {"head": repo.shas[1]}},
        {"schema": sr.REVIEW_SCHEMA, "window": {"head": repo.shas[0]}},
    ]
    window = sr.review_window(repo, "HEAD", ledger)
    assert (window["base_source"], window["base"]) == ("ledger", repo.shas[1])


def test_window_ignores_a_recorded_head_that_is_not_an_ancestor(repo):
    """Window ignores a recorded head that is not an ancestor."""
    _git(repo, "checkout", "--quiet", "-b", "side", repo.shas[1])
    side = _commit(repo, {"side": "s\n"}, "side", "2026-10-11T10:00:00Z")
    _git(repo, "checkout", "--quiet", "-")
    window = sr.review_window(repo, "HEAD", [{"schema": sr.REVIEW_SCHEMA, "window": {"head": side}}])
    assert window["base_source"] == "days"


def test_explicit_base_is_checked(repo):
    """Explicit base is checked."""
    assert sr.review_window(repo, "HEAD", [], repo.shas[0][:12])["base_source"] == "explicit"
    with pytest.raises(ValueError, match="commit sha"):
        sr.review_window(repo, "HEAD", [], "main; rm -rf /")
    with pytest.raises(ValueError, match="not an ancestor"):
        sr.review_window(repo, repo.shas[1], [], repo.shas[2])


# --- bundle ------------------------------------------------------------------------------------


def test_bundle_degrades_without_a_quality_report(repo, tmp_path):
    """No quality-history yet: the bundle is still written and says the report is unavailable."""
    out = tmp_path / "bundle"
    bundle = _bundle(repo, out)
    assert bundle["schema"] == sr.BUNDLE_SCHEMA
    assert bundle["quality"]["status"] == "unavailable"
    assert (out / "quality-reports.jsonl").read_text() == ""
    for name in ("bundle.json", "diff.patch", "prompt.md", "summary.md"):
        assert (out / name).is_file()
    assert json.loads((out / "bundle.json").read_text()) == bundle
    summary = (out / "summary.md").read_text()
    assert "Agent review: blocked in CI" in summary
    assert "unavailable" in summary


def test_bundle_marks_an_unreadable_quality_history_as_error(repo, tmp_path):
    """Bundle marks an unreadable quality history as error."""
    bundle = _bundle(repo, tmp_path / "b", records=None, error="could not read the quality history: OSError")
    assert bundle["quality"] == {
        "status": "error",
        "detail": "could not read the quality history: OSError",
        "reports": [],
        "path": "quality-reports.jsonl",
    }
    assert "no data" in (tmp_path / "b" / "prompt.md").read_text()


def test_bundle_carries_the_newest_main_quality_reports(repo, tmp_path):
    """Bundle carries the newest main quality reports."""
    reports = [
        {
            "schema": qr.SCHEMA,
            "ref": ref,
            "generated_at": f"2026-10-0{n}T06:37:00Z",
            "commit": str(n) * 40,
            "checks": {"tests": {"status": "pass"}, "deployment": {"status": "unavailable"}},
            "regressions": ["types.errors"] if n == 3 else [],
            "hotspots": [{"path": "src/janus/api/items.py", "score": 1.2, "worst_function": "create_item:17"}],
        }
        for n, ref in ((1, qr.MAIN_REF), (2, "refs/heads/feature"), (3, qr.MAIN_REF))
    ]
    out = tmp_path / "b"
    bundle = _bundle(repo, out, records=reports)
    assert bundle["quality"]["status"] == "available"
    assert [r["commit"] for r in bundle["quality"]["reports"]] == ["1" * 40, "3" * 40]
    assert len(history.read_jsonl((out / "quality-reports.jsonl").read_text())) == 2
    summary = sr.render_metrics(bundle)
    assert "regressions: types.errors" in summary
    assert "deployment (unavailable)" in summary
    assert "`create_item:17`" in summary


def test_bundle_measures_the_diff_codemaps_and_routers(repo, tmp_path):
    """Bundle measures the diff codemaps and routers."""
    bundle = _bundle(repo, tmp_path / "b")
    diff = bundle["diff"]
    assert [c["subject"] for c in diff["commits"]] == ["fix: x"]
    assert {f["path"]: f["codemap"] for f in diff["files"]} == {
        "docs.md": "CODEMAP.md",
        "src/janus/api/items.py": "src/janus/CODEMAP.md",
    }
    assert diff["by_area"]["src/janus/CODEMAP.md"] == {"files": 1, "insertions": 2, "deletions": 0}
    assert [m["path"] for m in bundle["codemaps"]] == ["CODEMAP.md", "src/janus/CODEMAP.md"]
    assert not any(m["changed_in_window"] for m in bundle["codemaps"])
    assert bundle["routers"]["modules"] == [
        {
            "path": "src/janus/api/items.py",
            "changed_in_window": True,
            "lines": 13,
            "routes": 2,
            "helpers": 1,
            "data_access_calls": 3,
        }
    ]
    assert bundle["routers"]["totals"]["modules_with_data_access"] == 1


def test_router_metrics_count_only_database_calls_in_handlers():
    """Router metrics count only database calls in handlers."""
    source = (
        "def helper(db):\n    db.commit()\n\n@app.websocket('/ws')\nasync def ws(session):\n    session.execute(1)\n"
    )
    assert sr.router_metrics(source) == {"lines": 5, "routes": 1, "helpers": 1, "data_access_calls": 1}


def test_numstat_keeps_new_paths_of_renames_and_flags_binaries():
    """Numstat keeps new paths of renames and flags binaries."""
    text = "3\t1\tsrc/{old => new}/a.py\n-\t-\timg.png\n2\t0\tx.py => y.py\n"
    files = sr.parse_numstat(text, ["CODEMAP.md", "src/CODEMAP.md"])
    assert [(f["path"], f["codemap"], f["binary"]) for f in files] == [
        ("src/new/a.py", "src/CODEMAP.md", False),
        ("img.png", "CODEMAP.md", True),
        ("y.py", "CODEMAP.md", False),
    ]


def test_patch_is_cut_at_a_line_end(repo, tmp_path):
    """Patch is cut at a line end."""
    window = sr.review_window(repo, "HEAD", [], repo.shas[0])
    out = tmp_path / "diff.patch"
    meta = sr.write_patch(repo, window, out, limit=200)
    assert meta["truncated"] is True and meta["bytes"] > 200
    assert 0 < len(out.read_bytes()) <= 200 and out.read_bytes().endswith(b"\n")
    bundle = _bundle(repo, tmp_path / "b")
    assert "is truncated" not in sr.render_prompt(bundle)
    bundle["diff"]["patch"] = meta
    assert "is truncated" in sr.render_prompt(bundle)


# --- review checks -----------------------------------------------------------------------------


def test_a_valid_review_passes_and_renders_metrics_apart_from_findings(repo, tmp_path):
    """A valid review passes and renders metrics apart from findings."""
    bundle = _bundle(repo, tmp_path / "b")
    review = _review(bundle)
    assert sr.check_review(review, bundle, repo) == []
    markdown = sr.render_review(review, bundle)
    metrics, findings = markdown.split("## Findings (qualitative")
    assert "automatic metrics" in metrics and "SR-1" not in metrics
    assert findings.index("SR-1") < findings.index("SR-2")  # medium before low
    assert "| JAN-137 |" in findings and "| proposed |" in findings
    assert "### Blockers\n\n- none" in findings


def _broken(**changes):
    def mutate(review):
        for path, value in changes.items():
            target = review
            *parents, last = path.split(".")
            for key in parents:
                target = target[int(key)] if key.isdigit() else target[key]
            if value is KeyError:
                del target[last]
            else:
                target[int(last) if last.isdigit() else last] = value

    return mutate


@pytest.mark.parametrize(
    ("mutate", "problem"),
    [
        (_broken(bundle_sha256="0" * 64), "bundle_sha256"),
        (_broken(**{"window.head": "f" * 40}), "window"),
        (_broken(**{"window.base_time": KeyError}), "window"),
        (_broken(schema="janus.structural-review/0"), "schema"),
        (_broken(reviewer="robot"), "reviewer"),
        (_broken(reviewed_at="yesterday"), "reviewed_at"),
        (_broken(**{"findings.0.path": "src/janus/api/missing.py"}), "is not a file"),
        (_broken(**{"findings.0.path": "src/janus/api"}), "is not a file"),
        (_broken(**{"findings.0.path": "src"}), "is not a file"),
        (_broken(**{"findings.0.path": ""}), "repository-relative file path"),
        (_broken(**{"findings.0.path": "."}), "repository-relative file path"),
        (_broken(**{"findings.0.path": "../etc/passwd"}), "repository-relative file path"),
        (_broken(**{"findings.0.path": "src/../README"}), "repository-relative file path"),
        (_broken(**{"findings.0.line": 999}), "lines at"),
        (_broken(**{"findings.0.line": True}), "positive integer"),
        (_broken(**{"findings.0.priority": "P1"}), "priority"),
        (_broken(**{"findings.0.category": "style"}), "category"),
        (_broken(**{"findings.0.action": "merge"}), "action"),
        (_broken(**{"findings.0.evidence": " "}), "evidence"),
        (_broken(**{"findings.0.extra": 1}), "keys must be exactly"),
        (_broken(**{"findings.1.id": "SR-1"}), "unique"),
        (_broken(**{"findings.0.reconciliation": {"status": "existing", "issue": None}}), "identifier"),
        (_broken(**{"findings.1.reconciliation": {"status": "proposed", "issue": "JAN-1"}}), "only an existing"),
        (_broken(**{"findings.1.reconciliation": {"status": "unchecked", "issue": None}}), "blocker"),
        (_broken(notes="contact me at someone@example.org"), "secret or an e-mail"),
        (_broken(**{"findings.0.evidence": "token ghp_" + "a" * 30}), "secret or an e-mail"),
        (_broken(blockers=[""]), "blockers"),
        (_broken(extra=1), "top level"),
    ],
)
def test_invalid_reviews_are_rejected(repo, tmp_path, mutate, problem):
    """Invalid reviews are rejected."""
    bundle = _bundle(repo, tmp_path / "b")
    review = copy.deepcopy(_review(bundle))
    mutate(review)
    problems = sr.check_review(review, bundle, repo)
    assert problems and any(problem in p for p in problems), problems


def test_unchecked_reconciliation_is_fine_once_reported_as_a_blocker(repo, tmp_path):
    """Unchecked reconciliation is fine once reported as a blocker."""
    bundle = _bundle(repo, tmp_path / "b")
    review = _review(bundle, blockers=["no Linear access in this session: reconciliation unchecked"])
    review["findings"][1]["reconciliation"] = {"status": "unchecked", "issue": None}
    assert sr.check_review(review, bundle, repo) == []
    assert "- no Linear access" in sr.render_review(review, bundle)


def test_check_cli(repo, tmp_path, capsys):
    """Check cli."""
    out = tmp_path / "b"
    bundle = _bundle(repo, out)
    good, bad, md = tmp_path / "good.json", tmp_path / "bad.json", tmp_path / "review.md"
    good.write_text(json.dumps(_review(bundle)))
    bad.write_text(json.dumps(_review(bundle, bundle_sha256="0" * 64)))
    args = ["--bundle", str(out / "bundle.json"), "--repo", str(repo)]
    assert sr.main(["check", str(good), *args, "--markdown", str(md)]) == 0
    assert "SR-1" in md.read_text()
    assert sr.main(["check", str(bad), *args]) == 1
    assert "INVALID bundle_sha256" in capsys.readouterr().err


# --- ledger ------------------------------------------------------------------------------------


def test_bundle_cli_reads_a_missing_history_branch_as_empty(repo, tmp_path, capsys):
    """The scheduled run before any report or review exists: no failure, quality unavailable."""
    out = tmp_path / "b"
    assert sr.main(["bundle", "--out-dir", str(out), "--repo", str(repo)]) == 0
    bundle = json.loads((out / "bundle.json").read_text())
    assert bundle["window"]["base_source"] == "days"
    assert bundle["quality"]["status"] == "unavailable"
    assert sr.digest(bundle) in capsys.readouterr().out


def test_bundle_cli_aborts_when_the_ledger_cannot_be_read(tmp_path, capsys):
    """A broken remote must not silently pick a different window."""
    repo = tmp_path / "lonely"
    repo.mkdir()
    _git(repo, "init", "--quiet", "-b", "main")
    _commit(repo, {"a": "a\n"}, "init", "2026-10-01T10:00:00Z")
    _git(repo, "remote", "add", "origin", str(tmp_path / "missing.git"))
    assert sr.main(["bundle", "--out-dir", str(tmp_path / "b"), "--repo", str(repo)]) == 2
    assert "git ls-remote" in capsys.readouterr().err
    assert not (tmp_path / "b").exists()
    # An explicit base needs no ledger; the quality history is then reported as an error.
    base = _git(repo, "rev-parse", "HEAD").strip()
    assert sr.main(["bundle", "--out-dir", str(tmp_path / "b"), "--repo", str(repo), "--base", base]) == 0
    assert json.loads((tmp_path / "b" / "bundle.json").read_text())["quality"]["status"] == "error"


def test_record_appends_main_reviews_once_and_moves_the_next_window(repo, tmp_path, capsys):
    """Record appends main reviews once and moves the next window."""
    out = tmp_path / "b"
    assert sr.main(["bundle", "--out-dir", str(out), "--repo", str(repo)]) == 0
    bundle = json.loads((out / "bundle.json").read_text())
    path = tmp_path / "review.json"
    path.write_text(json.dumps(_review(bundle)))
    args = [str(path), "--bundle", str(out / "bundle.json"), "--repo", str(repo)]
    assert sr.main(["record", *args]) == 0
    ledger = history.read_history(repo, "origin", qr.HISTORY_BRANCH, sr.LEDGER_PATH)
    assert [r["window"]["head"] for r in ledger] == [repo.shas[2]]
    # The quality report file on the same branch is untouched (there is none).
    assert history.read_history(repo, "origin", qr.HISTORY_BRANCH, qr.HISTORY_PATH) == []

    assert sr.main(["record", *args]) == 1
    assert "already recorded" in capsys.readouterr().err

    head = _commit(repo, {"later": "l\n"}, "feat: later", "2026-11-10T10:00:00Z")
    _git(repo, "push", "--quiet", "origin", "HEAD:main")
    ledger = history.read_history(repo, "origin", qr.HISTORY_BRANCH, sr.LEDGER_PATH)
    window = sr.review_window(repo, "HEAD", ledger)
    assert (window["base_source"], window["base"], window["head"]) == ("ledger", repo.shas[2], head)


def test_record_refuses_a_window_that_is_not_on_main(repo, tmp_path, capsys):
    """Record refuses a window that is not on main."""
    _commit(repo, {"local": "l\n"}, "local only", "2026-10-11T10:00:00Z")
    out = tmp_path / "b"
    bundle = _bundle(repo, out)
    path = tmp_path / "review.json"
    path.write_text(json.dumps(_review(bundle)))
    assert sr.main(["record", str(path), "--bundle", str(out / "bundle.json"), "--repo", str(repo)]) == 1
    assert "is not on main" in capsys.readouterr().err
    assert history.read_history(repo, "origin", qr.HISTORY_BRANCH, sr.LEDGER_PATH) == []


def test_record_refuses_an_invalid_review(repo, tmp_path, capsys):
    """Record refuses an invalid review."""
    out = tmp_path / "b"
    bundle = _bundle(repo, out)
    path = tmp_path / "review.json"
    path.write_text(json.dumps(_review(bundle, notes="mail me: a@b.example.org")))
    assert sr.main(["record", str(path), "--bundle", str(out / "bundle.json"), "--repo", str(repo)]) == 1
    assert "INVALID" in capsys.readouterr().err
    assert history.read_history(repo, "origin", qr.HISTORY_BRANCH, sr.LEDGER_PATH) == []


def test_script_never_calls_an_llm_or_the_network_itself():
    """The review is an agent's job; the script only reads git and local files."""
    source = (_SCRIPTS / "structural_review.py").read_text()
    for forbidden in ("urllib", "requests", "httpx", "anthropic", "openai", "LINEAR_API_KEY", "os.environ"):
        assert forbidden not in source


def test_record_refuses_a_review_older_than_the_last_recorded_one(repo, tmp_path, capsys):
    """A bundle completed out of order cannot be recorded after a newer review."""

    def bundle_for(head, base, name):
        out = tmp_path / name
        window = sr.review_window(repo, head, [], base)
        bundle = sr.build_bundle(repo, window, [], None, out, now=NOW)
        review = _review(bundle)
        for finding in review["findings"]:
            finding["path"], finding["line"] = "README", 1
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(review))
        return [str(path), "--bundle", str(out / "bundle.json"), "--repo", str(repo)]

    older = bundle_for(repo.shas[1], repo.shas[0], "older")
    newer = bundle_for(repo.shas[2], repo.shas[0], "newer")
    assert sr.main(["record", *newer]) == 0
    assert sr.main(["record", *older]) == 1
    assert "out of order" in capsys.readouterr().err
    ledger = history.read_history(repo, "origin", qr.HISTORY_BRANCH, sr.LEDGER_PATH)
    assert [r["window"]["head"] for r in ledger] == [repo.shas[2]]
