"""offline billing, phase 1: module key, devices as records, sale ids on orders, the business's offline switch

Groundwork only; nothing bills offline after this. Adds the `offline_billing` module key (given to the internal all-modules
legacy plan only), the branch-scoped `devices` table with the same row-level security policy as every other tenant table,
`orders.client_sale_id` with a per-branch unique index, and the business's offline switch and lease limits on `organizations`.

Revision ID: a7d2c9e54f16
Revises: f6c1a8d43e95
Create Date: 2026-11-13 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a7d2c9e54f16'
down_revision: Union[str, None] = 'f6c1a8d43e95'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_KEYS = "'pos', 'analytics', 'kds', 'inventory'"
NEW_KEYS = "'pos', 'analytics', 'kds', 'inventory', 'offline_billing'"
MODULE_CHECKS = (
    ('plan_modules', 'ck_plan_modules_module_key'),
    ('organization_modules', 'ck_organization_modules_module_key'),
    ('platform_modules', 'ck_platform_modules_module_key'),
)
SCOPE = 'app_org_scope() IS NULL OR branch_id IN (SELECT id FROM branches WHERE organization_id = app_org_scope())'
ORG_CHECKS = (
    ('ck_organizations_offline_max_hours', 'offline_max_hours BETWEEN 1 AND 72'),
    ('ck_organizations_offline_max_sales', 'offline_max_sales BETWEEN 1 AND 2000'),
    ('ck_organizations_offline_max_cash', 'offline_max_cash > 0 AND offline_max_cash <= 500000'),
)


def _recreate(keys: str) -> None:
    for table, name in MODULE_CHECKS:
        op.drop_constraint(name, table, type_='check')
        op.create_check_constraint(name, table, f"module_key IN ({keys})")


def upgrade() -> None:
    op.create_table(
        'devices',
        sa.Column('branch_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('device_id', sa.String(length=100), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=True),
        sa.Column('offline_enabled', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('series_letter', sa.String(length=1), nullable=True),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_by_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.CheckConstraint("series_letter IS NULL OR series_letter ~ '^[A-Z]$'", name='ck_devices_series_letter'),
        sa.CheckConstraint('NOT offline_enabled OR (series_letter IS NOT NULL AND revoked_at IS NULL)',
                           name='ck_devices_offline_needs_series'),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id']),
        sa.ForeignKeyConstraint(['revoked_by_id'], ['staff_users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_id'),
    )
    op.create_index(op.f('ix_devices_branch_id'), 'devices', ['branch_id'], unique=False)
    op.create_index('uq_devices_branch_series', 'devices', ['branch_id', 'series_letter'], unique=True,
                    postgresql_where=sa.text('series_letter IS NOT NULL'))
    op.execute('ALTER TABLE "devices" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "devices" FORCE ROW LEVEL SECURITY')
    op.execute(f'CREATE POLICY tenant_scope ON "devices" USING ({SCOPE}) WITH CHECK ({SCOPE})')

    op.add_column('orders', sa.Column('client_sale_id', sa.String(length=64), nullable=True))
    op.create_index('uq_orders_branch_client_sale', 'orders', ['branch_id', 'client_sale_id'], unique=True,
                    postgresql_where=sa.text('client_sale_id IS NOT NULL'))

    op.add_column('organizations', sa.Column('offline_allowed', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('organizations', sa.Column('offline_max_hours', sa.Integer(), server_default='24', nullable=False))
    op.add_column('organizations', sa.Column('offline_max_sales', sa.Integer(), server_default='300', nullable=False))
    op.add_column('organizations', sa.Column('offline_max_cash', sa.Numeric(precision=10, scale=2),
                                             server_default='50000.00', nullable=False))
    for name, condition in ORG_CHECKS:
        op.create_check_constraint(name, 'organizations', condition)

    _recreate(NEW_KEYS)
    op.execute("""
        INSERT INTO plan_modules (plan_id, module_key)
        SELECT p.id, 'offline_billing' FROM plans p
        WHERE p.code = 'legacy_all_modules'
          AND NOT EXISTS (SELECT 1 FROM plan_modules x WHERE x.plan_id = p.id AND x.module_key = 'offline_billing')
    """)


def downgrade() -> None:
    op.execute("DELETE FROM plan_modules WHERE module_key = 'offline_billing'")
    op.execute("DELETE FROM organization_modules WHERE module_key = 'offline_billing'")
    op.execute("DELETE FROM platform_modules WHERE module_key = 'offline_billing'")
    _recreate(OLD_KEYS)

    for name, _ in ORG_CHECKS:
        op.drop_constraint(name, 'organizations', type_='check')
    for column in ('offline_max_cash', 'offline_max_sales', 'offline_max_hours', 'offline_allowed'):
        op.drop_column('organizations', column)

    op.drop_index('uq_orders_branch_client_sale', table_name='orders')
    op.drop_column('orders', 'client_sale_id')

    op.drop_table('devices')
