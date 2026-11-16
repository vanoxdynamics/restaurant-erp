"""dress shops, stage A1: settings, categories, products (styles) and variants (size x colour)

New tables only (`apparel_*`), each scoped to its organization with the `tenant_scope` row-level security policy. No existing
table is touched.

Revision ID: c3d5f8a14b27
Revises: b1c4e7a92d05
Create Date: 2026-11-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c3d5f8a14b27'
down_revision: Union[str, None] = 'b1c4e7a92d05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ORG_SCOPE = 'app_org_scope() IS NULL OR organization_id = app_org_scope()'
TABLES = ('apparel_settings', 'apparel_categories', 'apparel_products', 'apparel_variants')


def _timestamps() -> list:
    return [
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        'apparel_settings',
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('prices_include_tax', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('round_to_rupee', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('low_rate', sa.Numeric(5, 2), server_default='5.00', nullable=False),
        sa.Column('slab_price', sa.Numeric(10, 2), server_default='2500.00', nullable=False),
        sa.Column('high_rate', sa.Numeric(5, 2), server_default='18.00', nullable=False),
        sa.Column('return_days', sa.Integer(), server_default='7', nullable=False),
        sa.Column('next_barcode', sa.BigInteger(), server_default='1', nullable=False),
        *_timestamps(),
        sa.CheckConstraint('low_rate >= 0 AND low_rate <= 40 AND high_rate >= 0 AND high_rate <= 40',
                           name='ck_apparel_settings_rates'),
        sa.CheckConstraint('slab_price >= 0', name='ck_apparel_settings_slab_price'),
        sa.CheckConstraint('return_days >= 0', name='ck_apparel_settings_return_days'),
        sa.CheckConstraint('next_barcode >= 1 AND next_barcode < 10000000000', name='ck_apparel_settings_next_barcode'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('organization_id'),
    )

    op.create_table(
        'apparel_categories',
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_apparel_categories_organization_id'), 'apparel_categories', ['organization_id'])
    op.create_index('uq_apparel_categories_org_name', 'apparel_categories', ['organization_id', sa.text('lower(name)')],
                    unique=True)

    op.create_table(
        'apparel_products',
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('category_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('name', sa.String(length=150), nullable=False),
        sa.Column('brand', sa.String(length=100), nullable=True),
        sa.Column('style_code', sa.String(length=64), nullable=True),
        sa.Column('hsn_code', sa.String(length=8), nullable=True),
        sa.Column('tax_mode', sa.String(length=10), server_default='slab', nullable=False),
        sa.Column('tax_rate', sa.Numeric(5, 2), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        *_timestamps(),
        sa.CheckConstraint("tax_mode IN ('slab', 'fixed')", name='ck_apparel_products_tax_mode'),
        sa.CheckConstraint("tax_mode <> 'fixed' OR tax_rate IS NOT NULL", name='ck_apparel_products_fixed_rate'),
        sa.CheckConstraint('tax_rate IS NULL OR (tax_rate >= 0 AND tax_rate <= 40)', name='ck_apparel_products_tax_rate'),
        sa.CheckConstraint("hsn_code IS NULL OR hsn_code ~ '^[0-9]{4,8}$'", name='ck_apparel_products_hsn_code'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['category_id'], ['apparel_categories.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_apparel_products_organization_id'), 'apparel_products', ['organization_id'])
    op.create_index(op.f('ix_apparel_products_category_id'), 'apparel_products', ['category_id'])
    op.create_index('ix_apparel_products_org_name', 'apparel_products', ['organization_id', 'name'])
    op.create_index('uq_apparel_products_org_style', 'apparel_products', ['organization_id', 'style_code'], unique=True,
                    postgresql_where=sa.text('style_code IS NOT NULL'))

    op.create_table(
        'apparel_variants',
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('product_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('size', sa.String(length=20), nullable=False),
        sa.Column('colour', sa.String(length=40), nullable=False),
        sa.Column('sku', sa.String(length=64), nullable=True),
        sa.Column('barcode', sa.String(length=64), nullable=True),
        sa.Column('mrp', sa.Numeric(10, 2), nullable=True),
        sa.Column('price', sa.Numeric(10, 2), nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False),
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        *_timestamps(),
        sa.CheckConstraint('price >= 0 AND (mrp IS NULL OR price <= mrp)', name='ck_apparel_variants_price'),
        sa.CheckConstraint("size <> '' AND colour <> ''", name='ck_apparel_variants_size_colour'),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['product_id'], ['apparel_products.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('product_id', 'size', 'colour', name='uq_apparel_variants_product_size_colour'),
    )
    op.create_index(op.f('ix_apparel_variants_organization_id'), 'apparel_variants', ['organization_id'])
    op.create_index(op.f('ix_apparel_variants_product_id'), 'apparel_variants', ['product_id'])
    op.create_index('uq_apparel_variants_org_sku', 'apparel_variants', ['organization_id', 'sku'], unique=True,
                    postgresql_where=sa.text('sku IS NOT NULL'))
    op.create_index('uq_apparel_variants_org_barcode', 'apparel_variants', ['organization_id', 'barcode'], unique=True,
                    postgresql_where=sa.text('barcode IS NOT NULL'))

    for table in TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f'CREATE POLICY tenant_scope ON "{table}" USING ({ORG_SCOPE}) WITH CHECK ({ORG_SCOPE})')


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f'DROP POLICY IF EXISTS tenant_scope ON "{table}"')
        op.drop_table(table)
