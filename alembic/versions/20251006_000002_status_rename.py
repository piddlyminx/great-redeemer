"""rename success->redeemed_new and add redeemed_already

Revision ID: 20251006_000002
Revises: 20251004_000001_initial
Create Date: 2025-10-06 00:00:00.000000
"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "20251006_000002"
down_revision = "20251004_000001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Data-only migration: map legacy 'success' to 'redeemed_new'
    op.execute("UPDATE redemptions SET status='redeemed_new' WHERE status='success'")


def downgrade() -> None:
    # Best-effort reverse
    op.execute("UPDATE redemptions SET status='success' WHERE status='redeemed_new'")

