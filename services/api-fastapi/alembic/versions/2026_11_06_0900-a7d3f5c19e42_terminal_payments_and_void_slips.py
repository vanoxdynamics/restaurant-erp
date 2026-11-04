"""terminal payments and VOID slip tracking

Payments gain the `reversed` status (a card/UPI terminal payment voided the same day) and the `payment_reverse` audit action.
KOTs gain `void_printed_at`, so the counter's print queue knows which VOID notices are still to print.

Revision ID: a7d3f5c19e42
Revises: e3c9a5f27d14
Create Date: 2026-11-06 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a7d3f5c19e42'
down_revision: Union[str, None] = 'e3c9a5f27d14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_STATUSES = "'pending', 'success', 'failed'"
NEW_STATUSES = OLD_STATUSES + ", 'reversed'"
OLD_ACTIONS = ("'kot_void', 'order_cancel', 'order_item_cancel', 'bill_void', 'high_discount', "
               "'table_transfer', 'table_merge', 'login_lockout', 'cash_variance', 'kot_reprint'")
NEW_ACTIONS = OLD_ACTIONS + ", 'payment_reverse'"


def upgrade() -> None:
    op.drop_constraint('ck_payments_status', 'payments', type_='check')
    op.create_check_constraint('ck_payments_status', 'payments', f"status IN ({NEW_STATUSES})")
    op.drop_constraint('ck_audit_logs_action_type', 'audit_logs', type_='check')
    op.create_check_constraint('ck_audit_logs_action_type', 'audit_logs', f"action_type IN ({NEW_ACTIONS})")
    op.add_column('kots', sa.Column('void_printed_at', sa.DateTime(timezone=True), nullable=True))
    # Tickets cancelled before this release are history: the print station must not print a backlog of old VOID notices.
    op.execute("UPDATE kots SET void_printed_at = updated_at WHERE status = 'voided'")


def downgrade() -> None:
    op.drop_column('kots', 'void_printed_at')
    op.execute("DELETE FROM audit_logs WHERE action_type = 'payment_reverse'")
    op.drop_constraint('ck_audit_logs_action_type', 'audit_logs', type_='check')
    op.create_check_constraint('ck_audit_logs_action_type', 'audit_logs', f"action_type IN ({OLD_ACTIONS})")
    op.execute("UPDATE payments SET status = 'failed' WHERE status = 'reversed'")
    op.drop_constraint('ck_payments_status', 'payments', type_='check')
    op.create_check_constraint('ck_payments_status', 'payments', f"status IN ({OLD_STATUSES})")
