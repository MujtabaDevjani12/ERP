"""
POS upgrade services.

Accounting (ON by default; PosSettings.post_to_accounting switches it off):
  sale completed     Dr Accounts receivable      Cr Sales (retail), Cr Output tax
                     Dr Cost of goods sold       Cr Inventory
  each payment       Dr Cash / Bank / Gift-card liability   Cr Accounts receivable
  return             Dr Sales returns, Dr Output tax        Cr Accounts receivable
                     Dr Inventory  Cr Cost of goods sold     (restocked goods)
  cash refund        Dr Accounts receivable      Cr Cash / Bank
  shift close        cash over / short against the counted cash
  cash drop          Dr Cash in safe             Cr Cash in hand   (only if a safe account is set)
Posting never blocks a sale: failures are recorded in PosPostingIssue and can be retried.
"""
import logging
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounting.models import JournalEntry
from accounting.services import create_journal_entry
from audit.models import AuditLog
from audit.services import log_action
from company.utils import company_of_store
from core.errors import ERPError
from core.money import q2, q3
from numbering.services import next_reference
from stock.models import StockLedgerEntry
from stock.services import inventory_account_key, post_stock_movement
from .models import CashDrop, PosPostingIssue, PosReturn, PosSettings

logger = logging.getLogger(__name__)
ZERO = Decimal("0")

METHOD_ACCOUNT = {"cash": "cash", "card": "bank", "bank_transfer": "bank", "cheque": "bank", "gift_card": "gift_card_liability"}


# ================================================================== settings
def settings_for(company):
    obj, _ = PosSettings.objects.get_or_create(company=company)
    return obj


def accounting_enabled(store) -> bool:
    company = getattr(store, "company", None)
    if company is None:
        return False
    # settings_for() creates the row on first use, so the model default (ON) also applies to companies that never opened the settings page
    return settings_for(company).post_to_accounting


# ================================================================== helpers
def _sale_cost(sale):
    """Cost of goods in the sale: from the stock ledger when it recorded the sale, else from the items' cost price."""
    entries = StockLedgerEntry.objects.filter(reference_type="POS_SALE", reference_no=sale.reference)
    if entries.exists():
        return q2(-sum((e.value_change for e in entries), ZERO))
    return q2(sum((i.quantity * i.variant.cost_price for i in sale.items.select_related("variant")), ZERO))


def _record_issue(company, doc_type, doc, error):
    PosPostingIssue.objects.create(company=company, doc_type=doc_type, doc_id=doc.pk, reference=getattr(doc, "reference", "")[:40], error=str(error)[:2000])
    logger.warning("POS accounting post failed for %s %s: %s", doc_type, doc.pk, error)


def _guarded(company, doc_type, doc, func):
    """Run `func` in a savepoint; on failure record an issue instead of breaking the sale."""
    try:
        with transaction.atomic():
            return func()
    except Exception as exc:                         # noqa: BLE001 - POS must keep working
        _record_issue(company, doc_type, doc, exc)
        return None


# ================================================================== posting
def post_pos_sale(sale, user=None):
    """Revenue, receivable and cost of sales for a COMPLETED sale. Idempotent."""
    if sale.status not in ("completed", "returned", "partially_returned"):
        return None
    company = company_of_store(sale.store)
    net = q2(sale.total_amount - sale.tax_amount)         # = subtotal - discount for a plain sale; also carries any shipping charge / rounding so the entry always balances
    cost = _sale_cost(sale)
    if sale.total_amount <= 0:
        return None
    lines = [{"account": "ar", "debit": sale.total_amount, "party_type": "customer" if sale.customer_id else "", "party_id": sale.customer_id,
              "description": sale.reference},
             {"account": "sales_retail", "credit": net}]
    if sale.tax_amount > 0:
        lines.append({"account": "output_tax", "credit": sale.tax_amount})
    entry = create_journal_entry(company, date=timezone.localtime(sale.created_at).date(), narration=f"POS sale {sale.reference}",
                                 source=("pos_sale", sale.pk), store=sale.store, user=user, lines=lines)
    if cost > 0:
        create_journal_entry(company, date=timezone.localtime(sale.created_at).date(), narration=f"POS cost of sale {sale.reference}",
                             source=("pos_sale_cost", sale.pk), store=sale.store, user=user,
                             lines=[{"account": "cogs", "debit": cost}, {"account": inventory_account_key(sale.warehouse), "credit": cost}])
    return entry


