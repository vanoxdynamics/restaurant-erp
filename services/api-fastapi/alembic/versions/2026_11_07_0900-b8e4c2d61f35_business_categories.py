"""business categories, shop items and decimal quantities

Organizations get a category (restaurant, cafe, grocery, retail) and their workflow-setting overrides. Items get barcode, SKU,
HSN code, unit and "sold by weight". Order and KOT quantities become decimals with 3 places (0.750 kg), order lines snapshot the
unit and HSN code, orders get a daily token number, and `counter_sale` becomes an order type. Existing data keeps its meaning:
every existing organization is a restaurant, every existing item a piece.

Revision ID: b8e4c2d61f35
Revises: a7d3f5c19e42
Create Date: 2026-11-07 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b8e4c2d61f35'
down_revision: Union[str, None] = 'a7d3f5c19e42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_ORDER_TYPES = "'dine_in', 'takeaway', 'delivery'"
NEW_ORDER_TYPES = OLD_ORDER_TYPES + ", 'counter_sale'"


def upgrade() -> None:
    op.add_column('organizations', sa.Column('category', sa.String(length=20), server_default='restaurant', nullable=False))
    op.add_column('organizations', sa.Column('settings', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False))
    op.create_check_constraint('ck_organizations_category', 'organizations',
                               "category IN ('restaurant', 'cafe', 'grocery', 'retail')")

    op.add_column('menu_items', sa.Column('barcode', sa.String(length=64), nullable=True))
    op.add_column('menu_items', sa.Column('sku', sa.String(length=64), nullable=True))
    op.add_column('menu_items', sa.Column('hsn_code', sa.String(length=8), nullable=True))
    op.add_column('menu_items', sa.Column('unit', sa.String(length=10), server_default='pcs', nullable=False))
    op.add_column('menu_items', sa.Column('sold_by_weight', sa.Boolean(), server_default='false', nullable=False))
    op.create_check_constraint('ck_menu_items_unit', 'menu_items', "unit IN ('pcs', 'kg', 'g', 'l', 'ml')")
    op.create_index('uq_menu_items_org_barcode', 'menu_items', ['organization_id', 'barcode'], unique=True,
                    postgresql_where=sa.text('barcode IS NOT NULL'))
    op.create_index('uq_menu_items_org_sku', 'menu_items', ['organization_id', 'sku'], unique=True,
                    postgresql_where=sa.text('sku IS NOT NULL'))

    op.alter_column('order_items', 'quantity', type_=sa.Numeric(10, 3), existing_type=sa.Integer(),
                    postgresql_using='quantity::numeric(10,3)')
    op.alter_column('kot_items', 'quantity', type_=sa.Numeric(10, 3), existing_type=sa.Integer(),
                    postgresql_using='quantity::numeric(10,3)')
    op.add_column('order_items', sa.Column('unit_snapshot', sa.String(length=10), server_default='pcs', nullable=False))
    op.add_column('order_items', sa.Column('hsn_snapshot', sa.String(length=8), nullable=True))

    op.add_column('orders', sa.Column('token_number', sa.Integer(), nullable=True))
    op.drop_constraint('ck_orders_order_type', 'orders', type_='check')
    op.create_check_constraint('ck_orders_order_type', 'orders', f"order_type IN ({NEW_ORDER_TYPES})")


def downgrade() -> None:
    op.execute("UPDATE orders SET order_type = 'takeaway' WHERE order_type = 'counter_sale'")
    op.drop_constraint('ck_orders_order_type', 'orders', type_='check')
    op.create_check_constraint('ck_orders_order_type', 'orders', f"order_type IN ({OLD_ORDER_TYPES})")
    op.drop_column('orders', 'token_number')

    op.drop_column('order_items', 'hsn_snapshot')
    op.drop_column('order_items', 'unit_snapshot')
    # Decimal quantities cannot be represented as integers; round up so no line becomes zero.
    op.alter_column('kot_items', 'quantity', type_=sa.Integer(), existing_type=sa.Numeric(10, 3),
                    postgresql_using='ceil(quantity)::integer')
    op.alter_column('order_items', 'quantity', type_=sa.Integer(), existing_type=sa.Numeric(10, 3),
                    postgresql_using='ceil(quantity)::integer')

    op.drop_index('uq_menu_items_org_sku', table_name='menu_items')
    op.drop_index('uq_menu_items_org_barcode', table_name='menu_items')
    op.drop_constraint('ck_menu_items_unit', 'menu_items', type_='check')
    for column in ('sold_by_weight', 'unit', 'hsn_code', 'sku', 'barcode'):
        op.drop_column('menu_items', column)

    op.drop_constraint('ck_organizations_category', 'organizations', type_='check')
    op.drop_column('organizations', 'settings')
    op.drop_column('organizations', 'category')
