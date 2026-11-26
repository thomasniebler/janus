"""Repair wiki bodies stored as a content-block list, and requeue lost extraction (JAN-410).

Wiki synthesis saved ``str(message.content)``. With the OpenAI Responses API
that content is a list of blocks (a reasoning block with encrypted content,
then the text), so pages and revisions held the whole Python repr of the list.
This keeps only the text blocks, and the citations still cited in that text.

Knowledge extraction parsed the same repr, found no JSON, and still marked the
chunks as extracted. For users whose extraction never produced one mention,
the marks are cleared so the chunks are extracted again (within the budget).

Revision ID: d7a9c1e3f5b8
Revises: c6f8d0e2a4b7
Create Date: 2026-11-26 15:00:00.000000
"""

import ast
import json
import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7a9c1e3f5b8"
down_revision: str | Sequence[str] | None = "c6f8d0e2a4b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("wiki_pages", "wiki_page_revisions")


def block_text(body: str | None) -> str | None:
    """The joined text blocks of a body stored as a block-list repr; ``None`` when it is not one."""
    if not body or not body.startswith("[{"):
        return None
    try:
        blocks = ast.literal_eval(body)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return None
    if not isinstance(blocks, list) or not all(isinstance(block, dict) and "type" in block for block in blocks):
        return None
    return "".join(str(block.get("text") or "") for block in blocks if block.get("type") == "text").strip()


def repair(bind: sa.engine.Connection) -> int:
    """Rewrites block-list bodies to their text; returns the number of rows changed."""
    changed = 0
    for table in TABLES:
        rows = bind.execute(sa.text(f"SELECT id, body, citations FROM {table} WHERE body LIKE '[{{%'")).all()  # noqa: S608
        for row_id, body, citations in rows:
            text = block_text(body)
            if not text:
                continue
            cited = {int(number) for number in re.findall(r"\[(\d+)\]", text)}
            kept = json.loads(citations) if isinstance(citations, str) else (citations or [])
            kept = [citation for citation in kept if citation.get("n") in cited]
            bind.execute(
                sa.text(f"UPDATE {table} SET body = :body, citations = :citations WHERE id = :id"),  # noqa: S608
                {"body": text, "citations": json.dumps(kept), "id": row_id},
            )
            changed += 1
    return changed


def requeue_extraction(bind: sa.engine.Connection) -> int:
    """Clears ``extracted_hash`` of users without any mention; returns the number of chunks requeued."""
    return bind.execute(
        sa.text(
            "UPDATE knowledge_chunks SET extracted_hash = NULL WHERE extracted_hash IS NOT NULL "
            "AND user_id NOT IN (SELECT DISTINCT user_id FROM knowledge_mentions)"
        )
    ).rowcount


def upgrade() -> None:
    """Keep only the text of block-list wiki bodies and requeue extraction that never produced a mention."""
    bind = op.get_bind()
    repair(bind)
    requeue_extraction(bind)


def downgrade() -> None:
    """Nothing to undo: the removed blocks were never page text, and requeued chunks are simply extracted again."""
