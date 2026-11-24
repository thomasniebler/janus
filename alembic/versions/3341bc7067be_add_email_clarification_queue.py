"""Add email clarification queue.

Revision ID: 3341bc7067be
Revises: b1c2d3e4f5a6
Create Date: 2026-04-17 14:33:03.923416

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "3341bc7067be"
down_revision: str | Sequence[str] | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    confidence_level = postgresql.ENUM("HIGH", "MEDIUM", "LOW", name="confidencelevel", create_type=False)
    clarification_status = postgresql.ENUM(
        "PENDING", "APPROVED", "DISMISSED", name="clarificationstatus", create_type=False
    )
    priority_enum = postgresql.ENUM("IMPORTANT", "DIGEST", "IGNORE", "SPAM", name="priority", create_type=False)

    confidence_level.create(bind, checkfirst=True)
    clarification_status.create(bind, checkfirst=True)

    email_columns = {column["name"] for column in inspector.get_columns("emails")}
    if "classification_confidence" not in email_columns:
        with op.batch_alter_table("emails") as batch_op:
            batch_op.add_column(sa.Column("classification_confidence", confidence_level, nullable=True))

    if not inspector.has_table("email_clarification_requests"):
        op.create_table(
            "email_clarification_requests",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("email_id", sa.Integer(), nullable=False),
            sa.Column("applied_rule_id", sa.Integer(), nullable=True),
            sa.Column("previous_priority", priority_enum, nullable=False),
            sa.Column("requested_priority", priority_enum, nullable=False),
            sa.Column("suggested_rule_name", sa.String(), nullable=False),
            sa.Column("suggested_rule_pattern", sa.String(), nullable=False),
            sa.Column("suggested_rule_field", sa.String(), nullable=False),
            sa.Column("suggested_rule_priority", priority_enum, nullable=False),
            sa.Column("suggestion_confidence", confidence_level, nullable=False),
            sa.Column("suggestion_reason", sa.String(), nullable=False),
            sa.Column("status", clarification_status, nullable=False, server_default="PENDING"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["applied_rule_id"], ["rules.id"]),
            sa.ForeignKeyConstraint(["email_id"], ["emails.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_email_clarification_requests_email_id"),
            "email_clarification_requests",
            ["email_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_email_clarification_requests_id"),
            "email_clarification_requests",
            ["id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_email_clarification_requests_status"),
            "email_clarification_requests",
            ["status"],
            unique=False,
        )
        op.create_index(
            op.f("ix_email_clarification_requests_user_id"),
            "email_clarification_requests",
            ["user_id"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema."""
    bind = op.get_bind()
    confidence_level = postgresql.ENUM("HIGH", "MEDIUM", "LOW", name="confidencelevel", create_type=False)
    clarification_status = postgresql.ENUM(
        "PENDING", "APPROVED", "DISMISSED", name="clarificationstatus", create_type=False
    )

    op.drop_index(op.f("ix_email_clarification_requests_user_id"), table_name="email_clarification_requests")
    op.drop_index(op.f("ix_email_clarification_requests_status"), table_name="email_clarification_requests")
    op.drop_index(op.f("ix_email_clarification_requests_id"), table_name="email_clarification_requests")
    op.drop_index(op.f("ix_email_clarification_requests_email_id"), table_name="email_clarification_requests")
    op.drop_table("email_clarification_requests")

    with op.batch_alter_table("emails") as batch_op:
        batch_op.drop_column("classification_confidence")

    clarification_status.drop(bind, checkfirst=True)
    confidence_level.drop(bind, checkfirst=True)
