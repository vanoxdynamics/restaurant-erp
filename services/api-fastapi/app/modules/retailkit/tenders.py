"""
Taking payment on a shop sale: the payment columns and the tender rules, shared by the dress and hardware modules.

This is a port of the restaurant bill's payment code (`app.modules.billing.service`: `record_payment`, `process_split_payment`,
`start_terminal_payment`, `complete_terminal_payment`, `reverse_terminal_payment`) for sales that are their own invoice. The
rules are the same:

- cash may exceed what is owed and returns change; no other mode may;
- while a card/UPI terminal payment is pending, nothing else may change what the sale owes;
- repeating the same terminal answer is harmless, a different answer for a finished payment is refused;
- reversing a terminal payment needs a manager and is audited.

`Tenders` always reads the sale's `balance_due` and never works it out, because a hardware sale also counts what was charged
to the customer's account. Each module owns its payment modes (its own enum and check constraint); `cash` is the only mode that
may give change.
"""

import uuid
from decimal import Decimal
from typing import Any, Awaitable, Callable, Dict, Generic, List, Optional, Protocol, Sequence, Tuple, Type, TypeVar

from sqlalchemy import ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, selectinload

from app.core.audit import record_audit
from app.core.auth import CurrentUser, require_branch_access, resolve_approver
from app.core.clock import utcnow
from app.core.enums import AuditAction, BillStatus, PaymentStatus, Topic
from app.core.errors import Conflict, InvalidTransition, ValidationFailed
from app.core.events import commit, queue_event
from app.core.lookups import get_or_404
from app.core.money import ZERO, q
from app.modules.billing.service import safe_terminal_details, terminal_ref
from app.modules.staff.service import get_open_shift

CASH = "cash"
PAYABLE = (BillStatus.UNPAID.value, BillStatus.PARTIALLY_PAID.value)


class PaymentColumns:
    """The columns of `payments` without `bill_id`. A module's payment model adds `sale_id`, `branch_id` and its checks."""

    payment_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    reference_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)  # UPI UTR, card approval code
    status: Mapped[str] = mapped_column(String(30), default=PaymentStatus.SUCCESS.value, nullable=False)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # "manual" (keyed in by the cashier) or a terminal ("pinelabs"), whose masked transaction details go in provider_meta.
    provider: Mapped[str] = mapped_column(String(30), default="manual", server_default="manual", nullable=False)
    provider_meta: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    @declared_attr
    def received_by_id(cls) -> Mapped[Optional[uuid.UUID]]:
        return mapped_column(UUID(as_uuid=True), ForeignKey("staff_users.id"), nullable=True)

    @declared_attr
    def shift_id(cls) -> Mapped[Optional[uuid.UUID]]:
        return mapped_column(UUID(as_uuid=True), ForeignKey("shifts.id"), nullable=True, index=True)


class SaleLike(Protocol):
    id: uuid.UUID
    branch_id: uuid.UUID
    sale_number: str
    grand_total: Decimal
    paid_amount: Decimal
    status: str
    settled_at: Any
    payments: List[Any]

    @property
    def balance_due(self) -> Decimal: ...


S = TypeVar("S")
P = TypeVar("P")
Hook = Callable[[AsyncSession, Any], Awaitable[None]]


async def _noop(db: AsyncSession, sale: Any) -> None:
    return None


