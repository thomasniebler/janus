"""Copy all Janus data from one database into another (e.g. SQLite -> PostgreSQL)."""

from collections.abc import Callable
from dataclasses import dataclass, field

import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory

from janus import database

_BATCH_SIZE = 1000


class DatabaseCopyError(RuntimeError):
    """Raised when a copy cannot start safely."""


@dataclass
class DatabaseCopyResult:
    """Row counts copied per table."""

    rows_per_table: dict[str, int] = field(default_factory=dict)

    @property
    def total_rows(self) -> int:
        """Return the number of rows copied across all tables."""
        return sum(self.rows_per_table.values())


def _alembic_config(url: str) -> Config:
    """Return an Alembic config bound to ``url``."""
    alembic_config = Config(str(database.ALEMBIC_INI_PATH))
    # configparser treats "%" as interpolation syntax, so escape it for URL-encoded passwords.
    alembic_config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return alembic_config


def _current_revisions(engine: sa.Engine) -> set[str]:
    """Return the Alembic revisions a database is stamped with."""
    with engine.connect() as conn:
        return set(MigrationContext.configure(conn).get_current_heads())


def _self_referencing_columns(table: sa.Table) -> list[str]:
    """Return columns holding a foreign key to their own table."""
    return [fk.parent.name for fk in table.foreign_keys if fk.column.table is table]


def _reset_postgresql_sequences(conn: sa.Connection, tables: list[sa.Table]) -> None:
    """Advance serial sequences past the copied ids so new inserts do not collide."""
    for table in tables:
        for column in table.primary_key.columns:
            if not isinstance(column.type, sa.Integer):
                continue
            sequence = conn.execute(
                sa.text("SELECT pg_get_serial_sequence(:table, :column)"),
                {"table": table.name, "column": column.name},
            ).scalar()
            if sequence:
                max_id = f'MAX("{column.name}")'
                conn.execute(
                    sa.text(
                        f'SELECT setval(:sequence, COALESCE({max_id}, 1), {max_id} IS NOT NULL) FROM "{table.name}"'
                    ),
                    {"sequence": sequence},
                )


def copy_database(
    source_url: str,
    target_url: str,
    *,
    on_table: Callable[[str, int], None] | None = None,
) -> DatabaseCopyResult:
    """Copy every Janus table from ``source_url`` into an empty ``target_url``.

    The source must be migrated to the current head. The target is migrated to head first and
    must not contain any Janus rows yet. The copy runs in one target transaction, so a failure
    leaves the target empty. The source is only read.
    """
    if source_url == target_url:
        raise DatabaseCopyError("Source and target databases must differ.")

    head = ScriptDirectory.from_config(_alembic_config(source_url)).get_current_head()
    source_engine = sa.create_engine(source_url)
    target_engine = sa.create_engine(target_url)
    try:
        source_revisions = _current_revisions(source_engine)
        if source_revisions != {head}:
            raise DatabaseCopyError(
                f"Source database is at revision {sorted(source_revisions) or 'none'}, expected {head}. "
                "Start Janus (or run `alembic upgrade head`) against the source first."
            )

        database.ensure_schema_up_to_date(target_url)

        tables = database.Base.metadata.sorted_tables
        source_metadata = sa.MetaData()
        source_metadata.reflect(bind=source_engine, only=[table.name for table in tables])

        with target_engine.connect() as conn:
            occupied = [
                table.name for table in tables if conn.execute(sa.select(sa.func.count()).select_from(table)).scalar()
            ]
        if occupied:
            raise DatabaseCopyError(f"Target database already contains data in: {', '.join(occupied)}.")

        result = DatabaseCopyResult()
        with source_engine.connect() as source_conn, target_engine.begin() as target_conn:
            for table in tables:
                # Read through the reflected source table so values are passed on as stored,
                # instead of being validated against (and possibly rejected by) the ORM types.
                source_table = source_metadata.tables[table.name]
                shared_columns = [column.name for column in table.columns if column.name in source_table.columns]
                deferred_columns = _self_referencing_columns(table)
                deferred_updates: list[dict] = []
                copied = 0

                rows = source_conn.execute(
                    sa.select(*(source_table.c[name] for name in shared_columns)).order_by(
                        *source_table.primary_key.columns
                    )
                ).mappings()
                while batch := rows.fetchmany(_BATCH_SIZE):
                    records = [dict(row) for row in batch]
                    for record in records:
                        deferred = {name: record[name] for name in deferred_columns if record.get(name) is not None}
                        if deferred:
                            # Self-references may point at rows later in the copy order.
                            deferred_updates.append({"_pk": record["id"], **deferred})
                            record.update(dict.fromkeys(deferred, None))
                    target_conn.execute(table.insert(), records)
                    copied += len(records)

                for name in deferred_columns:
                    updates = [
                        {"_pk": update["_pk"], "_value": update[name]} for update in deferred_updates if name in update
                    ]
                    if updates:
                        target_conn.execute(
                            table.update()
                            .where(table.c.id == sa.bindparam("_pk"))
                            .values({name: sa.bindparam("_value")}),
                            updates,
                        )

                result.rows_per_table[table.name] = copied
                if on_table:
                    on_table(table.name, copied)

            if target_engine.dialect.name == "postgresql":
                _reset_postgresql_sequences(target_conn, tables)
        return result
    finally:
        source_engine.dispose()
        target_engine.dispose()
