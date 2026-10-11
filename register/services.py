"""
Counter / register-session business logic, kept out of the views so the POS screen, the close-register modal and the tests
all use the same numbers.

  Counter (Register)  = the physical terminal.
  RegisterSession     = one open -> close cash shift on a counter. Sales, payments, expenses, returns link to the session.
"""
import logging
from decimal import Decimal

from django.apps import apps
from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from audit.models import AuditLog
from audit.services import log_action
from .models import Register, RegisterSession

logger = logging.getLogger(__name__)
ZERO = Decimal("0")
CENT = Decimal("0.01")


class RegisterError(Exception):
    """A problem the cashier can fix (shown to them as a normal message, never as a traceback)."""


def q2(value) -> Decimal:
    return Decimal(value or 0).quantize(CENT)


def parse_amount(raw, label="Amount"):
    """Decimal >= 0 from user input, or RegisterError."""
    try:
        value = Decimal(str(raw).strip() if raw not in (None, "") else "0")
    except Exception:                                    # noqa: BLE001 - InvalidOperation and friends
        raise RegisterError(f"{label} must be a number (0 or more).")
    if not value.is_finite() or value < 0:
        raise RegisterError(f"{label} must be a number (0 or more).")
    return q2(value)


# ---------------------------------------------------------------- who may use which counter
def allowed_registers(user, store):
    """Active counters of the branch that this user is allowed to open."""
    qs = Register.objects.filter(store=store, is_active=True).select_related("warehouse")
    if user.is_superuser or user.has_perm("register.change_register"):
        return qs
    return qs.filter(Q(assigned_users__isnull=True) | Q(assigned_users=user)).distinct()


def open_session_of(register):
    return RegisterSession.objects.filter(register=register, status="open").select_related("opened_by").first()


# ---------------------------------------------------------------- open / resume
def open_session(register, user, opening_cash, note=""):
    if not register.is_active:
        raise RegisterError("This counter is switched off. Ask a manager to activate it.")
    if not register.can_be_used_by(user):
        raise RegisterError("You are not assigned to this counter.")
    try:
        with transaction.atomic():
            if RegisterSession.objects.select_for_update().filter(register=register, status="open").exists():
                raise RegisterError("This counter already has an open register session.")
            session = RegisterSession.objects.create(
                register=register, opened_by=user, opening_cash=q2(opening_cash), opening_note=(note or "")[:255])
            log_action(AuditLog.CREATED, session, user=user, note=f"Register opened on {register.name}",
                       new={"register": register.name, "opening_cash": q2(opening_cash)})
    except IntegrityError:                               # two cashiers pressed "open" at the same instant
        raise RegisterError("This counter already has an open register session.")
    return session


def can_resume(session, user):
    """You can continue a session you opened; managers can continue anybody's (e.g. after the cashier logged out)."""
    return session.status == "open" and session.register.can_be_used_by(user) and (
        session.opened_by_id == user.id or user.is_superuser or user.has_perm("register.change_registersession"))


# ---------------------------------------------------------------- the numbers
def _payments(session):
    from payments.models import Payment
    # payments recorded before the session link existed are found through their sale
    return Payment.objects.filter(
        Q(register_session=session) | Q(register_session__isnull=True, sale__register_session=session)).exclude(status="voided")


def _returns(session):
    """(total refunded, cash refunded, cash dropped to the safe). With the ERP POS layer the refunds come from its PosReturn
    records; without it, from payments.Refund rows linked to the session."""
    if not apps.is_installed("pos_erp"):
        from payments.models import Refund
        refunds = Refund.objects.filter(register_session=session)
        total = refunds.aggregate(t=Sum("amount"))["t"] or ZERO
        cash = refunds.filter(method="cash").aggregate(t=Sum("amount"))["t"] or ZERO
        return q2(total), q2(cash), ZERO
    from pos_erp.models import CashDrop, PosReturn
    refunds = PosReturn.objects.filter(register_session=session)
    total = refunds.aggregate(t=Sum("refund_amount"))["t"] or ZERO
    cash = refunds.filter(refund_method="cash").aggregate(t=Sum("refund_amount"))["t"] or ZERO
    drops = CashDrop.objects.filter(register_session=session).aggregate(t=Sum("amount"))["t"] or ZERO
    return q2(total), q2(cash), q2(drops)


