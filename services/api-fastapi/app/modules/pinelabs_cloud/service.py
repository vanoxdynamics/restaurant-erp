"""
Taking a card/UPI payment through Pine Labs' cloud: the payment is first saved as pending by the module that owns the sale
(restaurant bill or shop sale), then sent to the terminal, polled, and completed here once Pine Labs says approved or failed.

The owning module is reached through an `Adapter`, so restaurant bills and shop sales share this code.
"""

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.enums import PaymentStatus
from app.core.errors import InvalidTransition, ValidationFailed
from app.core.events import commit
from app.modules.pinelabs_cloud import client as pinelabs
from app.modules.pinelabs_cloud import status as readings


def is_enabled() -> bool:
    from app.core.config import settings
    return all((settings.PINELABS_MERCHANT_ID, settings.PINELABS_SECURITY_TOKEN, settings.PINELABS_STORE_ID, settings.PINELABS_CLIENT_ID))


@dataclass
class Adapter:
    """`load` returns (payment, sale) locked for update; `complete` records approved/declined and returns the fresh pair."""
    load: Callable[[], Awaitable[Tuple[Any, Any]]]
    complete: Callable[[str, Optional[str], Dict[str, Any], str], Awaitable[Tuple[Any, Any]]]


Result = Tuple[Any, Any, str, str]  # payment, sale, state (pending|approved|declined|unknown), message


def _check_payable(payment: Any) -> None:
    if payment.provider != "pinelabs" or payment.payment_mode not in ("card", "upi"):
        raise InvalidTransition("This payment was not taken on a terminal", code="not_terminal_payment")


def _cloud(payment: Any) -> Dict[str, Any]:
    return dict((payment.provider_meta or {}).get("cloud") or {})


def _settled(payment: Any, sale: Any) -> Optional[Result]:
    """A payment that already has its answer is reported as it is, without asking Pine Labs again."""
    if payment.status == PaymentStatus.SUCCESS.value:
        return payment, sale, "approved", "Approved"
    if payment.status != PaymentStatus.PENDING.value:
        return payment, sale, "declined", (payment.provider_meta or {}).get("message") or "Not paid"
    return None


async def _finish(adapter: Adapter, reading: readings.Reading, payment: Any, sale: Any) -> Result:
    if reading.state == "pending":
        return payment, sale, "pending", reading.message
    payment, sale = await adapter.complete(
        "approved" if reading.state == "approved" else "declined", reading.approval_code or None, reading.details, reading.message)
    return payment, sale, reading.state, reading.message


async def _poll(adapter: Adapter, payment: Any, sale: Any, reference: Any) -> Result:
    try:
        data = await pinelabs.get_status(reference)
    except pinelabs.PineLabsNotConfigured as exc:
        raise ValidationFailed(str(exc), code="pinelabs_cloud_not_configured") from exc
    except pinelabs.PineLabsError as exc:
        return payment, sale, "unknown", f"{exc}. The payment is still pending: check again shortly."
    return await _finish(adapter, readings.read_status(data), payment, sale)


async def send(db: AsyncSession, adapter: Adapter) -> Result:
    """Send the pending payment to the terminal. Sending twice is safe: the second call only checks."""
    payment, sale = await adapter.load()
    _check_payable(payment)
    done = _settled(payment, sale)
    if done:
        return done
    cloud = _cloud(payment)
    if not cloud.get("reference_id") and payment.amount < settings.PINELABS_MIN_AMOUNT:
        raise ValidationFailed(f"The terminal takes {settings.PINELABS_MIN_AMOUNT} rupee or more", code="terminal_amount_too_small")
    if cloud.get("reference_id"):
        return await _poll(adapter, payment, sale, cloud["reference_id"])
    try:
        number = (payment.provider_meta or {}).get("billing_ref_no") or str(payment.id)[:10]
        data = await pinelabs.upload_transaction(number, payment.amount, payment_mode=pinelabs.allowed_payment_mode(payment.payment_mode))
    except pinelabs.PineLabsNotConfigured as exc:
        raise ValidationFailed(str(exc), code="pinelabs_cloud_not_configured") from exc
    except pinelabs.PineLabsError as exc:
        # We do not know whether Pine Labs received it: leave the payment pending for the cashier to check.
        return payment, sale, "unknown", f"{exc}. Check the terminal before sending again."
    reference = readings.upload_reference(data)
    if reference is None:
        message = readings.message_of(data) or "Pine Labs refused the payment"
        if readings.upload_may_be_on_terminal(data):
            return payment, sale, "unknown", f"{message}. It may already be on the terminal: check before sending again."
        payment, sale = await adapter.complete("declined", None, {"ResponseMsg": message[:100]}, message)
        return payment, sale, "declined", message
    payment.provider_meta = {**(payment.provider_meta or {}), "cloud": {"reference_id": reference}}
    await commit(db)
    payment, sale = await adapter.load()
    return payment, sale, "pending", "Sent to the terminal. Ask the customer to pay on it."


async def check(db: AsyncSession, adapter: Adapter) -> Result:
    """Ask Pine Labs how a sent payment went, and record the answer once it is final."""
    payment, sale = await adapter.load()
    _check_payable(payment)
    done = _settled(payment, sale)
    if done:
        return done
    reference = _cloud(payment).get("reference_id")
    if not reference:
        return payment, sale, "unknown", "This payment was not sent to the terminal. Send it again, or resolve it from the charge slip."
    return await _poll(adapter, payment, sale, reference)


async def cancel(db: AsyncSession, adapter: Adapter) -> Result:
    """Cancel a sent payment on the terminal. If it was already paid, the answer says so and it is recorded as approved."""
    payment, sale = await adapter.load()
    _check_payable(payment)
    done = _settled(payment, sale)
    if done:
        return done
    reference = _cloud(payment).get("reference_id")
    if not reference:
        payment, sale = await adapter.complete("declined", None, {}, "Cancelled before it was sent to the terminal")
        return payment, sale, "declined", "Cancelled"
    try:
        await pinelabs.cancel_transaction(reference, payment.amount)
    except pinelabs.PineLabsNotConfigured as exc:
        raise ValidationFailed(str(exc), code="pinelabs_cloud_not_configured") from exc
    except pinelabs.PineLabsError as exc:
        return payment, sale, "unknown", f"{exc}. The payment is still pending: check the terminal."
    # Whatever Pine Labs answered to the cancel, its status is the truth (the customer may have paid a moment before).
    payment, sale, state, message = await _poll(adapter, payment, sale, reference)
    if state == "pending":
        message = "Cancel sent. Check again in a moment; do not collect this amount another way until it shows cancelled."
    return payment, sale, state, message
