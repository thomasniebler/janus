"""Vector and keyword index adapters for semantic search (JAN-241).

Services only talk to ``VectorIndex`` and ``KeywordIndex``; nothing outside
this module branches on the database dialect. Vectors themselves live in the
dialect-neutral ``knowledge_embeddings`` table, so a PostgreSQL/pgvector
implementation only needs its own ``search()``.

- ``SqliteVecIndex`` ranks rows with ``sqlite-vec``'s ``vec_distance_cosine()``
  (exact brute force). The extension is loaded into each pooled DBAPI
  connection on first use, which needs ``sqlite3.Connection.enable_load_extension``.
- ``Fts5KeywordIndex`` keeps one FTS5 table per owner type
  (``knowledge_fts_<owner_type>``, rowid = owner id), created and backfilled
  when missing.

When either is unavailable (PostgreSQL, a Python built without extension
loading, SQLite without FTS5), the factory returns ``None`` and callers fall
back to the next weaker search.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import struct
import threading
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import LargeBinary, Select, bindparam, column, func, literal_column, select, table, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: Upper bound on candidates any single index search returns.
MAX_CANDIDATES = 500


@dataclass(frozen=True)
class SearchCapabilities:
    """What this Python/SQLite runtime supports for knowledge search."""

    load_extension: bool
    sqlite_vec: bool
    sqlite_vec_version: str | None
    fts5: bool
    error: str | None

    @property
    def vector_search(self) -> bool:
        """Whether vector search can run on SQLite."""
        return self.load_extension and self.sqlite_vec


_capabilities: SearchCapabilities | None = None
_capabilities_lock = threading.Lock()


def _load_sqlite_vec(dbapi_connection) -> str | None:
    """Loads sqlite-vec into one DBAPI connection and returns its version."""
    import sqlite_vec

    dbapi_connection.enable_load_extension(True)
    try:
        sqlite_vec.load(dbapi_connection)
    finally:
        dbapi_connection.enable_load_extension(False)
    return dbapi_connection.execute("SELECT vec_version()").fetchone()[0]


def probe_capabilities() -> SearchCapabilities:
    """Probes (once per process) extension loading, sqlite-vec, and FTS5 on an in-memory database."""
    global _capabilities
    with _capabilities_lock:
        if _capabilities is not None:
            return _capabilities
        conn = sqlite3.connect(":memory:")
        try:
            load_extension = hasattr(conn, "enable_load_extension")
            vec_version: str | None = None
            error: str | None = None
            if not load_extension:
                error = "Python's sqlite3 module was built without enable_load_extension."
            else:
                try:
                    vec_version = _load_sqlite_vec(conn)
                except Exception as exc:  # ImportError, OperationalError, AttributeError
                    error = f"sqlite-vec could not be loaded: {exc}"
            try:
                conn.execute("CREATE VIRTUAL TABLE probe_fts USING fts5(body)")
                fts5 = True
            except sqlite3.OperationalError:
                fts5 = False
        finally:
            conn.close()
        _capabilities = SearchCapabilities(
            load_extension=load_extension,
            sqlite_vec=vec_version is not None,
            sqlite_vec_version=vec_version,
            fts5=fts5,
            error=error,
        )
        return _capabilities


def log_capabilities() -> None:
    """Logs a warning at startup when vector or keyword search is unavailable."""
    capabilities = probe_capabilities()
    if not capabilities.vector_search:
        logger.warning(
            "Vector search is unavailable; memory and topic search fall back to keyword search. %s",
            capabilities.error,
        )
    if not capabilities.fts5:
        logger.warning("SQLite FTS5 is unavailable; keyword search falls back to substring matching.")


def serialize_vector(vector: list[float]) -> bytes:
    """Packs a vector as little-endian float32 (the sqlite-vec and pgvector wire format)."""
    return struct.pack(f"<{len(vector)}f", *vector)


def _is_sqlite(db: Session) -> bool:
    return db.get_bind().dialect.name == "sqlite"


def _table_exists(db: Session, name: str) -> bool:
    return (
        db.execute(text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :name"), {"name": name}).first()
        is not None
    )


#: Lightweight Core view of ``knowledge_embeddings`` (this module stays free of ORM imports).
_EMBEDDINGS = table(
    "knowledge_embeddings",
    column("owner_id"),
    column("user_id"),
    column("owner_type"),
    column("model"),
    column("dimensions"),
    column("vector"),
)


class VectorIndex(Protocol):
    """Similarity search over ``knowledge_embeddings`` rows."""

    def search(
        self,
        db: Session,
        *,
        user_id: int,
        owner_type: str,
        model: str,
        query_vector: list[float],
        min_similarity: float,
        candidates: Select | None = None,
        limit: int = MAX_CANDIDATES,
    ) -> list[tuple[int, float]]:
        """Returns ``(owner_id, cosine_similarity)`` pairs, most similar first.

        ``candidates`` is a ``SELECT`` of the owner ids the caller's metadata
        filters allow; ranking only ever runs inside that set.
        """
        ...


class SqliteVecIndex:
    """``VectorIndex`` over ``knowledge_embeddings`` using sqlite-vec's scalar functions."""

    @staticmethod
    def _ensure_loaded(db: Session) -> None:
        pooled = db.connection().connection
        if not pooled.info.get("sqlite_vec_loaded"):
            _load_sqlite_vec(pooled.dbapi_connection)
            pooled.info["sqlite_vec_loaded"] = True

    def search(
        self,
        db: Session,
        *,
        user_id: int,
        owner_type: str,
        model: str,
        query_vector: list[float],
        min_similarity: float,
        candidates: Select | None = None,
        limit: int = MAX_CANDIDATES,
    ) -> list[tuple[int, float]]:
        """Returns ``(owner_id, cosine_similarity)`` pairs at or above ``min_similarity``, within ``candidates``."""
        self._ensure_loaded(db)
        similarity = (
            1.0
            - func.vec_distance_cosine(
                _EMBEDDINGS.c.vector, bindparam("query", serialize_vector(query_vector), LargeBinary)
            )
        ).label("similarity")
        stmt = (
            select(_EMBEDDINGS.c.owner_id, similarity)
            .where(
                _EMBEDDINGS.c.user_id == user_id,
                _EMBEDDINGS.c.owner_type == owner_type,
                _EMBEDDINGS.c.model == model,
                _EMBEDDINGS.c.dimensions == len(query_vector),
            )
            .order_by(similarity.desc())
            .limit(limit)
        )
        if candidates is not None:
            stmt = stmt.where(_EMBEDDINGS.c.owner_id.in_(candidates))
        rows = db.execute(stmt).all()
        return [(int(owner_id), float(value)) for owner_id, value in rows if value >= min_similarity]