def session_summary(session):
    """Everything the 'Close register' screen shows (and the closing record freezes)."""
    from payments.models import CASH, PaymentMethod
    from sales.models import Sale

    labels = PaymentMethod.label_map(session.register.store)
    rows = _payments(session).values("method").annotate(total=Sum("amount"), n=Count("id")).order_by("method")
    by_method = [{"code": r["method"], "name": labels.get(r["method"], r["method"]), "total": q2(r["total"]), "count": r["n"]} for r in rows]
    totals = {r["code"]: r["total"] for r in by_method}
    cash_sales = totals.get(CASH, ZERO)
    card_sales = totals.get("card", ZERO)
    bank_sales = totals.get("bank_transfer", ZERO)
    other_payments = q2(sum((t for code, t in totals.items() if code not in (CASH, "card", "bank_transfer")), ZERO))

    sales = Sale.objects.filter(register_session=session).exclude(status__in=["held", "cancelled"])
    agg = sales.aggregate(total=Sum("total_amount"), n=Count("id"))
    refunds_total, cash_refunds, cash_drops = _returns(session)
    cash_expenses = q2(session.cash_expenses_total)
    opening = q2(session.opening_cash)
    expected = q2(opening + cash_sales - cash_expenses - cash_refunds - cash_drops)

    closed = session.status == "closed"
    return {
        "session_id": session.pk,
        "session_number": session.number,
        "counter": session.register.name,
        "cashier": session.opened_by.get_full_name() or session.opened_by.username,
        "opened_at": session.opened_at,
        "closed_at": session.closed_at if closed else None,
        "opening_cash": opening,
        "cash_sales": cash_sales,
        "card_sales": card_sales,
        "bank_sales": bank_sales,
        "other_payments": other_payments,
        "by_method": by_method,
        "refunds": refunds_total,
        "cash_refunds": cash_refunds,
        "cash_drops": cash_drops,
        "expenses": cash_expenses,
        "expected_cash": expected,
        "expected_closing_amount": expected,
        "total_sales": q2(agg["total"]),
        "transactions": agg["n"] or 0,
        "total_received": q2(sum((r["total"] for r in by_method), ZERO)),
    }


def jsonable(summary):
    """Same dict with Decimals/datetimes as strings (for JSON responses and the frozen closing record)."""
    out = {}
    for key, value in summary.items():
        if isinstance(value, Decimal):
            out[key] = str(value)
        elif hasattr(value, "isoformat"):
            out[key] = value.isoformat()
        elif key == "by_method":
            out[key] = [{**row, "total": str(row["total"])} for row in value]
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------- close
def close_session(session_id, user, counted, note=""):
    """Closes the session in one transaction: freezes the closing figures, removes the cart, writes the audit row.
    Raises RegisterError if it is not open any more. Returns the closed session."""
    with transaction.atomic():
        session = RegisterSession.objects.select_for_update().select_related("register", "register__store", "opened_by").get(pk=session_id)
        if session.status != "open":
            raise RegisterError("This register session is already closed.")
        counted = q2(counted)
        summary = session_summary(session)
        now = timezone.now()
        difference = q2(counted - summary["expected_cash"])
        session.status = "closed"
        session.closed_by = user
        session.closed_at = now
        session.closing_cash_counted = counted
        session.note = (note or "")[:2000]
        session.expected_cash_at_close = summary["expected_cash"]
        session.total_sales_at_close = summary["total_sales"]
        session.transactions_at_close = summary["transactions"]
        frozen = jsonable({**summary, "closed_at": now})
        frozen.update({"counted_cash": str(counted), "difference": str(difference)})
        session.closing_summary = frozen
        session.save()
        from sales.models import PosCart
        PosCart.objects.filter(register_session=session).delete()
        log_action(AuditLog.OTHER, session, user=user, note=f"Register closed ({session.register.name}), difference {difference}",
                   new={"counted_cash": counted, "expected_cash": summary["expected_cash"], "difference": difference})
    if apps.is_installed("pos_erp"):                    # books cash over/short when POS accounting is switched on; never blocks closing
        from pos_erp import hooks
        hooks.on_register_closed(session, user)
    return session
