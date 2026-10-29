"""integrity and feature gaps

Adds status CHECK constraints, missing foreign keys, branch-scoped document numbers,
modifier linkage, KOT stations, audit log, document counters, daily reports,
PIN lookup and optimistic-lock support.

Revision ID: 5aeda782303f
Revises: e54e65c309dc
Create Date: 2026-10-28 12:40:20.078769

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '5aeda782303f'
down_revision: Union[str, None] = 'e54e65c309dc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen copies of the enum values at the time of this revision.
CHECKS = {
    ("dining_tables", "status"): ["vacant", "occupied", "billed", "reserved"],
    ("menu_items", "station"): ["kitchen", "bar", "grill", "dessert"],
    ("orders", "status"): ["active", "billed", "completed", "cancelled"],
    ("orders", "order_type"): ["dine_in", "takeaway", "delivery"],
    ("order_items", "status"): ["ordered", "fired", "preparing", "ready", "served", "cancelled"],
    ("kots", "status"): ["queued", "preparing", "ready", "served", "voided"],
    ("kots", "station"): ["kitchen", "bar", "grill", "dessert"],
    ("bills", "status"): ["unpaid", "partially_paid", "paid", "voided", "refunded"],
    ("payments", "payment_mode"): ["cash", "card", "upi"],
    ("payments", "status"): ["pending", "success", "failed"],
    ("staff_users", "role"): ["owner", "manager", "captain", "waiter", "cashier", "kitchen"],
    ("shifts", "status"): ["open", "closed"],
    ("audit_logs", "action_type"): [
        "kot_void", "order_cancel", "order_item_cancel", "bill_void", "high_discount",
        "table_transfer", "table_merge", "login_lockout", "cash_variance",
    ],
}

PLAIN_CHECKS = [
    ("menu_items", "ck_menu_items_base_price", "base_price >= 0"),
    ("menu_items", "ck_menu_items_tax_rate", "tax_rate >= 0 AND tax_rate <= 28"),
    ("item_variants", "ck_item_variants_price", "price >= 0"),
    ("modifier_groups", "ck_modifier_groups_selection", "min_selection >= 0 AND max_selection >= min_selection"),
    ("modifier_items", "ck_modifier_items_price", "price >= 0"),
    ("orders", "ck_orders_guest_count", "guest_count >= 1"),
    ("order_items", "ck_order_items_quantity", "quantity > 0"),
    ("kot_items", "ck_kot_items_quantity", "quantity > 0"),
    ("payments", "ck_payments_amount", "amount > 0"),
    ("bills", "ck_bills_non_negative",
     "subtotal >= 0 AND tax_cgst >= 0 AND tax_sgst >= 0 AND discount_amount >= 0 "
     "AND grand_total >= 0 AND paid_amount >= 0"),
    ("bills", "ck_bills_discount_le_subtotal", "discount_amount <= subtotal"),
]

NEW_FKS = [
    ("fk_dining_tables_merged_into_table_id", "dining_tables", "dining_tables", "merged_into_table_id", "SET NULL"),
    ("fk_kots_branch_id", "kots", "branches", "branch_id", None),
    ("fk_kots_fired_by_id", "kots", "staff_users", "fired_by_id", None),
    ("fk_kots_voided_by_id", "kots", "staff_users", "voided_by_id", None),
    ("fk_kots_voided_approved_by_id", "kots", "staff_users", "voided_approved_by_id", None),
    ("fk_order_items_variant_id", "order_items", "item_variants", "variant_id", None),
    ("fk_orders_waiter_id", "orders", "staff_users", "waiter_id", None),
    ("fk_orders_opened_by_shift_id", "orders", "shifts", "opened_by_shift_id", None),
    ("fk_bills_branch_id", "bills", "branches", "branch_id", None),
    ("fk_bills_discount_approved_by_id", "bills", "staff_users", "discount_approved_by_id", None),
    ("fk_bills_voided_by_id", "bills", "staff_users", "voided_by_id", None),
    ("fk_payments_received_by_id", "payments", "staff_users", "received_by_id", None),
    ("fk_payments_shift_id", "payments", "shifts", "shift_id", None),
]


def upgrade() -> None:
    # --- New tables -------------------------------------------------------
    op.create_table(
        'audit_logs',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('branch_id', sa.UUID(), nullable=False),
        sa.Column('action_type', sa.String(length=40), nullable=False),
        sa.Column('actor_id', sa.UUID(), nullable=True),
        sa.Column('approver_id', sa.UUID(), nullable=True),
        sa.Column('entity_type', sa.String(length=40), nullable=False),
        sa.Column('entity_id', sa.UUID(), nullable=True),
        sa.Column('amount', sa.Numeric(precision=10, scale=2), nullable=True),
        sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], name='fk_audit_logs_branch_id'),
        sa.ForeignKeyConstraint(['actor_id'], ['staff_users.id'], name='fk_audit_logs_actor_id'),
        sa.ForeignKeyConstraint(['approver_id'], ['staff_users.id'], name='fk_audit_logs_approver_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_audit_logs_branch_created', 'audit_logs', ['branch_id', 'created_at'], unique=False)

    op.create_table(
        'document_counters',
        sa.Column('branch_id', sa.UUID(), nullable=False),
        sa.Column('doc_type', sa.String(length=20), nullable=False),
        sa.Column('period', sa.String(length=10), nullable=False),
        sa.Column('value', sa.BigInteger(), server_default='0', nullable=False),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], name='fk_document_counters_branch_id'),
        sa.PrimaryKeyConstraint('branch_id', 'doc_type', 'period'),
    )

    op.create_table(
        'daily_reports',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('branch_id', sa.UUID(), nullable=False),
        sa.Column('report_date', sa.Date(), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('generated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['branch_id'], ['branches.id'], name='fk_daily_reports_branch_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('uq_daily_reports_branch_date', 'daily_reports', ['branch_id', 'report_date'], unique=True)

    op.create_table(
        'menu_item_modifier_groups',
        sa.Column('menu_item_id', sa.UUID(), nullable=False),
        sa.Column('modifier_group_id', sa.UUID(), nullable=False),
        sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False),
        sa.ForeignKeyConstraint(['menu_item_id'], ['menu_items.id'], ondelete='CASCADE', name='fk_mimg_menu_item_id'),
        sa.ForeignKeyConstraint(['modifier_group_id'], ['modifier_groups.id'], ondelete='CASCADE', name='fk_mimg_modifier_group_id'),
        sa.PrimaryKeyConstraint('menu_item_id', 'modifier_group_id'),
    )

    op.create_table(
        'order_item_modifiers',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('order_item_id', sa.UUID(), nullable=False),
        sa.Column('modifier_item_id', sa.UUID(), nullable=False),
        sa.Column('name_snapshot', sa.String(length=100), nullable=False),
        sa.Column('price_snapshot', sa.Numeric(precision=10, scale=2), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['order_item_id'], ['order_items.id'], name='fk_order_item_modifiers_order_item_id'),
        sa.ForeignKeyConstraint(['modifier_item_id'], ['modifier_items.id'], name='fk_order_item_modifiers_modifier_item_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_order_item_modifiers_order_item_id'), 'order_item_modifiers', ['order_item_id'], unique=False)

    # --- Branches / floors / tables ------------------------------------------
    op.add_column('branches', sa.Column('timezone', sa.String(length=64), server_default='Asia/Kolkata', nullable=False))
    op.add_column('branches', sa.Column('gstin', sa.String(length=15), nullable=True))
    op.add_column('branches', sa.Column('upi_vpa', sa.String(length=100), nullable=True))
    op.add_column('branches', sa.Column('upi_payee_name', sa.String(length=100), nullable=True))
    op.add_column('floors', sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False))
    op.add_column('dining_tables', sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False))
    op.create_index(op.f('ix_dining_tables_merged_into_table_id'), 'dining_tables', ['merged_into_table_id'], unique=False)
    op.create_unique_constraint('uq_dining_tables_floor_number', 'dining_tables', ['floor_id', 'table_number'])

    # --- Catalog -------------------------------------------------------------
    op.add_column('menu_items', sa.Column('station', sa.String(length=50), server_default='kitchen', nullable=False))
    op.add_column('menu_items', sa.Column('sort_order', sa.Integer(), server_default='0', nullable=False))
    op.add_column('item_variants', sa.Column('is_active', sa.Boolean(), server_default='true', nullable=False))

    # --- Staff / shifts --------------------------------------------------------
    op.add_column('staff_users', sa.Column('pin_lookup', sa.String(length=64), nullable=True))
    op.add_column('staff_users', sa.Column('token_version', sa.Integer(), server_default='0', nullable=False))
    op.create_index(
        'uq_staff_users_branch_pin_lookup', 'staff_users', ['branch_id', 'pin_lookup'], unique=True,
        postgresql_where=sa.text('is_active AND pin_lookup IS NOT NULL'),
    )
    op.add_column('shifts', sa.Column('expected_cash', sa.Numeric(precision=10, scale=2), nullable=True))
    op.add_column('shifts', sa.Column('cash_variance', sa.Numeric(precision=10, scale=2), nullable=True))
    op.create_index('uq_shifts_user_open', 'shifts', ['user_id'], unique=True, postgresql_where=sa.text("status = 'open'"))

    # --- Orders ------------------------------------------------------------------
    op.add_column('orders', sa.Column('opened_by_shift_id', sa.UUID(), nullable=True))
    op.add_column('orders', sa.Column('notes', sa.Text(), nullable=True))
    op.add_column('orders', sa.Column('cancel_reason', sa.Text(), nullable=True))
    op.add_column('orders', sa.Column('business_date', sa.Date(), nullable=True))
    op.execute("UPDATE orders SET business_date = (created_at AT TIME ZONE 'Asia/Kolkata')::date")
    op.alter_column('orders', 'business_date', nullable=False)
    op.create_index(op.f('ix_orders_business_date'), 'orders', ['business_date'], unique=False)
    op.drop_index(op.f('ix_orders_order_number'), table_name='orders')
    op.create_index(op.f('ix_orders_order_number'), 'orders', ['order_number'], unique=False)
    op.create_unique_constraint('uq_orders_branch_number', 'orders', ['branch_id', 'order_number'])

    op.add_column('order_items', sa.Column('variant_name_snapshot', sa.String(length=100), nullable=True))
    op.add_column('order_items', sa.Column('station_snapshot', sa.String(length=50), server_default='kitchen', nullable=False))
    op.add_column('order_items', sa.Column('line_total', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False))
    op.execute("UPDATE order_items SET line_total = unit_price_snapshot * quantity")
    op.create_index(op.f('ix_order_items_item_id'), 'order_items', ['item_id'], unique=False)

    # --- KOTs ----------------------------------------------------------------------
    op.add_column('kots', sa.Column('branch_id', sa.UUID(), nullable=True))
    op.add_column('kots', sa.Column('fired_by_id', sa.UUID(), nullable=True))
    op.add_column('kots', sa.Column('voided_by_id', sa.UUID(), nullable=True))
    op.add_column('kots', sa.Column('voided_approved_by_id', sa.UUID(), nullable=True))
    op.execute("UPDATE kots SET branch_id = orders.branch_id FROM orders WHERE orders.id = kots.order_id")
    op.alter_column('kots', 'branch_id', nullable=False)
    op.drop_index(op.f('ix_kots_kot_number'), table_name='kots')
    op.create_index(op.f('ix_kots_kot_number'), 'kots', ['kot_number'], unique=False)
    op.create_index('ix_kots_branch_station_status', 'kots', ['branch_id', 'station', 'status'], unique=False)
    op.create_unique_constraint('uq_kots_branch_number', 'kots', ['branch_id', 'kot_number'])
    op.create_index(op.f('ix_kot_items_order_item_id'), 'kot_items', ['order_item_id'], unique=False)

    # --- Billing -------------------------------------------------------------------
    op.add_column('bills', sa.Column('branch_id', sa.UUID(), nullable=True))
    op.add_column('bills', sa.Column('business_date', sa.Date(), nullable=True))
    op.add_column('bills', sa.Column('discount_approved_by_id', sa.UUID(), nullable=True))
    op.add_column('bills', sa.Column('paid_amount', sa.Numeric(precision=10, scale=2), server_default='0', nullable=False))
    op.add_column('bills', sa.Column('voided_by_id', sa.UUID(), nullable=True))
    op.add_column('bills', sa.Column('void_reason', sa.Text(), nullable=True))
    op.execute(
        "UPDATE bills SET branch_id = orders.branch_id, business_date = orders.business_date "
        "FROM orders WHERE orders.id = bills.order_id"
    )
    op.execute(
        "UPDATE bills SET paid_amount = COALESCE((SELECT SUM(amount) FROM payments "
        "WHERE payments.bill_id = bills.id AND payments.status = 'success'), 0)"
    )
    op.alter_column('bills', 'branch_id', nullable=False)
    op.alter_column('bills', 'business_date', nullable=False)
    op.drop_index(op.f('ix_bills_bill_number'), table_name='bills')
    op.create_index(op.f('ix_bills_bill_number'), 'bills', ['bill_number'], unique=False)
    op.create_index(op.f('ix_bills_branch_id'), 'bills', ['branch_id'], unique=False)
    op.create_index(op.f('ix_bills_business_date'), 'bills', ['business_date'], unique=False)
    op.create_unique_constraint('uq_bills_branch_number', 'bills', ['branch_id', 'bill_number'])
    op.create_index('uq_bills_order_active', 'bills', ['order_id'], unique=True, postgresql_where=sa.text("status <> 'voided'"))

    op.add_column('payments', sa.Column('received_by_id', sa.UUID(), nullable=True))
    op.add_column('payments', sa.Column('shift_id', sa.UUID(), nullable=True))
    op.add_column('payments', sa.Column('idempotency_key', sa.String(length=100), nullable=True))
    op.create_index(op.f('ix_payments_shift_id'), 'payments', ['shift_id'], unique=False)
    op.create_index(
        'uq_payments_bill_idempotency', 'payments', ['bill_id', 'idempotency_key'], unique=True,
        postgresql_where=sa.text('idempotency_key IS NOT NULL'),
    )

    # --- Foreign keys and CHECK constraints --------------------------------------
    for name, source, referent, column, ondelete in NEW_FKS:
        op.create_foreign_key(name, source, referent, [column], ['id'], ondelete=ondelete)

    for (table, column), values in CHECKS.items():
        allowed = ", ".join(f"'{v}'" for v in values)
        op.create_check_constraint(f"ck_{table}_{column}", table, f"{column} IN ({allowed})")
    for table, name, condition in PLAIN_CHECKS:
        op.create_check_constraint(name, table, condition)


def downgrade() -> None:
    for table, name, _ in reversed(PLAIN_CHECKS):
        op.drop_constraint(name, table, type_='check')
    for (table, column) in reversed(list(CHECKS)):
        if table != 'audit_logs':
            op.drop_constraint(f"ck_{table}_{column}", table, type_='check')
    for name, source, _, _, _ in reversed(NEW_FKS):
        op.drop_constraint(name, source, type_='foreignkey')

    op.drop_index('uq_payments_bill_idempotency', table_name='payments')
    op.drop_index(op.f('ix_payments_shift_id'), table_name='payments')
    op.drop_column('payments', 'idempotency_key')
    op.drop_column('payments', 'shift_id')
    op.drop_column('payments', 'received_by_id')

    op.drop_index('uq_bills_order_active', table_name='bills')
    op.drop_constraint('uq_bills_branch_number', 'bills', type_='unique')
    op.drop_index(op.f('ix_bills_business_date'), table_name='bills')
    op.drop_index(op.f('ix_bills_branch_id'), table_name='bills')
    op.drop_index(op.f('ix_bills_bill_number'), table_name='bills')
    op.create_index(op.f('ix_bills_bill_number'), 'bills', ['bill_number'], unique=True)
    for column in ('void_reason', 'voided_by_id', 'paid_amount', 'discount_approved_by_id', 'business_date', 'branch_id'):
        op.drop_column('bills', column)

    op.drop_index(op.f('ix_kot_items_order_item_id'), table_name='kot_items')
    op.drop_constraint('uq_kots_branch_number', 'kots', type_='unique')
    op.drop_index('ix_kots_branch_station_status', table_name='kots')
    op.drop_index(op.f('ix_kots_kot_number'), table_name='kots')
    op.create_index(op.f('ix_kots_kot_number'), 'kots', ['kot_number'], unique=True)
    for column in ('voided_approved_by_id', 'voided_by_id', 'fired_by_id', 'branch_id'):
        op.drop_column('kots', column)

    op.drop_index(op.f('ix_order_items_item_id'), table_name='order_items')
    for column in ('line_total', 'station_snapshot', 'variant_name_snapshot'):
        op.drop_column('order_items', column)

    op.drop_constraint('uq_orders_branch_number', 'orders', type_='unique')
    op.drop_index(op.f('ix_orders_order_number'), table_name='orders')
    op.create_index(op.f('ix_orders_order_number'), 'orders', ['order_number'], unique=True)
    op.drop_index(op.f('ix_orders_business_date'), table_name='orders')
    for column in ('business_date', 'cancel_reason', 'notes', 'opened_by_shift_id'):
        op.drop_column('orders', column)

    op.drop_index('uq_shifts_user_open', table_name='shifts')
    op.drop_column('shifts', 'cash_variance')
    op.drop_column('shifts', 'expected_cash')
    op.drop_index('uq_staff_users_branch_pin_lookup', table_name='staff_users')
    op.drop_column('staff_users', 'token_version')
    op.drop_column('staff_users', 'pin_lookup')

    op.drop_column('item_variants', 'is_active')
    op.drop_column('menu_items', 'sort_order')
    op.drop_column('menu_items', 'station')

    op.drop_constraint('uq_dining_tables_floor_number', 'dining_tables', type_='unique')
    op.drop_index(op.f('ix_dining_tables_merged_into_table_id'), table_name='dining_tables')
    op.drop_column('dining_tables', 'is_active')
    op.drop_column('floors', 'is_active')
    for column in ('upi_payee_name', 'upi_vpa', 'gstin', 'timezone'):
        op.drop_column('branches', column)

    op.drop_index(op.f('ix_order_item_modifiers_order_item_id'), table_name='order_item_modifiers')
    op.drop_table('order_item_modifiers')
    op.drop_table('menu_item_modifier_groups')
    op.drop_index('uq_daily_reports_branch_date', table_name='daily_reports')
    op.drop_table('daily_reports')
    op.drop_table('document_counters')
    op.drop_index('ix_audit_logs_branch_created', table_name='audit_logs')
    op.drop_table('audit_logs')
