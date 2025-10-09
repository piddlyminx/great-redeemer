"""add performance indexes

Revision ID: 20251007_000001
Revises: 20251004_000001
Create Date: 2025-10-07 00:00:01

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251007_000001'
down_revision = '20251004_000001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add indexes for columns frequently used in WHERE clauses and JOINs
    # These indexes significantly improve performance of eligibility queries

    # Redemptions table indexes for filtering
    op.create_index('ix_redemptions_last_attempt_at', 'redemptions', ['last_attempt_at'])
    op.create_index('ix_redemptions_attempt_count', 'redemptions', ['attempt_count'])

    # RedemptionAttempts table index for foreign key lookups
    op.create_index('ix_redemption_attempts_redemption_id', 'redemption_attempts', ['redemption_id'])

    # Composite index for common query pattern (user_id, gift_code_id lookups)
    # This is already covered by the unique constraint, but making it explicit
    # op.create_index('ix_redemptions_user_code', 'redemptions', ['user_id', 'gift_code_id'])
    # Note: The unique constraint on (user_id, gift_code_id) already serves as an index

    # GiftCodes table index for ordering
    op.create_index('ix_gift_codes_first_seen_at', 'gift_codes', ['first_seen_at'])

    # Users table index for active filtering
    op.create_index('ix_users_active', 'users', ['active'])


def downgrade() -> None:
    # Drop indexes in reverse order
    op.drop_index('ix_users_active', table_name='users')
    op.drop_index('ix_gift_codes_first_seen_at', table_name='gift_codes')
    op.drop_index('ix_redemption_attempts_redemption_id', table_name='redemption_attempts')
    op.drop_index('ix_redemptions_attempt_count', table_name='redemptions')
    op.drop_index('ix_redemptions_last_attempt_at', table_name='redemptions')
