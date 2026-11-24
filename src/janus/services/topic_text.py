"""Pure text helpers shared by tracked-topic titles, slugs, and marker keywords."""

from __future__ import annotations

import json
import re
from typing import Any

from janus import models


def clean_text(value: str | None) -> str | None:
    """Collapse whitespace and return None for values that carry no text."""
    if value is None:
        return None
    cleaned = " ".join(value.split())
    return cleaned or None


def slugify(value: str | None) -> str | None:
    """Return the normalized topic slug for one title or dedupe hint."""
    cleaned = clean_text(value)
    if cleaned is None:
        return None
    slug = re.sub(r"-+", "-", "".join(ch.lower() if ch.isalnum() else "-" for ch in cleaned)).strip("-")
    return slug or None


def normalize_marker_keywords(values: list[str] | tuple[str, ...] | None) -> list[str]:
    """Trim and deduplicate user-defined marker keywords while preserving order."""
    if not values:
        return []

    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        keyword = clean_text(value)
        if keyword is None:
            continue
        dedupe_key = keyword.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        cleaned.append(keyword)
    return cleaned


def dump_marker_keywords(values: list[str] | tuple[str, ...] | None) -> str | None:
    """Serialize marker keywords for storage on tracked topics."""
    normalized = normalize_marker_keywords(values)
    if not normalized:
        return None
    return json.dumps(normalized)


def load_marker_keywords(raw_value: str | None) -> list[str]:
    """Load stored marker keywords from JSON text, failing closed on malformed content."""
    if not raw_value:
        return []
    try:
        parsed = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return normalize_marker_keywords([value for value in parsed if isinstance(value, str)])


def strip_candidate_topics(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop CANDIDATE topic badges from serialized email payloads, in place.

    Candidate topics are still awaiting corroboration, so any surface that
    feeds topics into an LLM (briefings, digests, chat context) must exclude
    them even though they remain visible as badges in the UI. Every caller
    that builds LLM-facing email context should route through this helper so
    the exclusion stays enforced in one place.
    """
    for payload in payloads:
        payload["topics"] = [
            topic
            for topic in payload.get("topics", [])
            if not (isinstance(topic, dict) and topic.get("status") == models.TopicStatus.CANDIDATE)
        ]
    return payloads


def build_email_search_text(email_record: models.EmailRecord) -> str:
    """Flatten the relevant email fields for lightweight marker-keyword matching."""
    return "\n".join(
        value
        for value in (
            email_record.sender,
            email_record.subject,
            email_record.snippet,
            email_record.body,
        )
        if value
    ).lower()