def get_vector_index(db: Session) -> VectorIndex | None:
    """Returns the vector index for this session's database, or ``None`` when unavailable."""
    if _is_sqlite(db) and probe_capabilities().vector_search:
        return SqliteVecIndex()
    return None


class KeywordIndex(Protocol):
    """Full-text search over one owner type's text."""

    def ensure_tables(self, db: Session) -> None:
        """Creates and backfills any missing index storage."""
        ...

    def upsert(self, db: Session, owner_type: str, rows: list[tuple[int, int, str]]) -> None:
        """Indexes ``(owner_id, user_id, text)`` rows, replacing earlier text."""
        ...

    def delete(self, db: Session, owner_type: str, owner_ids: list[int]) -> None:
        """Removes owners from the index."""
        ...

    def search(
        self, db: Session, *, user_id: int, owner_type: str, query: str, candidates: Select | None = None
    ) -> list[int]:
        """Returns matching owner ids within ``candidates``, best match first."""
        ...


#: How to backfill each FTS table from its owner table on creation.
_FTS_BACKFILL_SQL = {
    "fact": (
        "SELECT id, user_id, summary || ' ' || COALESCE(detail, '') FROM agent_memory_facts",
        "agent_memory_facts",
    ),
    "topic": (
        "SELECT id, user_id, title || ' ' || COALESCE(summary, '') || ' ' || COALESCE(marker_keywords, '') "
        "FROM tracked_topics",
        "tracked_topics",
    ),
}

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def fts_match_expression(query: str) -> str | None:
    """Turns free text into an FTS5 expression: any word, prefix-matched."""
    tokens = _TOKEN_RE.findall(query.lower())
    if not tokens:
        return None
    return " OR ".join(f'"{token}"*' for token in dict.fromkeys(tokens))