def post_pos_payment(payment, user=None):
    """Money received against a sale clears the receivable. Idempotent."""
    sale = payment.sale
    company = company_of_store(sale.store)
    account = METHOD_ACCOUNT.get(payment.method, "bank")
    # a customer may hand over more than the bill (change is given back): only the part that settles the bill is income
    earlier = sale.payments.filter(pk__lt=payment.pk).aggregate(t=Sum("amount"))["t"] or ZERO
    amount = q2(min(payment.amount, sale.total_amount - earlier))
    if amount <= 0:
        return None
    return create_journal_entry(
        company, voucher_type="RV", date=timezone.localtime(payment.paid_at).date() if payment.paid_at else timezone.localdate(),
        narration=f"POS payment {payment.reference} ({payment.method}) for {sale.reference}", source=("pos_payment", payment.pk),
        store=sale.store, user=user,
        lines=[{"account": account, "debit": amount},
               {"account": "ar", "credit": amount, "party_type": "customer" if sale.customer_id else "", "party_id": sale.customer_id}])


def post_pos_return(pos_return, user=None):
    company = company_of_store(pos_return.sale_return.sale.store)
    sale = pos_return.sale_return.sale
    lines = [{"account": "sales_returns", "debit": pos_return.net_amount},
             {"account": "ar", "credit": pos_return.net_amount + pos_return.tax_amount, "party_type": "customer" if sale.customer_id else "",
              "party_id": sale.customer_id}]
    if pos_return.tax_amount > 0:
        lines.append({"account": "output_tax", "debit": pos_return.tax_amount})
    create_journal_entry(company, voucher_type="CREDIT_NOTE", narration=f"POS return {pos_return.sale_return.reference}",
                         source=("pos_return", pos_return.pk), store=sale.store, user=user, lines=lines)
    if pos_return.restocked:
        value = ZERO
        from sales.pos_stock import stock_effects
        for ri in pos_return.sale_return.items.select_related("sale_item__variant__product"):
            for stock_variant, back in stock_effects(ri.sale_item.variant, ri.quantity):      # a service brings nothing back; a combo its parts
                value += q2(back * _unit_cost_of(ri.sale_item, stock_variant))
        value = q2(value)
        if value > 0:
            create_journal_entry(company, narration=f"POS return goods back in stock {pos_return.sale_return.reference}",
                                 source=("pos_return_stock", pos_return.pk), store=sale.store, user=user,
                                 lines=[{"account": inventory_account_key(sale.warehouse), "debit": value}, {"account": "cogs", "credit": value}])
    if pos_return.refund_amount > 0:
        account = METHOD_ACCOUNT.get(pos_return.refund_method or "cash", "cash")
        create_journal_entry(company, voucher_type="PV", narration=f"POS refund {pos_return.sale_return.reference}",
                             source=("pos_refund", pos_return.pk), store=sale.store, user=user,
                             lines=[{"account": "ar", "debit": pos_return.refund_amount, "party_type": "customer" if sale.customer_id else "",
                                     "party_id": sale.customer_id},
                                    {"account": account, "credit": pos_return.refund_amount}])


def _unit_cost_of(sale_item, variant=None):
    variant = variant or sale_item.variant
    entries = StockLedgerEntry.objects.filter(reference_type="POS_SALE", reference_no=sale_item.sale.reference, variant=variant)
    if entries.exists():
        total_qty = -sum((e.quantity for e in entries), ZERO)
        total_value = -sum((e.value_change for e in entries), ZERO)
        if total_qty > 0:
            return total_value / total_qty
    return variant.cost_price


# ================================================================== hooks used by the POS views
def on_sale_completed(sale, payments=()):
    """Called by the POS right after a completed sale (and its payments) is saved."""
    if sale.status != "completed" or not accounting_enabled(sale.store):
        return
    company = company_of_store(sale.store)
    _guarded(company, "sale", sale, lambda: post_pos_sale(sale))
    for p in payments:
        _guarded(company, "payment", p, lambda p=p: post_pos_payment(p))


def on_payment_added(payment):
    sale = payment.sale
    if not accounting_enabled(sale.store) or sale.status == "held":
        return
    company = company_of_store(sale.store)
    # the sale must be posted before money clears it
    _guarded(company, "sale", sale, lambda: post_pos_sale(sale))
    _guarded(company, "payment", payment, lambda: post_pos_payment(payment))


# ================================================================== returns
def returnable_quantities(sale):
    """{sale_item_id: quantity still returnable}"""
    out = {}
    for item in sale.items.all():
        returned = sum((r.quantity for r in item.return_items.all()), ZERO)
        out[item.pk] = item.quantity - returned
    return out