class Tenders(Generic[S, P]):
    """
    Tender handling for one module. `on_settled(db, sale)` runs in the transaction that makes the sale paid;
    `on_reopened(db, sale)` in the one that reverses a payment on a sale that had been paid.

    `entity_type` names the sale in audit rows ("apparel_sale"); `event_prefix` names its events ("apparel.sale").
    """

    def __init__(
        self, sale_model: Type[S], payment_model: Type[P], *, entity_type: str, event_prefix: str,
        on_settled: Hook = _noop, on_reopened: Hook = _noop,
    ) -> None:
        # Typed loosely: the models are the module's own, and the kit only relies on the columns described above.
        self.sale_model: Any = sale_model
        self.payment_model: Any = payment_model
        self.entity_type = entity_type
        self.event_prefix = event_prefix
        self.on_settled = on_settled
        self.on_reopened = on_reopened

    # --- building blocks (a module composes these for tenders of its own, such as store credit) ---------------------------

    async def load(self, db: AsyncSession, sale_id: uuid.UUID, *, for_update: bool = False) -> S:
        sale: S = await get_or_404(db, self.sale_model, sale_id, selectinload(self.sale_model.payments),
                                   for_update=for_update, populate_existing=True)
        return sale

    async def _reload(self, db: AsyncSession, sale_id: uuid.UUID) -> Any:
        return await self.load(db, sale_id)

    def event(self, db: AsyncSession, sale: Any, name: str, payload: Optional[Dict[str, Any]] = None) -> None:
        queue_event(db, sale.branch_id, Topic.BILLING, f"{self.event_prefix}.{name}", sale.id, payload)

    @staticmethod
    def refuse_while_terminal_pending(sale: Any) -> None:
        """While a terminal is charging this sale, nothing else may change what is owed."""
        pending = next((p for p in sale.payments if p.status == PaymentStatus.PENDING.value), None)
        if pending is not None:
            raise Conflict("A card or UPI payment on this sale is not finished yet. Complete or resolve it first.",
                           code="terminal_payment_pending", details={"payment_id": str(pending.id)})

    async def load_payable(self, db: AsyncSession, user: CurrentUser, sale_id: uuid.UUID) -> S:
        sale: Any = await self.load(db, sale_id, for_update=True)
        require_branch_access(user, sale.branch_id)
        if sale.status not in PAYABLE:
            raise InvalidTransition(f"Sale is {sale.status}", details={"status": sale.status})
        self.refuse_while_terminal_pending(sale)
        return sale

    async def add_payment(
        self, db: AsyncSession, user: CurrentUser, sale: Any, *, mode: str, amount: Decimal,
        reference_id: Optional[str] = None, idempotency_key: Optional[str] = None, **extra: Any,
    ) -> P:
        """A successful payment that counts towards the sale at once (cash, or card/UPI keyed in by hand)."""
        shift = await get_open_shift(db, user.id)
        record: Any = self.payment_model(
            sale_id=sale.id, branch_id=sale.branch_id, payment_mode=mode, amount=amount, reference_id=reference_id,
            status=PaymentStatus.SUCCESS.value, received_by_id=user.staff_id, shift_id=shift.id if shift else None,
            idempotency_key=idempotency_key, **extra,
        )
        sale.payments.append(record)
        sale.paid_amount = q(sale.paid_amount + amount)
        return record

    async def settle_if_paid(self, db: AsyncSession, sale: Any) -> None:
        if sale.balance_due > ZERO:
            sale.status = BillStatus.PARTIALLY_PAID.value
            self.event(db, sale, "payment_recorded", {"balance_due": str(sale.balance_due)})
            return
        sale.status = BillStatus.PAID.value
        sale.settled_at = utcnow()
        await self.on_settled(db, sale)

    async def flush_payment(self, db: AsyncSession) -> None:
        try:
            await db.flush()
        except IntegrityError as exc:
            await db.rollback()
            raise Conflict("Duplicate payment", code="duplicate_payment") from exc

    # --- the tenders -------------------------------------------------------------------------------------------------------

    async def record(
        self, db: AsyncSession, user: CurrentUser, sale_id: uuid.UUID, *, mode: str, amount: Decimal,
        reference_id: Optional[str] = None, idempotency_key: Optional[str] = None,
    ) -> Tuple[P, S, Decimal]:
        """Record one tender. Cash may exceed the balance (change is returned); other modes may not."""
        sale: Any = await self.load_payable(db, user, sale_id)
        balance = sale.balance_due
        change = ZERO
        if amount > balance:
            if mode != CASH:
                raise ValidationFailed("Payment exceeds the balance due", code="overpayment",
                                       details={"balance_due": str(balance)})
            change, amount = amount - balance, balance
        if amount <= ZERO:
            raise ValidationFailed("Nothing is owed on this sale", code="nothing_due")
        payment: Any = await self.add_payment(db, user, sale, mode=mode, amount=amount, reference_id=reference_id,
                                              idempotency_key=idempotency_key)
        await self.settle_if_paid(db, sale)
        await self.flush_payment(db)
        await commit(db)
        sale = await self._reload(db, sale.id)
        return next(p for p in sale.payments if p.id == payment.id), sale, change

    async def split(
        self, db: AsyncSession, user: CurrentUser, sale_id: uuid.UUID, tenders: Sequence[Tuple[str, Decimal, Optional[str]]],
    ) -> S:
        """Settle the whole balance across several tenders (mode, amount, reference) at once."""
        sale: Any = await self.load_payable(db, user, sale_id)
        total = sum((amount for _, amount, _ in tenders), ZERO)
        if total != sale.balance_due:
            raise ValidationFailed("Split tenders must add up exactly to the balance due", code="split_mismatch",
                                   details={"balance_due": str(sale.balance_due), "tendered": str(total)})
        for mode, amount, reference in tenders:
            await self.add_payment(db, user, sale, mode=mode, amount=amount, reference_id=reference)
        await self.settle_if_paid(db, sale)
        await commit(db)
        return await self._reload(db, sale.id)  # type: ignore[no-any-return]

    async def start_terminal(
        self, db: AsyncSession, user: CurrentUser, sale_id: uuid.UUID, *, mode: str, amount: Decimal, provider: str,
        idempotency_key: Optional[str] = None,
    ) -> Tuple[P, S]:
        """
        Record a card/UPI payment as pending before the terminal is asked to charge it, so a crash between the charge and the
        answer can never lose it. It counts towards the sale only once approved.
        """
        sale: Any = await self.load_payable(db, user, sale_id)
        if amount > sale.balance_due:
            raise ValidationFailed("Payment exceeds the balance due", code="overpayment",
                                   details={"balance_due": str(sale.balance_due)})
        shift = await get_open_shift(db, user.id)
        record: Any = self.payment_model(
            sale_id=sale.id, branch_id=sale.branch_id, payment_mode=mode, amount=amount, status=PaymentStatus.PENDING.value,
            received_by_id=user.staff_id, shift_id=shift.id if shift else None, idempotency_key=idempotency_key,
            provider=provider, provider_meta={"billing_ref_no": terminal_ref(), "started_by": str(user.id)},
        )
        sale.payments.append(record)
        await self.flush_payment(db)
        self.event(db, sale, "payment_started", {"payment_id": str(record.id)})
        await commit(db)
        sale = await self._reload(db, sale.id)
        return next(p for p in sale.payments if p.id == record.id), sale

    async def _payment_and_sale(self, db: AsyncSession, user: CurrentUser, payment_id: uuid.UUID) -> Tuple[Any, Any]:
        payment: Any = await get_or_404(db, self.payment_model, payment_id)
        if payment.sale_id is None:  # money received against an account, not a tender on a sale
            raise InvalidTransition("This payment is not on a sale", code="not_terminal_payment")
        sale: Any = await self.load(db, payment.sale_id, for_update=True)
        require_branch_access(user, sale.branch_id)
        return next(p for p in sale.payments if p.id == payment.id), sale

    async def complete_terminal(
        self, db: AsyncSession, user: CurrentUser, payment_id: uuid.UUID, *, outcome: str, approval_code: Optional[str],
        details: Dict[str, Any], source: str, message: Optional[str],
    ) -> Tuple[P, S]:
        """Record the terminal's answer. Approved: it counts and may settle the sale. Declined: kept as failed."""
        payment, sale = await self._payment_and_sale(db, user, payment_id)
        if payment.provider == "manual":
            raise InvalidTransition("This payment was not taken on a terminal", code="not_terminal_payment")
        approved = outcome == "approved"
        final = PaymentStatus.SUCCESS.value if approved else PaymentStatus.FAILED.value
        if payment.status != PaymentStatus.PENDING.value:
            if payment.status == final:
                return payment, sale
            raise Conflict(f"This payment is already {payment.status}", code="payment_already_completed",
                           details={"status": payment.status})

        payment.status = final
        payment.provider_meta = {
            **(payment.provider_meta or {}), **safe_terminal_details(details),
            "outcome": outcome, "source": source, "message": message, "completed_by": str(user.id),
        }
        if approved:
            payment.reference_id = approval_code
            sale.paid_amount = q(sale.paid_amount + payment.amount)
            await self.settle_if_paid(db, sale)
        else:
            self.event(db, sale, "payment_failed", {"payment_id": str(payment.id)})
        await commit(db)
        sale = await self._reload(db, sale.id)
        return next(p for p in sale.payments if p.id == payment.id), sale

    async def reverse_terminal(
        self, db: AsyncSession, user: CurrentUser, payment_id: uuid.UUID, *, reason: str, details: Dict[str, Any],
        approver_pin: Optional[str], check_only: bool = False, redis_client: Any = None,
    ) -> Tuple[P, S]:
        """
        A terminal payment voided on the device (same day): it stops counting and the sale reopens for the amount. Needs a
        manager and is audited.
        """
        payment, sale = await self._payment_and_sale(db, user, payment_id)
        if payment.status == PaymentStatus.REVERSED.value:
            return payment, sale
        if payment.status != PaymentStatus.SUCCESS.value or payment.provider == "manual":
            raise InvalidTransition("Only approved card or UPI terminal payments can be reversed", code="not_reversible",
                                    details={"status": payment.status, "provider": payment.provider})
        approver_id = await resolve_approver(db, user, sale.branch_id, approver_pin, redis_client)
        if check_only:
            return payment, sale

        was_paid = sale.status == BillStatus.PAID.value
        payment.status = PaymentStatus.REVERSED.value
        payment.provider_meta = {
            **(payment.provider_meta or {}),
            "reversal": {**safe_terminal_details(details), "reason": reason, "by": str(user.id),
                         "approved_by": str(approver_id), "at": utcnow().isoformat()},
        }
        sale.paid_amount = q(sale.paid_amount - payment.amount)
        nothing_settled = sale.paid_amount <= ZERO and sale.balance_due == sale.grand_total
        sale.status = BillStatus.UNPAID.value if nothing_settled else BillStatus.PARTIALLY_PAID.value
        sale.settled_at = None
        if was_paid:
            await self.on_reopened(db, sale)

        record_audit(
            db, branch_id=sale.branch_id, action=AuditAction.PAYMENT_REVERSE, actor_id=user.staff_id, approver_id=approver_id,
            entity_type=self.entity_type, entity_id=sale.id, amount=payment.amount,
            details={"sale_number": sale.sale_number, "payment_id": str(payment.id), "mode": payment.payment_mode,
                     "provider": payment.provider, "billing_ref_no": (payment.provider_meta or {}).get("billing_ref_no"),
                     "reason": reason},
        )
        self.event(db, sale, "payment_reversed", {"payment_id": str(payment.id), "balance_due": str(sale.balance_due)})
        await commit(db)
        sale = await self._reload(db, sale.id)
        return next(p for p in sale.payments if p.id == payment.id), sale
