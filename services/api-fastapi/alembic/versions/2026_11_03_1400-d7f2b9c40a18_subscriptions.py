"""subscriptions and module entitlements

Plans, per-organization subscriptions (monthly or yearly term) and module overrides. Every existing
organization is put on a non-expiring "legacy" plan that includes all modules, so nothing is switched off.

Revision ID: d7f2b9c40a18
Revises: c5e8a1b7d204
Create Date: 2026-11-03 14:00:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd7f2b9c40a18'
down_revision: Union[str, None] = 'c5e8a1b7d204'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

MODULES = ("pos", "analytics")
MODULE_CHECK = ", ".join(f"'{m}'" for m in MODULES)
STATUSES = "'trialing', 'active', 'past_due', 'paused', 'cancelled', 'expired'"
TERMS = "'monthly', 'yearly'"


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False)


def upgrade() -> None:
    op.create_table(
        'plans',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('code', sa.String(length=40), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('max_branches', sa.Integer(), nullable=True),
        sa.Column('max_staff', sa.Integer(), nullable=True),
        _ts('created_at'), _ts('updated_at'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_plans_code'), 'plans', ['code'], unique=True)

    op.create_table(
        'plan_modules',
        sa.Column('plan_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('module_key', sa.String(length=40), nullable=False),
        sa.CheckConstraint(f"module_key IN ({MODULE_CHECK})", name='ck_plan_modules_module_key'),
        sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('plan_id', 'module_key'),
    )

    op.create_table(
        'subscriptions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('plan_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('billing_term', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('price_amount', sa.Numeric(12, 2), server_default='0', nullable=False),
        sa.Column('currency', sa.String(length=3), server_default='INR', nullable=False),
        _ts('started_at'), _ts('current_period_start'),
        sa.Column('current_period_end', sa.DateTime(timezone=True), nullable=True),
        sa.Column('trial_ends_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('grace_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('auto_renew', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('cancel_at_period_end', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('max_branches_override', sa.Integer(), nullable=True),
        sa.Column('max_staff_override', sa.Integer(), nullable=True),
        sa.Column('version', sa.Integer(), server_default='1', nullable=False),
        _ts('created_at'), _ts('updated_at'),
        sa.CheckConstraint(f"billing_term IN ({TERMS})", name='ck_subscriptions_billing_term'),
        sa.CheckConstraint(f"status IN ({STATUSES})", name='ck_subscriptions_status'),
        sa.CheckConstraint("price_amount >= 0", name='ck_subscriptions_price_amount'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['plan_id'], ['plans.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('organization_id'),
    )
    op.create_index(op.f('ix_subscriptions_plan_id'), 'subscriptions', ['plan_id'], unique=False)

    op.create_table(
        'organization_modules',
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('module_key', sa.String(length=40), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('reason', sa.String(length=255), nullable=True),
        sa.Column('set_by', postgresql.UUID(as_uuid=True), nullable=True),
        _ts('created_at'), _ts('updated_at'),
        sa.CheckConstraint(f"module_key IN ({MODULE_CHECK})", name='ck_organization_modules_module_key'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('organization_id', 'module_key'),
    )

    # Existing organizations keep working: legacy plan, every module, no expiry.
    bind = op.get_bind()
    org_ids = [r[0] for r in bind.execute(sa.text("SELECT id FROM organizations")).fetchall()]
    if org_ids:
        plan_id = uuid.uuid4()
        bind.execute(
            sa.text("INSERT INTO plans (id, code, name) VALUES (:id, 'legacy_all_modules', 'Legacy (all modules)')"),
            {"id": plan_id},
        )
        for key in MODULES:
            bind.execute(sa.text("INSERT INTO plan_modules (plan_id, module_key) VALUES (:p, :k)"), {"p": plan_id, "k": key})
        for org_id in org_ids:
            bind.execute(
                sa.text("INSERT INTO subscriptions (id, organization_id, plan_id, billing_term, status) "
                        "VALUES (:id, :o, :p, 'yearly', 'active')"),
                {"id": uuid.uuid4(), "o": org_id, "p": plan_id},
            )


def downgrade() -> None:
    op.drop_table('organization_modules')
    op.drop_index(op.f('ix_subscriptions_plan_id'), table_name='subscriptions')
    op.drop_table('subscriptions')
    op.drop_table('plan_modules')
    op.drop_index(op.f('ix_plans_code'), table_name='plans')
    op.drop_table('plans')
