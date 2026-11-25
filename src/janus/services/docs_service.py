"""Read-only access to Janus's own documentation (``docs/docs``), shared by the API and the agent.

``api/docs.py`` (the in-app ``/docs`` page) and the ``docs`` agent toolbox
(JAN-297) both go through this module, so they share one set of
path-confinement rules:

* a page path is resolved against ``docs/docs`` and must stay inside it after
  resolution -- ``..`` segments, absolute paths, and symlinks pointing outside
  are all "not found";
* the only file outside ``docs/docs`` that can be read is the root
  ``TECH_SPEC.md``, through the ``tech-spec.md`` alias.

The agent additionally passes :data:`AGENT_EXCLUDED_DIRS`: the ``reviews/``
directory (dated security assessment and code review reports) is not offered
to Janus. Those reports describe weaknesses at a point in time, are mostly
stale, and are not instructions for users; the router still serves them
unchanged.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from janus import resources

REPO_ROOT = resources.docs_root()
DOCS_DIR = REPO_ROOT / "docs" / "docs"
NAV_CONFIG = REPO_ROOT / "docs" / "zensical.toml"
TECH_SPEC_ALIAS = "tech-spec.md"
CHANGELOG_ALIAS = "changelog.md"
# Root documents shown in the docs: the tech-spec index, and release-please's changelog (which
# ``docs/docs/changelog.md`` links for the static site).
ROOT_DOC_ALIASES = {TECH_SPEC_ALIAS: REPO_ROOT / "TECH_SPEC.md", CHANGELOG_ALIAS: REPO_ROOT / "CHANGELOG.md"}

#: Top-level ``docs/docs`` directories the agent toolbox never lists, reads, or searches.
AGENT_EXCLUDED_DIRS: frozenset[str] = frozenset({"reviews"})

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


class DocNotFoundError(LookupError):
    """Raised when a documentation path does not name a readable page."""


def _docs_root() -> Path:
    return DOCS_DIR.resolve()


def resolve_doc_path(path: str, *, excluded_dirs: Iterable[str] = ()) -> Path:
    """Return the real file for a documentation path, or raise :class:`DocNotFoundError`.

    ``path`` is relative to ``docs/docs`` (``""`` means ``index.md``). The
    resolved file must be a regular file inside ``docs/docs`` (so ``..``,
    absolute paths, and outward symlinks are refused), unless ``path`` is the
    ``tech-spec.md`` or ``changelog.md`` alias. A file under one of ``excluded_dirs`` (top-level
    directory names) is refused too.
    """
    requested = path or "index.md"
    aliased = ROOT_DOC_ALIASES.get(requested)
    if aliased is not None:
        file_path = aliased.resolve()
        if not file_path.is_file():
            raise DocNotFoundError(requested)
        return file_path

    docs_root = _docs_root()
    try:
        file_path = (docs_root / requested).resolve()
    except (OSError, ValueError, RuntimeError) as error:  # embedded NUL, symlink loop
        raise DocNotFoundError(requested) from error
    if docs_root not in file_path.parents or not file_path.is_file():
        raise DocNotFoundError(requested)
    excluded = frozenset(excluded_dirs)
    if excluded and file_path.relative_to(docs_root).parts[0] in excluded:
        raise DocNotFoundError(requested)
    return file_path


def read_doc_text(path: str, *, excluded_dirs: Iterable[str] = ()) -> str:
    """Return the Markdown text of one documentation page."""
    return resolve_doc_path(path, excluded_dirs=excluded_dirs).read_text(encoding="utf-8")


def parse_nav(nav_data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recursively turn the Zensical nav structure into ``{name, path}`` / ``{name, children}`` nodes."""
    parsed: list[dict[str, Any]] = []
    for item in nav_data:
        for key, value in item.items():
            if isinstance(value, str):
                parsed.append({"name": key, "path": Path(value).as_posix()})
            elif isinstance(value, list):
                parsed.append({"name": key, "children": parse_nav(value)})
    return parsed


def load_nav() -> list[dict[str, Any]]:
    """Return the parsed navigation from ``docs/zensical.toml`` (``FileNotFoundError`` if it is missing)."""
    with NAV_CONFIG.open("rb") as config_file:
        config = tomllib.load(config_file)
    return parse_nav(config.get("project", {}).get("nav", []))


def _nav_paths(nodes: list[dict[str, Any]]) -> list[str]:
    paths: list[str] = []
    for node in nodes:
        if "path" in node:
            paths.append(node["path"])
        paths.extend(_nav_paths(node.get("children", [])))
    return paths


