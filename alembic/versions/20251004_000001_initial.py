"""initial schema

Revision ID: 20251004_000001
Revises: 
Create Date: 2025-10-04 00:00:01

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20251004_000001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # alliances
    op.create_table(
        'alliances',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('tag', sa.String(length=3), nullable=False),
        sa.Column('quota', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('tag', name='uq_alliance_tag'),
    )
    op.create_index('ix_alliance_name', 'alliances', ['name'])

    # users
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('fid', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=True),
        sa.Column('alliance_id', sa.Integer(), sa.ForeignKey('alliances.id', ondelete='SET NULL'), nullable=True),
        sa.Column('state', sa.String(length=50), nullable=True),
        sa.Column('rank', sa.String(length=10), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('fid', name='uq_users_fid'),
    )
    op.create_index('ix_users_fid', 'users', ['fid'])
    op.create_index('ix_users_alliance', 'users', ['alliance_id'])

    # web_accounts
    op.create_table(
        'web_accounts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('username', sa.String(length=120), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False, server_default=sa.text("'manager'")),
        sa.Column('alliance_id', sa.Integer(), sa.ForeignKey('alliances.id', ondelete='SET NULL'), nullable=True),
        sa.Column('alliance_rank', sa.String(length=2), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.UniqueConstraint('username', name='uq_web_accounts_username'),
    )

    # gift_codes
    op.create_table(
        'gift_codes',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('source_url', sa.String(length=500), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('first_seen_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('expires_at', sa.DateTime(), nullable=True),
        sa.UniqueConstraint('code', name='uq_gift_codes_code'),
    )
    op.create_index('ix_gift_codes_active', 'gift_codes', ['active'])

    # redemptions
    op.create_table(
        'redemptions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('gift_code_id', sa.Integer(), sa.ForeignKey('gift_codes.id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False, server_default=sa.text("'pending'")),
        sa.Column('captcha', sa.String(length=8), nullable=True),
        sa.Column('rewards', sa.Text(), nullable=True),
        sa.Column('result_msg', sa.Text(), nullable=True),
        sa.Column('err_code', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_at', sa.DateTime(), nullable=True, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('last_attempt_at', sa.DateTime(), nullable=True),
        sa.Column('attempt_count', sa.Integer(), nullable=False, server_default='0'),
        sa.UniqueConstraint('user_id', 'gift_code_id', name='uq_user_code'),
    )
    op.create_index('ix_redemptions_status', 'redemptions', ['status'])

    # redemption_attempts
    op.create_table(
        'redemption_attempts',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('redemption_id', sa.Integer(), sa.ForeignKey('redemptions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('attempt_no', sa.Integer(), nullable=False),
        sa.Column('captcha', sa.String(length=8), nullable=True),
        sa.Column('result_msg', sa.Text(), nullable=True),
        sa.Column('err_code', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
    )


def downgrade() -> None:
    op.drop_table('redemption_attempts')
    op.drop_index('ix_redemptions_status', table_name='redemptions')
    op.drop_table('redemptions')
    op.drop_index('ix_gift_codes_active', table_name='gift_codes')
    op.drop_table('gift_codes')
    op.drop_table('web_accounts')
    op.drop_index('ix_users_alliance', table_name='users')
    op.drop_index('ix_users_fid', table_name='users')
    op.drop_table('users')
    op.drop_index('ix_alliance_name', table_name='alliances')
    op.drop_table('alliances')
