"""
Pine Labs cloud-based integration: send a bill amount to a payment terminal over Pine Labs' servers, ask how it went, or cancel it.

Three calls (UploadBilledTransaction, GetCloudBasedTxnStatus, CancelTransaction). Amounts go to Pine Labs in paise. Credentials
come from settings (PINELABS_*) and are never logged or returned. This module only talks to Pine Labs; recording the payment
against a bill is done by the billing service.
"""

import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("pinelabs.cloud")

UPLOAD_PATH = "/API/CloudBasedIntegration/V1/UploadBilledTransaction"
STATUS_PATH = "/API/CloudBasedIntegration/V1/GetCloudBasedTxnStatus"
CANCEL_PATH = "/API/CloudBasedIntegration/V1/CancelTransaction"

PAYMENT_MODE_ALL = "1"  # AllowedPaymentMode used in Pine Labs' sample request: card

# AllowedPaymentMode sent to the terminal for each way of paying: 1 card, 10 UPI, 11 Bharat QR. The UAT terminal answers
# "subsystem not supported" to 10, so UPI on the POS is taken through Bharat QR (11).
ALLOWED_PAYMENT_MODES = {"card": "1", "upi": "11", "bharatqr": "11"}


def allowed_payment_mode(payment_mode: str) -> str:
    return ALLOWED_PAYMENT_MODES.get((payment_mode or "").lower(), PAYMENT_MODE_ALL)


class PineLabsError(Exception):
    """Pine Labs could not be reached, answered with an error status, or sent something that is not JSON."""


class PineLabsNotConfigured(PineLabsError):
    pass


def to_paise(amount: Decimal) -> str:
    paise = (Decimal(amount) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if paise <= 0:
        raise ValueError("Amount must be positive")
    return str(int(paise))


def _credentials() -> Dict[str, Any]:
    needed = {"PINELABS_MERCHANT_ID": settings.PINELABS_MERCHANT_ID, "PINELABS_SECURITY_TOKEN": settings.PINELABS_SECURITY_TOKEN,
              "PINELABS_STORE_ID": settings.PINELABS_STORE_ID, "PINELABS_CLIENT_ID": settings.PINELABS_CLIENT_ID}
    missing = [name for name, value in needed.items() if not value]
    if missing:
        raise PineLabsNotConfigured("Pine Labs cloud is not configured: " + ", ".join(missing))
    return {"MerchantID": int(settings.PINELABS_MERCHANT_ID), "SecurityToken": settings.PINELABS_SECURITY_TOKEN}


async def _post(path: str, body: Dict[str, Any], client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    url = settings.PINELABS_CLOUD_BASE_URL.rstrip("/") + path
    try:
        if client is not None:
            response = await client.post(url, json=body)
        else:
            async with httpx.AsyncClient(timeout=settings.PINELABS_TIMEOUT_SECONDS, follow_redirects=False) as own:
                response = await own.post(url, json=body)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Pine Labs call %s failed: %s", path, type(exc).__name__)
        raise PineLabsError("Could not reach Pine Labs") from exc
    except ValueError as exc:
        raise PineLabsError("Pine Labs sent an unreadable answer") from exc
    if not isinstance(data, dict):
        raise PineLabsError("Pine Labs sent an unexpected answer")
    return data


async def upload_transaction(
    transaction_number: str, amount: Decimal, sequence_number: int = 1, payment_mode: str = PAYMENT_MODE_ALL,
    auto_cancel_minutes: int = 5, client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """Send a billed amount to the terminal. The answer carries PlutusTransactionReferenceID, used to poll or cancel."""
    body = {
        **_credentials(),
        "TransactionNumber": str(transaction_number),
        "SequenceNumber": sequence_number,
        "AllowedPaymentMode": payment_mode,
        "Amount": to_paise(amount),
        "UserID": settings.PINELABS_USER_ID,
        "StoreID": settings.PINELABS_STORE_ID,
        "ClientID": settings.PINELABS_CLIENT_ID,
        "AutoCancelDurationInMinutes": auto_cancel_minutes,
    }
    return await _post(UPLOAD_PATH, body, client)


async def get_status(plutus_reference_id: int, client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    body = {**_credentials(), "StoreId": settings.PINELABS_STORE_ID, "ClientId": settings.PINELABS_CLIENT_ID,
            "PlutusTransactionReferenceID": plutus_reference_id}
    return await _post(STATUS_PATH, body, client)


async def cancel_transaction(plutus_reference_id: int, amount: Decimal, client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    body = {**_credentials(), "ClientId": settings.PINELABS_CLIENT_ID, "StoreID": settings.PINELABS_STORE_ID,
            "PlutusTransactionReferenceID": plutus_reference_id, "Amount": to_paise(amount)}
    return await _post(CANCEL_PATH, body, client)
