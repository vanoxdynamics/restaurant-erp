from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class EntitlementsRead(BaseModel):
    """What the caller's organization may use. The server enforces this; apps use it to hide what is off."""
    access: str  # full | read_only | blocked
    org_status: str
    plan_code: Optional[str]
    subscription_status: Optional[str]
    billing_term: Optional[str]
    current_period_end: Optional[datetime]
    trial_ends_at: Optional[datetime]
    grace_until: Optional[datetime]
    modules: List[str]
    max_branches: Optional[int]
    max_staff: Optional[int]
    impersonation: bool = False  # true when a platform operator is viewing this account (read-only)
