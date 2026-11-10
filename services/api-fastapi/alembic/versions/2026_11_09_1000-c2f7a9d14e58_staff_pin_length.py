"""staff PIN length

Staff users keep how many digits their PIN has (not the PIN), so the sign-in pad shows 4, 5 or 6 boxes. PINs set before this
change stay NULL until they are next set; the pad then grows as digits are typed, as before.

Revision ID: c2f7a9d14e58
Revises: b8e4c2d61f35
Create Date: 2026-11-09 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c2f7a9d14e58'
down_revision: Union[str, None] = 'b8e4c2d61f35'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('staff_users', sa.Column('pin_length', sa.SmallInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column('staff_users', 'pin_length')
