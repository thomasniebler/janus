"""Lightweight link checks for technical-spec Markdown."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCS_ROOT = REPO_ROOT / "docs" / "docs"
TECH_SPEC_ROOT = DOCS_ROOT / "tech-spec"
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def _local_markdown_links(markdown: str) -> list[str]:
    links = []
    for match in MARKDOWN_LINK_RE.finditer(markdown):
        target = match.group(1).strip()
        if not target or target.startswith(("#", "http://", "https://", "mailto:")):
            continue
        target = target.split("#", 1)[0]
        if target.endswith("/"):
            links.append(f"{target}index.md")
        elif target.endswith(".md"):
            links.append(target)
    return links


def test_technical_spec_markdown_links_resolve():
    """Root and child technical specs should not contain stale local Markdown links."""
    spec_files = [REPO_ROOT / "TECH_SPEC.md", *sorted(TECH_SPEC_ROOT.glob("*.md"))]

    missing: list[str] = []
    for spec_file in spec_files:
        for target in _local_markdown_links(spec_file.read_text(encoding="utf-8")):
            resolved = (spec_file.parent / target).resolve()
            if not resolved.is_file():
                missing.append(f"{spec_file.relative_to(REPO_ROOT)} -> {target}")

    assert missing == []


def test_technical_spec_nav_targets_resolve():
    """Every technical-spec page listed in Zensical navigation should exist."""
    with (REPO_ROOT / "docs" / "zensical.toml").open("rb") as config_file:
        config = tomllib.load(config_file)

    nav = config["project"]["nav"]
    technical_spec = next(item["Technical Spec"] for item in nav if "Technical Spec" in item)

    missing: list[str] = []
    for item in technical_spec:
        path = next(iter(item.values()))
        if path == "tech-spec.md":  # noqa: SIM108
            resolved = REPO_ROOT / "TECH_SPEC.md"
        else:
            resolved = DOCS_ROOT / path
        if not resolved.is_file():
            missing.append(path)

    assert missing == []