def _filter_nav(nodes: list[dict[str, Any]], excluded_dirs: frozenset[str]) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for node in nodes:
        if "children" in node:
            children = _filter_nav(node["children"], excluded_dirs)
            if children:
                kept.append({"name": node["name"], "children": children})
        elif PurePosixPath(node["path"]).parts[0] not in excluded_dirs:
            kept.append(node)
    return kept


def list_pages(*, excluded_dirs: Iterable[str] = ()) -> list[str]:
    """Return every readable page path (sorted), including the ``tech-spec.md`` alias."""
    docs_root = _docs_root()
    excluded = frozenset(excluded_dirs)
    pages: list[str] = []
    for file_path in docs_root.rglob("*.md"):
        relative = file_path.relative_to(docs_root)
        if relative.parts[0] in excluded:
            continue
        try:
            resolve_doc_path(relative.as_posix(), excluded_dirs=excluded)
        except DocNotFoundError:
            continue
        pages.append(relative.as_posix())
    if ROOT_DOC_ALIASES[TECH_SPEC_ALIAS].is_file():
        pages.append(TECH_SPEC_ALIAS)
    return sorted(pages)


def agent_nav() -> dict[str, Any]:
    """Return the nav tree without excluded pages, plus readable pages the nav does not list."""
    excluded = AGENT_EXCLUDED_DIRS
    try:
        nav = _filter_nav(load_nav(), excluded)
    except FileNotFoundError:
        nav = []
    listed = set(_nav_paths(nav))
    return {
        "nav": nav,
        "other_pages": [page for page in list_pages(excluded_dirs=excluded) if page not in listed],
    }


def slugify(heading: str) -> str:
    """Return the anchor slug of a heading, as the docs site builds it (``Send mail`` -> ``send-mail``)."""
    text = re.sub(r"[^\w\s-]", "", heading).strip().lower()
    return re.sub(r"[\s-]+", "-", text).strip("-")


@dataclass(frozen=True)
class DocSection:
    """One heading section of a page: from its heading to the next heading of the same or a higher level."""

    heading: str
    slug: str
    level: int
    text: str


def page_title(markdown: str, fallback: str) -> str:
    """Return the first top-level heading, or ``fallback``."""
    for section in split_sections(markdown):
        if section.level == 1:
            return section.heading
    return fallback


def split_sections(markdown: str, *, nested: bool = True) -> list[DocSection]:
    """Split Markdown into heading sections (headings inside code fences are ignored).

    With ``nested`` (the default) each section's text runs until the next
    heading of the same or a higher level, so a ``##`` section includes its
    ``###`` subsections; without it, until the next heading of any level.
    Text before the first heading is returned as a level-0 section with an
    empty heading.
    """
    lines = markdown.splitlines()
    headings: list[tuple[int, int, str]] = []  # (line index, level, heading)
    in_fence = False
    for index, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING_RE.match(line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip()))
    sections: list[DocSection] = []
    first = headings[0][0] if headings else len(lines)
    preamble = "\n".join(lines[:first]).strip()
    if preamble:
        sections.append(DocSection("", "", 0, preamble))
    for position, (start, level, heading) in enumerate(headings):
        end = len(lines)
        for later_start, later_level, _ in headings[position + 1 :]:
            if later_level <= level or not nested:
                end = later_start
                break
        sections.append(DocSection(heading, slugify(heading), level, "\n".join(lines[start:end]).strip()))
    return sections


def find_section(markdown: str, section: str) -> DocSection | None:
    """Return the section whose slug or heading text matches ``section`` (case-insensitive), if any."""
    wanted = section.strip().lstrip("#").strip()
    wanted_slug = slugify(wanted)
    for candidate in split_sections(markdown):
        if candidate.level == 0:
            continue
        if candidate.slug == wanted_slug or candidate.heading.lower() == wanted.lower():
            return candidate
    return None


__all__ = [
    "AGENT_EXCLUDED_DIRS",
    "DOCS_DIR",
    "DocNotFoundError",
    "DocSection",
    "ROOT_DOC_ALIASES",
    "TECH_SPEC_ALIAS",
    "agent_nav",
    "find_section",
    "list_pages",
    "load_nav",
    "page_title",
    "parse_nav",
    "read_doc_text",
    "resolve_doc_path",
    "slugify",
    "split_sections",
]