@transaction.atomic
def process_pos_return(sale, items, reason, *, user=None, session=None, refund_method="cash", restock=True):
    """items: [{"sale_item": SaleItem, "quantity": 1}]. Creates the legacy SaleReturn (+items) and Refund, puts good stock back,
    updates the sale status and, when enabled, posts the accounting. Returns the PosReturn."""
    from configuration.models import NumberingPrefix
    from payments.models import Refund
    from sales.models import SaleItem, SaleReturn, SaleReturnItem

    if not (reason or "").strip():
        raise ERPError("A reason is required for a return.")
    if not items:
        raise ERPError("Select at least one item to return.")
    sale = type(sale).objects.select_for_update().select_related("store", "warehouse", "customer").get(pk=sale.pk)
    if sale.status not in ("completed", "partially_returned"):
        raise ERPError(f"A sale that is '{sale.get_status_display()}' cannot be returned.")
    left = returnable_quantities(sale)
    net, tax, consumed, prepared = ZERO, ZERO, {}, []
    for it in items:
        sale_item = SaleItem.objects.select_for_update().get(pk=it["sale_item"].pk)
        if sale_item.sale_id != sale.pk:
            raise ERPError("An item does not belong to this sale.")
        qty = q3(it["quantity"])
        consumed[sale_item.pk] = consumed.get(sale_item.pk, ZERO) + qty
        if qty <= 0 or consumed[sale_item.pk] > left[sale_item.pk]:
            raise ERPError(f"{sale_item.variant}: only {left[sale_item.pk]} can still be returned.")
        from sales.returns import line_parts
        line_net, line_tax = line_parts(sale_item, qty, sale_item.quantity - left[sale_item.pk] + consumed[sale_item.pk] - qty)
        prepared.append((sale_item, qty))
        net += line_net
        tax += line_tax
    net, tax = q2(net), q2(tax)
    refund_total = q2(net + tax)

    settings_obj = settings_for(company_of_store(sale.store))
    approver = None
    if settings_obj.return_approval_threshold > 0 and refund_total > settings_obj.return_approval_threshold:
        if user is not None and not user.has_perm("rbac.approve_document"):
            raise PermissionDenied(f"Returns above {settings_obj.return_approval_threshold} need a manager's approval.")
        approver = user if getattr(user, "is_authenticated", False) else None

    sale_return = SaleReturn.objects.create(reference=NumberingPrefix.next_reference(NumberingPrefix.SALE_RETURN), sale=sale, reason=reason,
                                            processed_by=user if getattr(user, "is_authenticated", False) else None)
    for sale_item, qty in prepared:
        SaleReturnItem.objects.create(sale_return=sale_return, sale_item=sale_item, quantity=qty)
        if restock:
            from sales.pos_stock import stock_effects
            for stock_variant, back in stock_effects(sale_item.variant, qty):
                post_stock_movement(stock_variant, sale.warehouse, back, reference_type="POS_RETURN", reference_no=sale_return.reference,
                                    user=user if getattr(user, "is_authenticated", False) else None, remarks=reason[:200],
                                    unit_cost=_unit_cost_of(sale_item, stock_variant), allow_negative=True)
    refund = None
    if refund_total > 0 and refund_method:
        refund = Refund.objects.create(reference=NumberingPrefix.next_reference("refund"), order=sale, amount=refund_total,
                                       method=refund_method, reason=reason,
                                       processed_by=user if getattr(user, "is_authenticated", False) else None)
    pos_return = PosReturn.objects.create(
        sale_return=sale_return, register_session=session, refund=refund, net_amount=net, tax_amount=tax,
        refund_amount=refund_total if refund else ZERO, refund_method=refund_method if refund else "", restocked=restock, approved_by=approver)

    fully = all(v <= 0 for v in returnable_quantities(sale).values())
    sale.status = "returned" if fully else "partially_returned"
    sale.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.CREATED, sale_return, user=user, document_number=sale_return.reference, note=f"refund {refund_total}",
               company=company_of_store(sale.store))
    if accounting_enabled(sale.store):
        company = company_of_store(sale.store)
        _guarded(company, "sale", sale, lambda: post_pos_sale(sale))
        _guarded(company, "return", sale_return, lambda: post_pos_return(pos_return))
    return pos_return


# ================================================================== shifts / cash
def shift_cash_adjustments(session) -> Decimal:
    """Cash that left the till outside sales/expenses: cash refunds and drops (subtracted from expected cash)."""
    refunds = PosReturn.objects.filter(register_session=session, refund_method="cash").aggregate(t=Sum("refund_amount"))["t"] or ZERO
    drops = CashDrop.objects.filter(register_session=session).aggregate(t=Sum("amount"))["t"] or ZERO
    return q2(refunds + drops)


