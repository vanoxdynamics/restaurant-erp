"""platform api: organization profile, plan prices, scheduled term changes, subscription events, module kill switch

Revision ID: f1b6d3a82c47
Revises: e8a3c5d71b29
Create Date: 2026-11-03 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f1b6d3a82c47'
down_revision: Union[str, None] = 'e8a3c5d71b29'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MODULE_CHECK = "'pos', 'analytics'"


def upgrade() -> None:
    op.add_column('organizations', sa.Column('legal_name', sa.String(length=200), nullable=True))
    op.add_column('organizations', sa.Column('billing_email', sa.String(length=254), nullable=True))
    op.add_column('organizations', sa.Column('country', sa.String(length=2), nullable=True))
    op.add_column('organizations', sa.Column('suspended_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('organizations', sa.Column('suspension_reason', sa.String(length=500), nullable=True))

    op.add_column('plans', sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False))
    op.add_column('plans', sa.Column('price_monthly', sa.Numeric(12, 2), nullable=True))
    op.add_column('plans', sa.Column('price_yearly', sa.Numeric(12, 2), nullable=True))

    op.add_column('subscriptions', sa.Column('pending_billing_term', sa.String(length=20), nullable=True))
    op.add_column('subscriptions', sa.Column('pending_price_amount', sa.Numeric(12, 2), nullable=True))
    op.create_check_constraint('ck_subscriptions_pending_billing_term', 'subscriptions',
                               "pending_billing_term IS NULL OR pending_billing_term IN ('monthly', 'yearly')")

    op.create_table(
        'subscription_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('event_type', sa.String(length=40), nullable=False),
        sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('actor_admin_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_subscription_events_organization_id'), 'subscription_events', ['organization_id'])

    op.create_table(
        'platform_modules',
        sa.Column('module_key', sa.String(length=40), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('reason', sa.String(length=255), nullable=True),
        sa.Column('set_by', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint(f"module_key IN ({MODULE_CHECK})", name='ck_platform_modules_module_key'),
        sa.PrimaryKeyConstraint('module_key'),
    )


def downgrade() -> None:
    op.drop_table('platform_modules')
    op.drop_index(op.f('ix_subscription_events_organization_id'), table_name='subscription_events')
    op.drop_table('subscription_events')
    op.drop_constraint('ck_subscriptions_pending_billing_term', 'subscriptions', type_='check')
    op.drop_column('subscriptions', 'pending_price_amount')
    op.drop_column('subscriptions', 'pending_billing_term')
    op.drop_column('plans', 'price_yearly')
    op.drop_column('plans', 'price_monthly')
    op.drop_column('plans', 'currency')
    for col in ('suspension_reason', 'suspended_at', 'country', 'billing_email', 'legal_name'):
        op.drop_column('organizations', col)
