"""kitchen slip print tracking

Per-ticket print count and attribution, and the `kot_reprint` audit action.

Revision ID: e3c9a5f27d14
Revises: d6b2e8a41f93
Create Date: 2026-11-05 13:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'e3c9a5f27d14'
down_revision: Union[str, None] = 'd6b2e8a41f93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_ACTIONS = ("'kot_void', 'order_cancel', 'order_item_cancel', 'bill_void', 'high_discount', "
               "'table_transfer', 'table_merge', 'login_lockout', 'cash_variance'")
NEW_ACTIONS = OLD_ACTIONS + ", 'kot_reprint'"


def upgrade() -> None:
    op.add_column('kots', sa.Column('print_count', sa.Integer(), server_default='0', nullable=False))
    op.add_column('kots', sa.Column('first_printed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('kots', sa.Column('last_printed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('kots', sa.Column('last_printed_by_id', postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key('fk_kots_last_printed_by_id', 'kots', 'staff_users', ['last_printed_by_id'], ['id'])

    op.drop_constraint('ck_audit_logs_action_type', 'audit_logs', type_='check')
    op.create_check_constraint('ck_audit_logs_action_type', 'audit_logs', f"action_type IN ({NEW_ACTIONS})")


def downgrade() -> None:
    op.execute("DELETE FROM audit_logs WHERE action_type = 'kot_reprint'")
    op.drop_constraint('ck_audit_logs_action_type', 'audit_logs', type_='check')
    op.create_check_constraint('ck_audit_logs_action_type', 'audit_logs', f"action_type IN ({OLD_ACTIONS})")

    op.drop_constraint('fk_kots_last_printed_by_id', 'kots', type_='foreignkey')
    op.drop_column('kots', 'last_printed_by_id')
    op.drop_column('kots', 'last_printed_at')
    op.drop_column('kots', 'first_printed_at')
    op.drop_column('kots', 'print_count')