def drop_keyword_tables(db: Session) -> None:
    """Drops every FTS table so the next use rebuilds it from the owner tables."""
    if not _is_sqlite(db):
        return
    for owner_type in _FTS_BACKFILL_SQL:
        db.execute(text(f"DROP TABLE IF EXISTS knowledge_fts_{owner_type}"))


class Fts5KeywordIndex:
    """``KeywordIndex`` backed by one SQLite FTS5 table per owner type."""

    @staticmethod
    def table_name(owner_type: str) -> str:
        """The FTS5 table name for ``owner_type`` (``fact`` or ``topic``)."""
        if owner_type not in _FTS_BACKFILL_SQL:
            raise ValueError(f"unknown knowledge owner type: {owner_type}")
        return f"knowledge_fts_{owner_type}"

    def _ensure_table(self, db: Session, owner_type: str) -> str:
        """Creates and backfills the FTS table when it is missing; returns its name.

        Checked on every call rather than cached per connection, because a
        rolled-back transaction also rolls back the ``CREATE VIRTUAL TABLE``.
        """
        table = self.table_name(owner_type)
        if _table_exists(db, table):
            return table
        db.execute(text(f"CREATE VIRTUAL TABLE {table} USING fts5(body, user_id UNINDEXED)"))
        select_sql, owner_table = _FTS_BACKFILL_SQL[owner_type]
        if _table_exists(db, owner_table):
            db.execute(text(f"INSERT INTO {table}(rowid, user_id, body) {select_sql}"))
        return table

    def ensure_tables(self, db: Session) -> None:
        """Creates and backfills every missing FTS table (startup)."""
        for owner_type in _FTS_BACKFILL_SQL:
            self._ensure_table(db, owner_type)

    def upsert(self, db: Session, owner_type: str, rows: list[tuple[int, int, str]]) -> None:
        """Indexes ``(owner_id, user_id, text)`` rows, replacing earlier text."""
        if not rows:
            return
        table = self._ensure_table(db, owner_type)
        db.execute(text(f"DELETE FROM {table} WHERE rowid = :id"), [{"id": owner_id} for owner_id, _, _ in rows])
        db.execute(
            text(f"INSERT INTO {table}(rowid, user_id, body) VALUES (:id, :user_id, :body)"),
            [{"id": owner_id, "user_id": user_id, "body": body} for owner_id, user_id, body in rows],
        )

    def delete(self, db: Session, owner_type: str, owner_ids: list[int]) -> None:
        """Removes owners from the index."""
        if not owner_ids:
            return
        table = self._ensure_table(db, owner_type)
        db.execute(text(f"DELETE FROM {table} WHERE rowid = :id"), [{"id": owner_id} for owner_id in owner_ids])

    def search(
        self, db: Session, *, user_id: int, owner_type: str, query: str, candidates: Select | None = None
    ) -> list[int]:
        """Returns matching owner ids for one user within ``candidates``, best BM25 rank first."""
        expression = fts_match_expression(query)
        if expression is None:
            return []
        name = self._ensure_table(db, owner_type)
        fts = table(name, column("rowid"), column("user_id"))
        stmt = (
            select(fts.c.rowid)
            .where(text(f"{name} MATCH :expression").bindparams(expression=expression), fts.c.user_id == user_id)
            .order_by(literal_column("rank"))
            .limit(MAX_CANDIDATES)
        )
        if candidates is not None:
            stmt = stmt.where(fts.c.rowid.in_(candidates))
        return [int(row[0]) for row in db.execute(stmt).all()]


def get_keyword_index(db: Session) -> KeywordIndex | None:
    """Returns the keyword index for this session's database, or ``None`` when unavailable."""
    if _is_sqlite(db) and probe_capabilities().fts5:
        return Fts5KeywordIndex()
    return None
