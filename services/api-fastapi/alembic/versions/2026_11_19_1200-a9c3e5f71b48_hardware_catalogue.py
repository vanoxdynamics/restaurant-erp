"""hardware shops, stage H1: settings, categories, products and their alternate units

New tables only (`hardware_*`), each scoped to its organization with the `tenant_scope` row-level security policy. No existing
table is touched.

Revision ID: a9c3e5f71b48
Revises: f6a1d3b8c924
Create Date: 2026-11-19 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a9c3e5f71b48'
down_revision: Union[str, None] = 'f6a1d3b8c924'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ORG_SCOPE = 'app_org_scope() IS NULL OR organization_id = app_org_scope()'
TABLES = ('hardware_settings', 'hardware_categories', 'hardware_products', 'hardware_product_units')
UNITS = "'pcs', 'box', 'pkt', 'dozen', 'set', 'pair', 'bag', 'bundle', 'roll', 'coil', 'kg', 'g', 'm', 'ft', 'sqft', 'l', 'ml'"


def _timestamps() -> list:
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    ]


def _uuid(name: str, nullable: bool = False) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        'hardware_settings',
        _uuid('organization_id'),
        sa.Column('prices_include_tax', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('round_to_rupee', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('default_tax_rate', sa.Numeric(5, 2), server_default='18.00', nullable=False),
        sa.Column('estimate_valid_days', sa.Integer(), server_default='15', nullable=False),
        sa.Column('state_code', sa.String(length=2), nullable=True),
        *_timestamps(),
        sa.CheckConstraint('default_tax_rate >= 0 AND default_tax_rate <= 40', name='ck_hardware_settings_default_tax_rate'),
        sa.CheckConstraint('estimate_valid_days >= 1 AND estimate_valid_days <= 365', name='ck_hardware_settings_estimate_days'),
        sa.CheckConstraint("state_code IS NULL OR state_code ~ '^[0-9]{2}$'", name='ck_hardware_settings_state_code'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('organization_id'),
    )

    op.create_table(
        'hardware_categories',
        _uuid('organization_id'),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        _uuid('id'),
        *_timestamps(),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_hardware_categories_organization_id'), 'hardware_categories', ['organization_id'])
    op.create_index('uq_hardware_categories_org_name', 'hardware_categories', ['organization_id', sa.text('lower(name)')],
                    unique=True)

    op.create_table(
        'hardware_products',
        _uuid('organization_id'), _uuid('category_id'),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('brand', sa.String(length=100), nullable=True),
        sa.Column('size_spec', sa.String(length=60), nullable=True),
        sa.Column('sku', sa.String(length=64), nullable=True),
        sa.Column('barcode', sa.String(length=64), nullable=True),
        sa.Column('hsn_code', sa.String(length=8), nullable=True),
        sa.Column('tax_rate', sa.Numeric(5, 2), nullable=False),
        sa.Column('base_unit', sa.String(length=10), nullable=False),
        sa.Column('allow_fraction', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('price', sa.Numeric(12, 4), nullable=False),
        sa.Column('wholesale_price', sa.Numeric(12, 4), nullable=True),
        sa.Column('min_price', sa.Numeric(12, 4), nullable=True),
        sa.Column('mrp', sa.Numeric(12, 4), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        _uuid('id'),
        *_timestamps(),
        sa.CheckConstraint(f'base_unit IN ({UNITS})', name='ck_hardware_products_base_unit'),
        sa.CheckConstraint('tax_rate >= 0 AND tax_rate <= 40', name='ck_hardware_products_tax_rate'),
        sa.CheckConstraint("hsn_code IS NULL OR hsn_code ~ '^[0-9]{4,8}$'", name='ck_hardware_products_hsn_code'),
        sa.CheckConstraint('price >= 0 AND (wholesale_price IS NULL OR wholesale_price >= 0) '
                           'AND (min_price IS NULL OR min_price >= 0) AND (mrp IS NULL OR mrp >= 0)',
                           name='ck_hardware_products_prices'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['category_id'], ['hardware_categories.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_hardware_products_organization_id'), 'hardware_products', ['organization_id'])
    op.create_index(op.f('ix_hardware_products_category_id'), 'hardware_products', ['category_id'])
    op.create_index('ix_hardware_products_org_name', 'hardware_products', ['organization_id', 'name'])
    op.create_index('uq_hardware_products_org_sku', 'hardware_products', ['organization_id', 'sku'], unique=True,
                    postgresql_where=sa.text('sku IS NOT NULL'))
    op.create_index('uq_hardware_products_org_barcode', 'hardware_products', ['organization_id', 'barcode'], unique=True,
                    postgresql_where=sa.text('barcode IS NOT NULL'))

    op.create_table(
        'hardware_product_units',
        _uuid('organization_id'), _uuid('product_id'),
        sa.Column('unit', sa.String(length=10), nullable=False),
        sa.Column('factor', sa.Numeric(12, 4), nullable=False),
        sa.Column('price', sa.Numeric(10, 2), nullable=True),
        sa.Column('wholesale_price', sa.Numeric(10, 2), nullable=True),
        sa.Column('min_price', sa.Numeric(10, 2), nullable=True),
        sa.Column('barcode', sa.String(length=64), nullable=True),
        _uuid('id'),
        *_timestamps(),
        sa.CheckConstraint(f'unit IN ({UNITS})', name='ck_hardware_product_units_unit'),
        sa.CheckConstraint('factor > 0', name='ck_hardware_product_units_factor'),
        sa.CheckConstraint('(price IS NULL OR price >= 0) AND (wholesale_price IS NULL OR wholesale_price >= 0) '
                           'AND (min_price IS NULL OR min_price >= 0)', name='ck_hardware_product_units_prices'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['product_id'], ['hardware_products.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('product_id', 'unit', name='uq_hardware_product_units_product_unit'),
    )
    op.create_index(op.f('ix_hardware_product_units_organization_id'), 'hardware_product_units', ['organization_id'])
    op.create_index(op.f('ix_hardware_product_units_product_id'), 'hardware_product_units', ['product_id'])
    op.create_index('uq_hardware_product_units_org_barcode', 'hardware_product_units', ['organization_id', 'barcode'],
                    unique=True, postgresql_where=sa.text('barcode IS NOT NULL'))

    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_scope ON "{table}" USING ({ORG_SCOPE}) WITH CHECK ({ORG_SCOPE})')


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f'DROP POLICY IF EXISTS tenant_scope ON "{table}"')
        op.drop_table(table)
