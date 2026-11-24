"""Reads the release notes release-please writes to ``CHANGELOG.md``.

The "what is new" flash in the UI shows the releases a user has not seen yet. Each release in the
file is a ``## [X.Y.Z](compare-url) (date)`` heading followed by ``### Group`` headings (Features,
Bug Fixes, ...) with one bullet per change. Bullets are reduced to plain text: the commit and
issue links release-please appends are dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from janus.config import settings

CHANGELOG_PATH = Path(__file__).resolve().parents[3] / "CHANGELOG.md"

# A release heading is ``## [X.Y.Z](compare-url) (date)``, or ``## X.Y.Z (date)`` without a link.
_RELEASE_RE = re.compile(r"^## \[?(\d+)\.(\d+)\.(\d+)\]?(?:\(https?://[^)]*\))?\s*(?:\((\d{4}-\d{2}-\d{2})\))?\s*$")
_GROUP_RE = re.compile(r"^### (.+?)\s*$")
_LINK_RE = re.compile(r"\s*\(\[[^\]]*\]\([^)]*\)\)")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")


#: Where the UI opens the changelog: the in-app docs page (GitHub Pages is disabled for the repository).
CHANGELOG_DOCS_PATH = "/docs/changelog"


def releases_url() -> str:
    """The GitHub releases page of the configured repository."""
    return f"https://github.com/{settings.auto_update_repository}/releases"


@dataclass
class ReleaseNotes:
    """One released version and its changes, grouped by heading."""

    version: str
    date: str | None
    groups: dict[str, list[str]] = field(default_factory=dict)

    @property
    def key(self) -> tuple[int, int, int]:
        """The version as a comparable tuple."""
        return parse_version(self.version) or (0, 0, 0)


def parse_version(value: str | None) -> tuple[int, int, int] | None:
    """Returns ``(major, minor, patch)`` for ``X.Y.Z`` (an optional leading ``v`` is ignored), else None."""
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", (value or "").strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def _plain(bullet: str) -> str:
    """Drops release-please's trailing ``([#1](url)) ([sha](url))`` links and any other markdown links."""
    return _MD_LINK_RE.sub(r"\1", _LINK_RE.sub("", bullet)).strip()


def parse_changelog(text: str) -> list[ReleaseNotes]:
    """Parses release-please's changelog into releases, newest first as written in the file."""
    releases: list[ReleaseNotes] = []
    current: ReleaseNotes | None = None
    group: str | None = None
    for line in text.splitlines():
        release = _RELEASE_RE.match(line)
        if release:
            current = ReleaseNotes(".".join(release.group(i) for i in (1, 2, 3)), release.group(4))
            releases.append(current)
            group = None
        elif line.startswith("## "):
            # A heading in another shape (a pre-release) is not ours, nor the previous release's.
            current = None
            group = None
        elif current and (heading := _GROUP_RE.match(line)):
            group = heading.group(1)
        elif current and group and line.startswith("* "):
            current.groups.setdefault(group, []).append(_plain(line[2:]))
    return releases


_cache: dict[Path, tuple[float, list[ReleaseNotes]]] = {}


def load_releases(path: Path = CHANGELOG_PATH) -> list[ReleaseNotes]:
    """Reads and parses the changelog, cached until the file changes; no file means no release notes."""
    try:
        modified = path.stat().st_mtime
        cached = _cache.get(path)
        if cached and cached[0] == modified:
            return cached[1]
        releases = parse_changelog(path.read_text(encoding="utf-8"))
    except OSError:
        return []
    _cache[path] = (modified, releases)
    return releases


def releases_since(
    releases: list[ReleaseNotes], since: str | None, current: str, *, limit: int = 5
) -> list[ReleaseNotes]:
    """Returns the releases after ``since`` up to and including ``current``, newest first.

    Without a usable ``since`` (a first visit) nothing is returned: there is no "previous
    release" to compare with, so no flash is due.
    """
    seen, now = parse_version(since), parse_version(current)
    if seen is None or now is None or seen >= now:
        return []
    return sorted((r for r in releases if seen < r.key <= now), key=lambda r: r.key, reverse=True)[:limit]
