"""
Reading Pine Labs' answers. Pure (no network, no database).

A payment is only called approved when Pine Labs says so in plain words, and only called failed when it says so too: anything
else stays "pending", so an unfamiliar answer can never record a payment that was not taken (or drop one that was).
Check these rules against a real UAT answer before relying on them in production.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Literal

from app.modules.billing.service import TERMINAL_DETAIL_KEYS

State = Literal["pending", "approved", "declined"]

# Words in ResponseMessage that mean the terminal will not take this payment.
FAILED_WORDS = ("DECLINE", "CANCEL", "FAIL", "REJECT", "TIMEOUT", "TIMED OUT", "EXPIRED", "NOT APPROVED", "INVALID")
# A rejected upload with one of these may mean the amount is already on the terminal: do not treat it as "never sent".
ALREADY_SENT_WORDS = ("DUPLICATE", "ALREADY", "EXIST")


@dataclass
class Reading:
    state: State
    message: str
    approval_code: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


_KEPT = {_norm(key): key for key in TERMINAL_DETAIL_KEYS}
# Pine Labs' own labels seen in a real UAT approval ("RRN", "TID", "MID", "Acquirer Id")
_KEPT.update({"rrn": "RetrievalReferenceNumber", "tid": "TerminalId", "mid": "MerchantId", "acquirerid": "AcquiringBankCode"})


def message_of(data: Dict[str, Any]) -> str:
    return str(data.get("ResponseMessage") or data.get("Message") or "").strip()


def transaction_details(data: Dict[str, Any]) -> Dict[str, Any]:
    """The Tag/Value pairs of TransactionData, renamed to the fields we keep (the server drops everything else)."""
    details: Dict[str, Any] = {}
    rows = data.get("TransactionData")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and row.get("Tag") is not None:
                key = _KEPT.get(_norm(str(row["Tag"])))
                if key and row.get("Value") not in (None, ""):
                    details[key] = row["Value"]
    return details


def read_status(data: Dict[str, Any]) -> Reading:
    message = message_of(data)
    upper = message.upper()
    details = transaction_details(data)
    details["ResponseMsg"] = message[:100]
    if data.get("ResponseCode") is not None:
        details["ResponseCode"] = str(data["ResponseCode"])
    if "APPROVED" in upper and not any(word in upper for word in FAILED_WORDS):
        code = str(details.get("ApprovalCode") or "").strip() or f"PL{data.get('PlutusTransactionReferenceID', '')}"
        return Reading("approved", message or "Approved", code, details)
    if any(word in upper for word in FAILED_WORDS):
        return Reading("declined", message, "", details)
    return Reading("pending", message or "Waiting for the customer to pay on the terminal", "", details)


def upload_reference(data: Dict[str, Any]) -> Any:
    """The id Pine Labs gives an accepted upload, or None when it was refused."""
    ref = data.get("PlutusTransactionReferenceID")
    return ref if data.get("ResponseCode") in (0, "0") and ref not in (None, "", 0) else None


def upload_may_be_on_terminal(data: Dict[str, Any]) -> bool:
    return any(word in message_of(data).upper() for word in ALREADY_SENT_WORDS)
