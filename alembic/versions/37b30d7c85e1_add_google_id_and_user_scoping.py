"""Add google_id and user scoping.

Revision ID: 37b30d7c85e1
Revises: f0a1b2c3d4e5
Create Date: 2026-05-27 10:42:02.291820

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "37b30d7c85e1"
down_revision: str | Sequence[str] | None = "f0a1b2c3d4e5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("email_accounts") as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_email_accounts_user_id", ["user_id"], unique=False)
        batch_op.create_foreign_key("fk_email_accounts_user_id", "users", ["user_id"], ["id"])

    with op.batch_alter_table("oauth_states") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.INTEGER(), nullable=True)

    with op.batch_alter_table("planner_accounts") as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_planner_accounts_user_id", ["user_id"], unique=False)
        batch_op.create_foreign_key("fk_planner_accounts_user_id", "users", ["user_id"], ["id"])

    with op.batch_alter_table("rules") as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_rules_user_id", ["user_id"], unique=False)
        batch_op.create_foreign_key("fk_rules_user_id", "users", ["user_id"], ["id"])

    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("google_id", sa.String(), nullable=True))
        batch_op.create_index("ix_users_google_id", ["google_id"], unique=True)

    # Backfill: assign existing rows to the first admin user so data is not orphaned.
    # IMPORTANT: If no admin user exists at migration time the subquery returns NULL and
    # those rows will remain user_id = NULL. The subsequent ALTER COLUMN NOT NULL will
    # then fail loudly, preventing a silent data-loss scenario. Fix by creating at least
    # one admin user before running this migration.
    op.execute(
        "UPDATE email_accounts SET user_id = ("
        "  SELECT id FROM users WHERE is_admin = TRUE ORDER BY id LIMIT 1"
        ") WHERE user_id IS NULL"
    )
    op.execute(
        "UPDATE planner_accounts SET user_id = ("
        "  SELECT id FROM users WHERE is_admin = TRUE ORDER BY id LIMIT 1"
        ") WHERE user_id IS NULL"
    )
    op.execute(
        "UPDATE rules SET user_id = ("
        "  SELECT id FROM users WHERE is_admin = TRUE ORDER BY id LIMIT 1"
        ") WHERE user_id IS NULL"
    )

    with op.batch_alter_table("email_accounts") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("planner_accounts") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("rules") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.Integer(), nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index("ix_users_google_id")
        batch_op.drop_column("google_id")

    with op.batch_alter_table("rules") as batch_op:
        batch_op.drop_constraint("fk_rules_user_id", type_="foreignkey")
        batch_op.drop_index("ix_rules_user_id")
        batch_op.drop_column("user_id")

    with op.batch_alter_table("planner_accounts") as batch_op:
        batch_op.drop_constraint("fk_planner_accounts_user_id", type_="foreignkey")
        batch_op.drop_index("ix_planner_accounts_user_id")
        batch_op.drop_column("user_id")

    with op.batch_alter_table("oauth_states") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.INTEGER(), nullable=False)

    with op.batch_alter_table("email_accounts") as batch_op:
        batch_op.drop_constraint("fk_email_accounts_user_id", type_="foreignkey")
        batch_op.drop_index("ix_email_accounts_user_id")
        batch_op.drop_column("user_id")