@transaction.atomic
def record_cash_drop(session, amount, *, user=None, reason=""):
    if session.status != "open":
        raise ERPError("The register session is closed.")
    amount = q2(amount)
    if amount <= 0:
        raise ERPError("Cash drop must be greater than zero.")
    if amount > session.expected_cash:
        raise ERPError(f"The till only holds {session.expected_cash}; cannot drop {amount}.")
    drop = CashDrop.objects.create(register_session=session, amount=amount, reason=reason,
                                   created_by=user if getattr(user, "is_authenticated", False) else None)
    store = session.register.store
    if accounting_enabled(store):
        company = company_of_store(store)
        safe = settings_for(company).safe_account
        if safe is not None:
            _guarded(company, "cash_drop", drop, lambda: create_journal_entry(
                company, voucher_type="CONTRA", narration=f"Cash drop {session.register.name}", source=("cash_drop", drop.pk), store=store,
                user=user, lines=[{"account": safe, "debit": amount}, {"account": "cash", "credit": amount}]))
    return drop


def shift_report(session):
    """Everything about one till session."""
    from payments.models import Payment
    sales = session.sales.all()
    by_method = {r["method"]: r["t"] for r in Payment.objects.filter(sale__register_session=session).values("method").annotate(t=Sum("amount"))}
    refunds = PosReturn.objects.filter(register_session=session).aggregate(t=Sum("refund_amount"))["t"] or ZERO
    return {
        "opening_cash": session.opening_cash, "sales_count": sales.exclude(status="held").count(),
        "sales_total": q2(sum((s.total_amount for s in sales.exclude(status="held")), ZERO)), "by_method": by_method,
        "cash_expenses": session.cash_expenses_total, "cash_refunds": q2(PosReturn.objects.filter(register_session=session, refund_method="cash").aggregate(
            t=Sum("refund_amount"))["t"] or ZERO), "refunds_total": q2(refunds),
        "cash_drops": q2(CashDrop.objects.filter(register_session=session).aggregate(t=Sum("amount"))["t"] or ZERO),
        "expected_cash": session.expected_cash, "counted": session.closing_cash_counted, "variance": session.variance,
    }


def on_register_closed(session, user=None):
    """Called after the legacy close view stored the counted cash: books cash over / short when accounting is on."""
    store = session.register.store
    if not accounting_enabled(store) or session.closing_cash_counted is None:
        return None
    company = company_of_store(store)
    variance = q2(session.closing_cash_counted - session.expected_cash)
    if variance == 0:
        return None
    lines = ([{"account": "cash", "debit": variance}, {"account": "cash_over_short", "credit": variance}] if variance > 0 else
             [{"account": "cash_over_short", "debit": -variance}, {"account": "cash", "credit": -variance}])
    return _guarded(company, "shift", session, lambda: create_journal_entry(
        company, narration=f"Cash {'over' if variance > 0 else 'short'} {session.register.name} shift {session.pk}", source=("pos_shift", session.pk),
        store=store, user=user, lines=lines))


# ================================================================== reports
def daily_sales_summary(store, day):
    from payments.models import Payment
    from sales.models import Sale
    sales = Sale.objects.filter(store=store, created_at__date=day).exclude(status__in=["held", "cancelled"])
    methods = {r["method"]: r["t"] for r in Payment.objects.filter(sale__in=sales).values("method").annotate(t=Sum("amount"))}
    returns = PosReturn.objects.filter(sale_return__sale__store=store, created_at__date=day).aggregate(t=Sum("refund_amount"))["t"] or ZERO
    return {"count": sales.count(), "gross": q2(sum((s.subtotal for s in sales), ZERO)), "discount": q2(sum((s.discount_amount for s in sales), ZERO)),
            "tax": q2(sum((s.tax_amount for s in sales), ZERO)), "total": q2(sum((s.total_amount for s in sales), ZERO)),
            "by_method": methods, "returns": q2(returns)}


def cashier_report(store, date_from, date_to):
    from sales.models import Sale
    qs = Sale.objects.filter(store=store, created_at__date__range=(date_from, date_to)).exclude(status__in=["held", "cancelled"])
    return list(qs.values("biller__username").annotate(sales=Sum("total_amount")).order_by("-sales"))


def unresolved_issues(company):
    return PosPostingIssue.objects.filter(company=company, resolved=False)
