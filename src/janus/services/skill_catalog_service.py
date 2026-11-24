"""Remote catalog helpers for discovering agent skills from GitHub-hosted SKILL.md files."""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from typing import Any

import httpx
import yaml

_GITHUB_API_BASE_URL = "https://api.github.com"
_GITHUB_RAW_BASE_URL = "https://raw.githubusercontent.com"
_SKILLS_REPO = "anthropics/skills"
_SKILLS_DEFAULT_BRANCH = "main"
_TREE_PATH = f"/repos/{_SKILLS_REPO}/git/trees/{_SKILLS_DEFAULT_BRANCH}"
_TIMEOUT_SECONDS = 8.0
_CACHE_TTL_SECONDS = 300.0
_SKILL_PATH_RE = re.compile(r"^skills/(?P<remote_id>[^/]+)/SKILL\.md$")
_MAX_FETCH_WORKERS = 8

_catalog_cache: dict[str, Any] = {"expires_at": 0.0, "items": []}


def _normalize_text(value: Any) -> str:
    """Return a stripped string for scalar values."""
    if value is None:
        return ""
    return str(value).strip()


def _parse_skill_markdown(remote_id: str, raw_markdown: str) -> dict[str, Any] | None:
    """Parse a SKILL.md document with YAML frontmatter."""
    frontmatter: dict[str, Any] = {}
    content = raw_markdown.strip()

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) == 3:
            _, raw_frontmatter, raw_body = parts
            try:
                parsed = yaml.safe_load(raw_frontmatter) or {}
            except yaml.YAMLError:
                parsed = {}
            if not isinstance(parsed, dict):
                parsed = {}
            frontmatter = parsed
            content = raw_body.strip()

    name = _normalize_text(frontmatter.get("name") or remote_id)
    if not name:
        return None

    description = _normalize_text(frontmatter.get("description"))
    license_name = _normalize_text(frontmatter.get("license")) or None
    source_url = f"https://github.com/{_SKILLS_REPO}/tree/{_SKILLS_DEFAULT_BRANCH}/skills/{remote_id}"

    return {
        "remote_id": remote_id,
        "name": name,
        "description": description,
        "license": license_name,
        "content": content or None,
        "source_url": source_url,
    }


def _fetch_catalog_skill(remote_id: str) -> dict[str, Any] | None:
    """Fetch and parse a single SKILL.md document from the remote catalog."""
    raw_url = f"{_GITHUB_RAW_BASE_URL}/{_SKILLS_REPO}/{_SKILLS_DEFAULT_BRANCH}/skills/{remote_id}/SKILL.md"
    try:
        response = httpx.get(raw_url, headers={"User-Agent": "janus-skill-catalog"}, timeout=_TIMEOUT_SECONDS)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return _parse_skill_markdown(remote_id, response.text)


def _fetch_catalog_items() -> list[dict[str, Any]]:
    """Fetch and parse all catalog skills from the GitHub repository."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "janus-skill-catalog",
    }
    params = {"recursive": "1"}

    with httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
        tree_response = client.get(f"{_GITHUB_API_BASE_URL}{_TREE_PATH}", params=params, headers=headers)
        tree_response.raise_for_status()
        tree_payload = tree_response.json()

    remote_ids: list[str] = []
    for entry in tree_payload.get("tree", []):
        path = _normalize_text(entry.get("path"))
        match = _SKILL_PATH_RE.match(path)
        if match:
            remote_ids.append(match.group("remote_id"))

    items: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=_MAX_FETCH_WORKERS) as executor:
        for parsed in executor.map(_fetch_catalog_skill, remote_ids):
            if parsed is not None:
                items.append(parsed)

    return sorted(items, key=lambda item: item["name"].lower())


def _get_catalog_items() -> list[dict[str, Any]]:
    """Return cached catalog items, refreshing them when needed."""
    now = monotonic()
    expires_at = float(_catalog_cache.get("expires_at") or 0.0)
    cached_items = _catalog_cache.get("items")
    if now < expires_at and isinstance(cached_items, list):
        return cached_items

    items = _fetch_catalog_items()
    _catalog_cache["items"] = items
    _catalog_cache["expires_at"] = now + _CACHE_TTL_SECONDS
    return items


def _query_matches(item: dict[str, Any], query: str) -> bool:
    """Return whether a normalized skill matches the query."""
    if not query:
        return True
    haystack = " ".join(
        (
            _normalize_text(item.get("remote_id")),
            _normalize_text(item.get("name")),
            _normalize_text(item.get("description")),
            _normalize_text(item.get("license")),
            _normalize_text(item.get("content")),
        )
    ).lower()
    return query in haystack


def normalize_skill_name(raw_name: str) -> str:
    """Convert arbitrary skill names to Janus' slug-safe naming convention."""
    normalized = re.sub(r"[^a-z0-9]+", "-", raw_name.strip().lower()).strip("-")
    if not normalized:
        return ""
    return normalized[:100]


def search_remote_skills(query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """Search the GitHub-backed community skill catalog and return normalized results."""
    cleaned_query = query.strip().lower()
    clamped_limit = max(1, min(limit, 50))

    results: list[dict[str, Any]] = []
    for item in _get_catalog_items():
        if _query_matches(item, cleaned_query):
            results.append(item)
        if len(results) >= clamped_limit:
            break
    return results


def get_remote_skill(remote_id: str) -> dict[str, Any] | None:
    """Fetch a single remote skill by id/slug from the GitHub-backed community catalog."""
    cleaned = remote_id.strip().lower()
    if not cleaned:
        return None

    for item in _get_catalog_items():
        if item["remote_id"].lower() == cleaned or normalize_skill_name(item["name"]) == cleaned:
            return item
    return None
