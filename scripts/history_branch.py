"""Append-only JSON Lines history kept on a dedicated git branch.

Workflow artifacts expire (90 days at most), so reports that must outlive them are appended, one
JSON object per line, to a file on a branch that holds nothing else (``ops-metrics`` for deploy
and availability metrics, ``quality-history`` for the quality report). The branch shares no
history with ``main``, is never merged, and only grows: each publish adds lines and never edits or
deletes earlier ones. Export is a plain ``git fetch`` plus ``git show <branch>:<file>``.

The functions run ``git`` in an existing clone (on GitHub Actions, the ``actions/checkout``
workspace and its credentials) without touching its working tree or checked-out branch, and retry
a push that lost a race against another publisher. Standard library only; importable from the
sibling scripts and the tests.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path

BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
PUSH_ATTEMPTS = 3

GitRunner = Callable[..., subprocess.CompletedProcess]


def run_git(
    repo: Path, *args: str, check: bool = True, stdin: str | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess:
    """Run ``git -C repo args`` (fixed argument list, no shell) and capture its output."""
    return subprocess.run(  # noqa: S603 - git with an argument list built in this module
        ["git", "-C", str(repo), *args],  # noqa: S607 - git from PATH, as in every workflow step
        check=check,
        capture_output=True,
        text=True,
        input=stdin,
        env={**os.environ, **env} if env else None,
    )


def read_jsonl(text: str) -> list[dict]:
    """Parse JSON Lines; blank lines are skipped, anything but an object per line is an error."""
    records = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError(f"line {number} is not a JSON object")
        records.append(record)
    return records


def dump_jsonl(records: Iterable[dict]) -> str:
    """Serialize records as JSON Lines with sorted keys, so each line is stable and diffable."""
    return "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in records)


def fetch_branch(repo: Path, remote: str, branch: str, git: GitRunner = run_git) -> str | None:
    """Fetch ``remote/branch``; return its commit, or ``None`` when the branch does not exist yet.

    Only a definite "no such ref" counts as missing: ``ls-remote --exit-code`` exits 2 (or lists
    nothing). Any other failure (network, authentication, a broken remote) raises
    ``CalledProcessError``, so a transient error never reads as an empty history and never resets
    baselines or starts a fresh orphan branch.
    """
    ref = f"refs/heads/{branch}"
    listed = git(repo, "ls-remote", "--exit-code", "--heads", remote, ref, check=False)
    if listed.returncode == 2 or (listed.returncode == 0 and not (listed.stdout or "").strip()):
        return None
    if listed.returncode != 0:
        raise subprocess.CalledProcessError(
            listed.returncode, ["git", "ls-remote", "--exit-code", "--heads", remote, ref], listed.stdout, listed.stderr
        )
    git(repo, "fetch", "--quiet", "--no-tags", remote, f"+refs/heads/{branch}:refs/remotes/{remote}/{branch}")
    return git(repo, "rev-parse", f"refs/remotes/{remote}/{branch}").stdout.strip()


def _file_at(repo: Path, commit: str, path: str, git: GitRunner) -> str:
    shown = git(repo, "show", f"{commit}:{path}", check=False)
    return shown.stdout if shown.returncode == 0 else ""


def read_history(repo: Path, remote: str, branch: str, path: str, git: GitRunner = run_git) -> list[dict]:
    """The records of ``path`` on ``remote/branch``; empty when the branch or file is missing."""
    commit = fetch_branch(repo, remote, branch, git)
    return [] if commit is None else read_jsonl(_file_at(repo, commit, path, git))


def append_and_push(
    repo: Path,
    remote: str,
    branch: str,
    path: str,
    records: list[dict] | Callable[[list[dict]], list[dict]],
    message: str,
    git: GitRunner = run_git,
    attempts: int = PUSH_ATTEMPTS,
) -> str:
    """Append ``records`` to ``path`` on ``remote/branch`` and push; return the new commit.

    The commit is built with plumbing in a throwaway index, so the clone's working tree and
    checked-out branch stay untouched. The branch starts as an orphan on the first publish. A
    rejected push (another run published first) is retried from the new tip up to ``attempts``
    times; nothing is ever forced.

    ``records`` may be a function of the records already on the branch tip; it is called again
    for every attempt, so records derived from the history (such as the quality report's delta and
    baselines) are always computed against the tip they are appended to.
    """
    if not callable(records) and not records:
        raise ValueError("nothing to publish")
    identity = {
        "GIT_AUTHOR_NAME": BOT_NAME,
        "GIT_AUTHOR_EMAIL": BOT_EMAIL,
        "GIT_COMMITTER_NAME": BOT_NAME,
        "GIT_COMMITTER_EMAIL": BOT_EMAIL,
    }
    last_error = ""
    for _ in range(attempts):
        base = fetch_branch(repo, remote, branch, git)
        existing = _file_at(repo, base, path, git) if base is not None else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        batch = records(read_jsonl(existing)) if callable(records) else records
        if not batch:
            raise ValueError("nothing to publish")
        blob = git(repo, "hash-object", "-w", "--stdin", stdin=existing + dump_jsonl(batch)).stdout.strip()
        with tempfile.TemporaryDirectory(prefix="history-index-") as tmp:
            index = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
            if base is not None:
                git(repo, "read-tree", base, env=index)
            git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=index)
            tree = git(repo, "write-tree", env=index).stdout.strip()
        parents = ["-p", base] if base is not None else []
        commit = git(repo, "commit-tree", tree, *parents, "-m", message, env=identity).stdout.strip()
        pushed = git(repo, "push", "--quiet", remote, f"{commit}:refs/heads/{branch}", check=False)
        if pushed.returncode == 0:
            return commit
        last_error = pushed.stderr.strip()
    raise RuntimeError(f"push to {remote}/{branch} failed after {attempts} attempts: {last_error}")
