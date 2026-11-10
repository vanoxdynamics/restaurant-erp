import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class Notice(BaseModel):
    id: str  # stable: the same event always has the same id
    kind: str  # food_ready | kot_cancelled | kot_printed | cancel_approved | cancel_declined | bill_paid | sold_out
    title: str
    detail: Optional[str] = None
    order_id: Optional[uuid.UUID] = None
    at: datetime


class NoticesRead(BaseModel):
    now: datetime  # send this back as `since` next time
    notices: List[Notice]
