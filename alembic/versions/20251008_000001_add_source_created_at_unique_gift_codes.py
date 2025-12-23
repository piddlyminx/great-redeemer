"""Add source_created_at and unique gift code+date

Revision ID: 20251008_000001
Revises: 20251007_000001
Create Date: 2025-10-08 00:00:01

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20251008_000001"
down_revision = "20251007_000001"
branch_labels = None
depends_on = None


def _has_unique_constraint(conn, table: str, name: str) -> bool:
    insp = sa.inspect(conn)
    return any(c["name"] == name for c in insp.get_unique_constraints(table))


def upgrade() -> None:
    conn = op.get_bind()
    with op.batch_alter_table("gift_codes") as batch_op:
        batch_op.add_column(sa.Column("source_created_at", sa.DateTime(), nullable=True))

    op.execute(
        "UPDATE gift_codes "
        "SET source_created_at = COALESCE(source_created_at, first_seen_at, created_at) "
        "WHERE source_created_at IS NULL"
    )

    with op.batch_alter_table("gift_codes") as batch_op:
        if _has_unique_constraint(conn, "gift_codes", "uq_gift_codes_code"):
            batch_op.drop_constraint("uq_gift_codes_code", type_="unique")
        batch_op.alter_column("source_created_at", nullable=False)
        if not _has_unique_constraint(conn, "gift_codes", "uq_gift_codes_code_created"):
            batch_op.create_unique_constraint("uq_gift_codes_code_created", ["code", "source_created_at"])


def downgrade() -> None:
    conn = op.get_bind()
    with op.batch_alter_table("gift_codes") as batch_op:
        if _has_unique_constraint(conn, "gift_codes", "uq_gift_codes_code_created"):
            batch_op.drop_constraint("uq_gift_codes_code_created", type_="unique")
        if not _has_unique_constraint(conn, "gift_codes", "uq_gift_codes_code"):
            batch_op.create_unique_constraint("uq_gift_codes_code", ["code"])
        batch_op.drop_column("source_created_at")
