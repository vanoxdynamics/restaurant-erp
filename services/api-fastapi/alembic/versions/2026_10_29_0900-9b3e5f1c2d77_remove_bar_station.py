"""remove bar station

The restaurant has no bar: drinks are prepared in the main kitchen. Existing bar items, order lines
and tickets move to the kitchen station, and the station CHECK constraints no longer allow 'bar'.

Revision ID: 9b3e5f1c2d77
Revises: 7c1d2e9f4a10
Create Date: 2026-10-29 09:00:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = '9b3e5f1c2d77'
down_revision: Union[str, None] = '7c1d2e9f4a10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STATIONS_WITHOUT_BAR = "'kitchen', 'grill', 'dessert'"
STATIONS_WITH_BAR = "'kitchen', 'bar', 'grill', 'dessert'"


def upgrade() -> None:
    op.execute("UPDATE menu_items SET station = 'kitchen' WHERE station = 'bar'")
    op.execute("UPDATE order_items SET station_snapshot = 'kitchen' WHERE station_snapshot = 'bar'")
    op.execute("UPDATE kots SET station = 'kitchen' WHERE station = 'bar'")

    for table in ('menu_items', 'kots'):
        op.drop_constraint(f'ck_{table}_station', table, type_='check')
        op.create_check_constraint(f'ck_{table}_station', table, f'station IN ({STATIONS_WITHOUT_BAR})')


def downgrade() -> None:
    # Rows moved to 'kitchen' are not moved back; only the constraint is widened again.
    for table in ('menu_items', 'kots'):
        op.drop_constraint(f'ck_{table}_station', table, type_='check')
        op.create_check_constraint(f'ck_{table}_station', table, f'station IN ({STATIONS_WITH_BAR})')
