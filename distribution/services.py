"""
Sales & distribution services.

Accounting recap
  delivery challan:  Dr Cost of goods sold        Cr Inventory            (at average cost)
  sales invoice:     Dr Accounts receivable       Cr Sales, Cr Output tax
  customer receipt:  Dr Cash / Bank               Cr Accounts receivable
  sales return:      Dr Sales returns, Dr Output tax   Cr Accounts receivable   (credit note)
                     + for restocked goods: Dr Inventory  Cr Cost of goods sold
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from accounting.models import JournalEntry, JournalLine
from accounting.reports import aging_buckets
from accounting.services import create_journal_entry, reverse_entry
from approvals.services import find_workflow, submit_for_approval
from audit.models import AuditLog
from audit.services import log_action
from company.utils import company_of_store, company_of_warehouse
from core.errors import ERPError
from core.money import q2, q3, q4
from masters.services import compute_tax
from numbering.services import next_reference
from rbac.permissions import require
from stock.services import inventory_account_key, post_stock_movement
from .models import (CustomerReceipt, CustomerReceiptAllocation, DeliveryChallan, DeliveryChallanLine, ERPSalesReturn,
                     ERPSalesReturnLine, SalesInvoice, SalesInvoiceLine, SalesOrder, SalesOrderItem, SalesQuotation,
                     SalesQuotationItem)

ZERO = Decimal("0")


def _u(user):
    return user if (user is not None and getattr(user, "is_authenticated", False)) else None


def max_discount_percent():
    return Decimal(str(getattr(settings, "SALES_MAX_DISCOUNT_PERCENT", 10)))


def _price_lines(items):
    """Normalises [{"variant", "quantity", "unit_price"?, "discount_percent"?}]; returns (clean, subtotal_after_discount, discount)."""
    if not items:
        raise ERPError("Add at least one item.")
    clean, subtotal, discount = [], ZERO, ZERO
    for it in items:
        qty = q3(it["quantity"])
        price = q2(it["unit_price"] if it.get("unit_price") is not None else it["variant"].selling_price)
        pct = q2(it.get("discount_percent") or 0)
        if qty <= 0:
            raise ERPError("Quantities must be greater than zero.")
        if price < 0:
            raise ERPError("Prices cannot be negative.")
        if not 0 <= pct <= 100:
            raise ERPError("Discount must be between 0 and 100 percent.")
        gross = q2(qty * price)
        disc = q2(gross * pct / 100)
        clean.append({"variant": it["variant"], "quantity": qty, "unit_price": price, "discount_percent": pct})
        subtotal += gross - disc
        discount += disc
    return clean, q2(subtotal), q2(discount)


def _totals(subtotal, tax_rate):
    tax = ZERO
    if tax_rate is not None:
        if tax_rate.is_inclusive:
            raise ERPError("Sales documents need a tax-exclusive tax rate (enter prices without tax).")
        _, tax, _ = compute_tax(subtotal, tax_rate.rate)
    return tax, q2(subtotal + tax)


def _check_customer(company, customer):
    if company_of_store(customer.store).pk != company.pk:
        raise ERPError("The customer belongs to a different company.")
    if not customer.is_active:
        raise ERPError("The customer is inactive.")


def _profile(customer):
    return getattr(customer, "erp_profile", None)


# ================================================================== quotation
@transaction.atomic
def create_quotation(company, customer, warehouse, items, *, user=None, valid_until=None, sales_type="wholesale", tax_rate=None, terms=""):
    _check_customer(company, customer)
    clean, _, _ = _price_lines(items)
    q = SalesQuotation.objects.create(
        company=company, reference=next_reference(company, "quotation"), customer=customer, warehouse=warehouse,
        quotation_date=timezone.localdate(), valid_until=valid_until, sales_type=sales_type, tax_rate=tax_rate, terms=terms, created_by=_u(user))
    for c in clean:
        SalesQuotationItem.objects.create(quotation=q, **c)
    log_action(AuditLog.CREATED, q, user=user, document_number=q.reference, company=company)
    return q


def _quotation_state(q, status, user, note):
    q = SalesQuotation.objects.select_for_update().get(pk=q.pk)
    q.status = status
    q.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.OTHER, q, user=user, note=note, document_number=q.reference, company=q.company)
    return q


@transaction.atomic
def send_quotation(q, user=None):
    if q.status != SalesQuotation.DRAFT:
        raise ERPError("Only a draft quotation can be sent.")
    return _quotation_state(q, SalesQuotation.SENT, user, "sent to customer")


@transaction.atomic
def accept_quotation(q, user=None):
    q = SalesQuotation.objects.get(pk=q.pk)
    if q.status not in (SalesQuotation.DRAFT, SalesQuotation.SENT):
        raise ERPError("This quotation cannot be accepted.")
    if q.valid_until and q.valid_until < timezone.localdate():
        raise ERPError("This quotation has expired.")
    return _quotation_state(q, SalesQuotation.ACCEPTED, user, "accepted by customer")


@transaction.atomic
def reject_quotation(q, user=None):
    if q.status not in (SalesQuotation.DRAFT, SalesQuotation.SENT):
        raise ERPError("This quotation cannot be rejected.")
    return _quotation_state(q, SalesQuotation.REJECTED, user, "rejected by customer")


@transaction.atomic
def convert_quotation(q, user=None, delivery_date=None):
    q = SalesQuotation.objects.select_for_update().get(pk=q.pk)
    if q.status != SalesQuotation.ACCEPTED:
        raise ERPError("Accept the quotation before converting it to an order.")
    items = [{"variant": i.variant, "quantity": i.quantity, "unit_price": i.unit_price, "discount_percent": i.discount_percent}
             for i in q.items.all()]
    order = create_sales_order(q.company, q.customer, q.warehouse, items, user=user, delivery_date=delivery_date,
                               sales_type=q.sales_type, tax_rate=q.tax_rate, quotation=q)
    q.status = SalesQuotation.CONVERTED
    q.save(update_fields=["status", "updated_at"])
    return order


# ================================================================== sales order
@transaction.atomic
def create_sales_order(company, customer, warehouse, items, *, user=None, order_date=None, delivery_date=None,
                       sales_type="wholesale", tax_rate=None, note="", quotation=None):
    _check_customer(company, customer)
    if company_of_warehouse(warehouse).pk != company.pk:
        raise ERPError("The warehouse belongs to a different company.")
    clean, subtotal, discount = _price_lines(items)
    tax, total = _totals(subtotal, tax_rate)
    order = SalesOrder.objects.create(
        company=company, reference=next_reference(company, "sales_order"), customer=customer, warehouse=warehouse, quotation=quotation,
        order_date=order_date or timezone.localdate(), delivery_date=delivery_date, sales_type=sales_type, tax_rate=tax_rate,
        subtotal=subtotal, discount_amount=discount, tax_amount=tax, total=total, note=note, created_by=_u(user))
    for c in clean:
        SalesOrderItem.objects.create(order=order, **c)
    log_action(AuditLog.CREATED, order, user=user, document_number=order.reference, company=company)
    return order


def customer_exposure(company, customer, exclude_order=None):
    """What the customer already owes plus confirmed orders not yet invoiced."""
    owed = sum((i.outstanding for i in SalesInvoice.objects.filter(company=company, customer=customer, status="posted") if i.outstanding > 0), ZERO)
    open_orders = SalesOrder.objects.filter(company=company, customer=customer, status__in=["confirmed", "partially_delivered", "delivered"])
    if exclude_order is not None:
        open_orders = open_orders.exclude(pk=exclude_order.pk)
    pending = ZERO
    for o in open_orders.prefetch_related("items"):
        value = sum(((i.quantity - i.invoiced_quantity) * i.net_unit_price for i in o.items.all()), ZERO)
        if o.tax_rate_id:
            value += value * o.tax_rate.rate / 100
        pending += max(q2(value), ZERO)
    return q2(owed + pending)


def discount_exceeds_limit(order):
    return any(i.discount_percent > max_discount_percent() for i in order.items.all())


@transaction.atomic
def submit_sales_order(order, user=None, *, credit_override=False):
    order = SalesOrder.objects.select_for_update().select_related("customer", "company").get(pk=order.pk)
    if order.status not in (SalesOrder.DRAFT, SalesOrder.REJECTED):
        raise ERPError("Only a draft order can be submitted.")
    profile = _profile(order.customer)
    limit = profile.credit_limit if profile else ZERO
    if limit > 0:
        exposure = customer_exposure(order.company, order.customer, exclude_order=order) + order.total
        if exposure > limit:
            if not credit_override:
                raise ERPError(f"Credit limit exceeded: {order.customer} would owe {exposure} against a limit of {limit}. "
                               f"A manager can approve this with the credit override.")
            require(user, "approve_document")
            order.credit_override = True
            order.save(update_fields=["credit_override", "updated_at"])
            log_action(AuditLog.OTHER, order, user=user, note=f"credit limit override (exposure {exposure}, limit {limit})",
                       document_number=order.reference, company=order.company)
    if discount_exceeds_limit(order) and find_workflow(order.company, "sales_order", order.total) is None:
        if user is not None and not user.has_perm("rbac.approve_document"):
            raise PermissionDenied(f"A discount above {max_discount_percent()}% needs a manager's approval. "
                                   f"Ask someone with approval rights to submit this order.")
    submit_for_approval(order, "sales_order", company=order.company, user=user, amount=order.total, number=order.reference)
    order.refresh_from_db()
    return order


@transaction.atomic
def cancel_sales_order(order, user=None, reason=""):
    order = SalesOrder.objects.select_for_update().get(pk=order.pk)
    if order.challans.exclude(status=DeliveryChallan.CANCELLED).exists() or order.invoices.exclude(status=SalesInvoice.CANCELLED).exists():
        raise ERPError("Goods were already delivered or invoiced against this order.")
    if order.status == SalesOrder.CANCELLED:
        return order
    order.status = SalesOrder.CANCELLED
    order.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.CANCELLED, order, user=user, note=reason[:200], document_number=order.reference, company=order.company)
    return order


# ================================================================== delivery
@transaction.atomic
def create_delivery(order, lines, *, user=None, delivery_date=None, vehicle_no="", driver="", note=""):
    """lines: [{"order_item": item, "quantity": 10, "batch": optional}]. Stock leaves the warehouse and cost of sales is booked."""
    order = SalesOrder.objects.select_for_update().select_related("company", "warehouse").get(pk=order.pk)
    if order.status not in (SalesOrder.CONFIRMED, SalesOrder.PARTIAL):
        raise ERPError(f"The order is {order.get_status_display().lower()}; only confirmed orders can be delivered.")
    if not lines:
        raise ERPError("Enter at least one delivery line.")
    items = {i.pk: i for i in order.items.select_for_update().select_related("variant")}
    seen = set()
    for l in lines:
        item = items.get(l["order_item"].pk)
        if item is None:
            raise ERPError("A line does not belong to this order.")
        if item.pk in seen:
            raise ERPError("The same order line appears twice; combine the quantities.")
        seen.add(item.pk)
        qty = q3(l["quantity"])
        if qty <= 0:
            raise ERPError("Delivery quantity must be greater than zero.")
        if qty > item.quantity - item.delivered_quantity:
            raise ERPError(f"{item.variant}: delivering {qty} but only {item.quantity - item.delivered_quantity} remains on the order.")
    dc = DeliveryChallan.objects.create(
        company=order.company, reference=next_reference(order.company, "delivery_challan"), order=order, customer=order.customer,
        warehouse=order.warehouse, delivery_date=delivery_date or timezone.localdate(), vehicle_no=vehicle_no, driver=driver, note=note,
        created_by=_u(user))
    cost_total = ZERO
    for l in lines:
        item, qty = items[l["order_item"].pk], q3(l["quantity"])
        entry = post_stock_movement(item.variant, order.warehouse, -qty, reference_type="DELIVERY", reference_no=dc.reference,
                                    batch=l.get("batch"), user=user, remarks=f"To {order.customer.name}")
        DeliveryChallanLine.objects.create(challan=dc, order_item=item, variant=item.variant, batch=l.get("batch"), quantity=qty,
                                           unit_cost=entry.unit_cost)
        SalesOrderItem.objects.filter(pk=item.pk).update(delivered_quantity=F("delivered_quantity") + qty)
        cost_total += -entry.value_change
    cost_total = q2(cost_total)
    if cost_total > 0:
        create_journal_entry(order.company, date=dc.delivery_date, narration=f"{dc.reference} cost of sales ({order.reference})",
                             source=("delivery_challan", dc.pk), user=user, store=order.warehouse.store,
                             lines=[{"account": "cogs", "debit": cost_total},
                                    {"account": inventory_account_key(order.warehouse), "credit": cost_total}])
    done = all(i.delivered_quantity >= i.quantity for i in SalesOrderItem.objects.filter(order=order))
    order.status = SalesOrder.DELIVERED if done else SalesOrder.PARTIAL
    order.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.CREATED, dc, user=user, document_number=dc.reference, company=order.company)
    return dc


@transaction.atomic
def reverse_delivery(dc, user=None, reason=""):
    require(user, "reverse_document")
    dc = DeliveryChallan.objects.select_for_update().select_related("order", "warehouse").get(pk=dc.pk)
    if dc.status == DeliveryChallan.CANCELLED:
        raise ERPError("This challan was already cancelled.")
    if dc.lines.filter(invoiced_quantity__gt=0).exists():
        raise ERPError("Part of this delivery is invoiced; cancel the invoice first.")
    for line in dc.lines.select_related("variant"):
        post_stock_movement(line.variant, dc.warehouse, line.quantity, reference_type="DELIVERY_REVERSAL", reference_no=dc.reference,
                            unit_cost=line.unit_cost, batch=line.batch, user=user, remarks=reason[:200])
        SalesOrderItem.objects.filter(pk=line.order_item_id).update(delivered_quantity=F("delivered_quantity") - line.quantity)
    entry = JournalEntry.objects.filter(company=dc.company, source_type="delivery_challan", source_id=str(dc.pk)).first()
    if entry and entry.status == JournalEntry.POSTED:
        reverse_entry(entry, user, reason=f"{dc.reference} cancelled")
    dc.status = DeliveryChallan.CANCELLED
    dc.save(update_fields=["status", "updated_at"])
    order = SalesOrder.objects.get(pk=dc.order_id)
    any_delivered = any(i.delivered_quantity > 0 for i in order.items.all())
    order.status = SalesOrder.PARTIAL if any_delivered else SalesOrder.CONFIRMED
    order.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.REVERSED, dc, user=user, note=reason[:200], document_number=dc.reference, company=dc.company)
    return dc


# ================================================================== invoice
def _revenue_key(sales_type):
    return "sales_retail" if sales_type == "retail" else "sales_wholesale"


@transaction.atomic
def create_invoice(order, lines, *, user=None, invoice_date=None, due_date=None, note=""):
    """lines: [{"challan_line": dcl, "quantity": 10}] - only delivered, not-yet-invoiced goods can be billed.
    Prices come from the sales order (after the agreed discount)."""
    order = SalesOrder.objects.select_for_update().select_related("company", "customer", "tax_rate").get(pk=order.pk)
    if order.status in (SalesOrder.DRAFT, SalesOrder.PENDING, SalesOrder.REJECTED, SalesOrder.CANCELLED):
        raise ERPError("The order is not confirmed.")
    if not lines:
        raise ERPError("Select the delivered goods to invoice.")
    invoice_date = invoice_date or timezone.localdate()
    if due_date is None:
        profile = _profile(order.customer)
        days = profile.payment_term.days if (profile and profile.payment_term_id) else 0
        due_date = invoice_date + timedelta(days=days)
    prepared, subtotal, discount, consumed = [], ZERO, ZERO, {}
    for l in lines:
        dcl = DeliveryChallanLine.objects.select_for_update().select_related("challan", "order_item__variant").get(pk=l["challan_line"].pk)
        if dcl.challan.order_id != order.pk or dcl.challan.status == DeliveryChallan.CANCELLED:
            raise ERPError("A delivery line does not belong to this order or was cancelled.")
        qty = q3(l["quantity"])
        consumed[dcl.pk] = consumed.get(dcl.pk, ZERO) + qty
        if qty <= 0 or consumed[dcl.pk] > dcl.quantity - dcl.invoiced_quantity:
            raise ERPError(f"{dcl.variant}: only {dcl.quantity - dcl.invoiced_quantity} delivered and not yet invoiced.")
        item = dcl.order_item
        gross = q2(qty * item.unit_price)
        disc = q2(gross * item.discount_percent / 100)
        total_line = gross - disc
        prepared.append((dcl, item, qty, total_line))
        subtotal += total_line
        discount += disc
    subtotal = q2(subtotal)
    tax, total = _totals(subtotal, order.tax_rate)
    inv = SalesInvoice.objects.create(
        company=order.company, reference=next_reference(order.company, "sales_invoice", on_date=invoice_date), customer=order.customer,
        order=order, sales_type=order.sales_type, invoice_date=invoice_date, due_date=due_date, tax_rate=order.tax_rate,
        subtotal=subtotal, discount_amount=q2(discount), tax_amount=tax, total=total, note=note, created_by=_u(user))
    for dcl, item, qty, total_line in prepared:
        SalesInvoiceLine.objects.create(
            invoice=inv, challan_line=dcl, order_item=item, variant=dcl.variant, quantity=qty,
            unit_price=q2(total_line / qty) if qty else ZERO, list_price=item.unit_price, line_total=total_line, unit_cost=dcl.unit_cost)
        DeliveryChallanLine.objects.filter(pk=dcl.pk).update(invoiced_quantity=F("invoiced_quantity") + qty)
        SalesOrderItem.objects.filter(pk=item.pk).update(invoiced_quantity=F("invoiced_quantity") + qty)
    lines_jl = [{"account": "ar", "debit": total, "party_type": "customer", "party_id": order.customer_id,
                 "description": f"{order.customer.name} {inv.reference}"},
                {"account": _revenue_key(order.sales_type), "credit": subtotal}]
    if tax > 0:
        lines_jl.append({"account": "output_tax", "credit": tax})
    create_journal_entry(order.company, date=invoice_date, narration=f"{inv.reference} ({order.reference})",
                         source=("sales_invoice", inv.pk), user=user, lines=lines_jl)
    if all(i.invoiced_quantity >= i.quantity for i in SalesOrderItem.objects.filter(order=order)):
        order.status = SalesOrder.CLOSED
        order.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.POSTED, inv, user=user, document_number=inv.reference, company=order.company)
    return inv


@transaction.atomic
def deliver_and_invoice(order, *, user=None, invoice_date=None, vehicle_no=""):
    """Counter / wholesale shortcut: deliver everything still pending and invoice it in one step."""
    order = SalesOrder.objects.get(pk=order.pk)
    pending = [{"order_item": i, "quantity": i.quantity - i.delivered_quantity} for i in order.items.all() if i.quantity > i.delivered_quantity]
    dc = create_delivery(order, pending, user=user, vehicle_no=vehicle_no) if pending else None
    order.refresh_from_db()
    to_bill = [{"challan_line": l, "quantity": l.quantity - l.invoiced_quantity}
               for c in order.challans.exclude(status=DeliveryChallan.CANCELLED) for l in c.lines.all() if l.quantity > l.invoiced_quantity]
    if not to_bill:
        raise ERPError("Nothing left to invoice on this order.")
    return dc, create_invoice(order, to_bill, user=user, invoice_date=invoice_date)


@transaction.atomic
def cancel_invoice(invoice, user=None, reason=""):
    require(user, "reverse_document")
    inv = SalesInvoice.objects.select_for_update().get(pk=invoice.pk)
    if inv.status == SalesInvoice.CANCELLED:
        return inv
    if inv.paid_amount > 0 or inv.credited_amount > 0 or inv.returns.exists():
        raise ERPError("This invoice has receipts or returns against it; reverse those first.")
    entry = JournalEntry.objects.filter(company=inv.company, source_type="sales_invoice", source_id=str(inv.pk)).first()
    if entry and entry.status == JournalEntry.POSTED:
        reverse_entry(entry, user, reason=f"{inv.reference} cancelled. {reason}")
    for l in inv.lines.all():
        if l.challan_line_id:
            DeliveryChallanLine.objects.filter(pk=l.challan_line_id).update(invoiced_quantity=F("invoiced_quantity") - l.quantity)
        if l.order_item_id:
            SalesOrderItem.objects.filter(pk=l.order_item_id).update(invoiced_quantity=F("invoiced_quantity") - l.quantity)
    if inv.order_id and inv.order.status == SalesOrder.CLOSED:
        fully = all(i.delivered_quantity >= i.quantity for i in inv.order.items.all())
        SalesOrder.objects.filter(pk=inv.order_id).update(status=SalesOrder.DELIVERED if fully else SalesOrder.PARTIAL)
    inv.status = SalesInvoice.CANCELLED
    inv.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.CANCELLED, inv, user=user, note=reason[:200], document_number=inv.reference, company=inv.company)
    return inv


# ================================================================== receipts
def _receipt_account(receipt):
    if receipt.method == "cash":
        return "cash"
    if receipt.bank_account_id:
        return receipt.bank_account.ledger_account
    return "bank"


@transaction.atomic
def create_customer_receipt(company, customer, amount, *, user=None, method="cash", bank_account=None, receipt_date=None,
                            allocations=None, cheque_no="", note=""):
    """allocations: [(invoice, amount)]. Money not allocated stays as an advance on the customer's account."""
    require(user, "pay_document")
    amount = q2(amount)
    if amount <= 0:
        raise ERPError("Receipt amount must be greater than zero.")
    allocations = allocations or []
    total_alloc, seen = ZERO, set()
    locked = []
    for inv, amt in allocations:
        inv = SalesInvoice.objects.select_for_update().get(pk=inv.pk)
        amt = q2(amt)
        if inv.pk in seen:
            raise ERPError("An invoice is listed twice in the allocation.")
        seen.add(inv.pk)
        if inv.customer_id != customer.pk or inv.status != SalesInvoice.POSTED:
            raise ERPError(f"{inv.reference} does not belong to this customer or is not posted.")
        if amt <= 0 or amt > inv.outstanding:
            raise ERPError(f"{inv.reference}: allocation must be between 0.01 and the outstanding {inv.outstanding}.")
        total_alloc += amt
        locked.append((inv, amt))
    if total_alloc > amount:
        raise ERPError("The allocated amounts exceed the receipt amount.")
    receipt = CustomerReceipt.objects.create(
        company=company, reference=next_reference(company, "customer_receipt"), customer=customer,
        receipt_date=receipt_date or timezone.localdate(), amount=amount, method=method, bank_account=bank_account,
        cheque_no=cheque_no, note=note, created_by=_u(user))
    for inv, amt in locked:
        CustomerReceiptAllocation.objects.create(receipt=receipt, invoice=inv, amount=amt)
        SalesInvoice.objects.filter(pk=inv.pk).update(paid_amount=F("paid_amount") + amt)
    create_journal_entry(
        company, voucher_type="RV", date=receipt.receipt_date, narration=f"{receipt.reference} from {customer.name}",
        source=("customer_receipt", receipt.pk), user=user,
        lines=[{"account": _receipt_account(receipt), "debit": amount},
               {"account": "ar", "credit": amount, "party_type": "customer", "party_id": customer.pk}])
    log_action(AuditLog.POSTED, receipt, user=user, document_number=receipt.reference, company=company)
    return receipt


@transaction.atomic
def reverse_customer_receipt(receipt, user=None, reason=""):
    require(user, "reverse_document")
    receipt = CustomerReceipt.objects.select_for_update().get(pk=receipt.pk)
    if receipt.status != CustomerReceipt.POSTED:
        raise ERPError("This receipt is already cancelled.")
    entry = JournalEntry.objects.filter(company=receipt.company, source_type="customer_receipt", source_id=str(receipt.pk)).first()
    if entry:
        reverse_entry(entry, user, reason=f"{receipt.reference} reversed. {reason}")
    for a in receipt.allocations.all():
        SalesInvoice.objects.filter(pk=a.invoice_id).update(paid_amount=F("paid_amount") - a.amount)
    receipt.status = CustomerReceipt.CANCELLED
    receipt.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.REVERSED, receipt, user=user, note=reason[:200], document_number=receipt.reference, company=receipt.company)
    return receipt


# ================================================================== returns / credit notes
@transaction.atomic
def create_sales_return(invoice, items, reason, *, user=None, restock_warehouse=None, return_date=None, refund_method=None, refund_bank=None):
    """items: [{"invoice_line": line, "quantity": 2, "restock": True}]. Creates the credit note and, if refund_method is given,
    pays the credit back out of cash/bank in the same step."""
    if not (reason or "").strip():
        raise ERPError("A reason is required for a sales return.")
    inv = SalesInvoice.objects.select_for_update().select_related("company", "order__warehouse", "tax_rate", "customer").get(pk=invoice.pk)
    if inv.status != SalesInvoice.POSTED:
        raise ERPError("Only a posted invoice can be returned against.")
    if not items:
        raise ERPError("Select the returned items.")
    restock_warehouse = restock_warehouse or (inv.order.warehouse if inv.order_id else None)
    net, prepared, consumed = ZERO, [], {}
    for it in items:
        line = SalesInvoiceLine.objects.select_for_update().select_related("variant").get(pk=it["invoice_line"].pk)
        if line.invoice_id != inv.pk:
            raise ERPError("A line does not belong to this invoice.")
        qty = q3(it["quantity"])
        consumed[line.pk] = consumed.get(line.pk, ZERO) + qty
        if qty <= 0 or consumed[line.pk] > line.quantity - line.returned_quantity:
            raise ERPError(f"{line.variant}: only {line.quantity - line.returned_quantity} can still be returned.")
        final = qty == line.quantity - line.returned_quantity
        value = q2(line.line_total - line.returned_amount) if final else q2(line.line_total * qty / line.quantity)
        restock = bool(it.get("restock", True))
        if restock and restock_warehouse is None:
            raise ERPError("Choose the warehouse the returned goods go back to.")
        prepared.append((line, qty, value, restock))
        net += value
    net = q2(net)
    tax = ZERO
    if inv.tax_rate_id:
        _, tax, _ = compute_tax(net, inv.tax_rate.rate)
    credit = q2(net + tax)
    remaining = q2(inv.total - inv.credited_amount)
    if credit > remaining:
        if credit - remaining > Decimal("0.05"):
            raise ERPError("The credit exceeds what is left of the invoice.")
        tax -= credit - remaining          # tax rounding across several partial returns
        credit = remaining
    ret = ERPSalesReturn.objects.create(
        company=inv.company, reference=next_reference(inv.company, "sales_return"), invoice=inv, return_date=return_date or timezone.localdate(),
        reason=reason, restock_warehouse=restock_warehouse, net_amount=net, tax_amount=tax, credit_amount=credit, created_by=_u(user))
    restock_value = ZERO
    for line, qty, value, restock in prepared:
        ERPSalesReturnLine.objects.create(sales_return=ret, invoice_line=line, quantity=qty, restocked=restock)
        SalesInvoiceLine.objects.filter(pk=line.pk).update(returned_quantity=F("returned_quantity") + qty,
                                                           returned_amount=F("returned_amount") + value)
        if restock:
            post_stock_movement(line.variant, restock_warehouse, qty, reference_type="SALES_RETURN", reference_no=ret.reference,
                                unit_cost=line.unit_cost, user=user, remarks=reason[:200])
            restock_value += q2(qty * line.unit_cost)
    jl = [{"account": "sales_returns", "debit": net},
          {"account": "ar", "credit": credit, "party_type": "customer", "party_id": inv.customer_id}]
    if tax > 0:
        jl.append({"account": "output_tax", "debit": tax})
    create_journal_entry(inv.company, voucher_type="CREDIT_NOTE", date=ret.return_date, narration=f"{ret.reference} credit note for {inv.reference}",
                         source=("sales_return", ret.pk), user=user, lines=jl)
    restock_value = q2(restock_value)
    if restock_value > 0:
        create_journal_entry(inv.company, date=ret.return_date, narration=f"{ret.reference} goods back in stock",
                             source=("sales_return_stock", ret.pk), user=user, store=restock_warehouse.store,
                             lines=[{"account": inventory_account_key(restock_warehouse), "debit": restock_value},
                                    {"account": "cogs", "credit": restock_value}])
    SalesInvoice.objects.filter(pk=inv.pk).update(credited_amount=F("credited_amount") + credit)
    if refund_method:
        refund = credit
        source = "cash" if refund_method == "cash" else (refund_bank.ledger_account if refund_bank is not None else "bank")
        create_journal_entry(inv.company, voucher_type="PV", date=ret.return_date, narration=f"{ret.reference} refund to {inv.customer.name}",
                             source=("sales_return_refund", ret.pk), user=user,
                             lines=[{"account": "ar", "debit": refund, "party_type": "customer", "party_id": inv.customer_id},
                                    {"account": source, "credit": refund}])
        ERPSalesReturn.objects.filter(pk=ret.pk).update(refunded_amount=refund)
        ret.refunded_amount = refund
    log_action(AuditLog.CREATED, ret, user=user, document_number=ret.reference, company=inv.company)
    return ret


# ================================================================== reports
def customer_statement(company, customer, date_from=None, date_to=None):
    qs = JournalLine.objects.filter(entry__company=company, entry__status__in=["posted", "reversed"], account__system_key="ar",
                                    party_type="customer", party_id=customer.pk).select_related("entry").order_by("entry__date", "entry_id", "id")
    opening = ZERO
    if date_from:
        agg = qs.filter(entry__date__lt=date_from).aggregate(d=Sum("debit"), c=Sum("credit"))
        opening = (agg["d"] or ZERO) - (agg["c"] or ZERO)
        qs = qs.filter(entry__date__gte=date_from)
    if date_to:
        qs = qs.filter(entry__date__lte=date_to)
    rows, balance = [], opening
    for l in qs:
        balance += l.debit - l.credit
        rows.append({"date": l.entry.date, "entry": l.entry.entry_number, "narration": l.entry.narration,
                     "debit": l.debit, "credit": l.credit, "balance": q2(balance)})
    return {"opening": q2(opening), "rows": rows, "closing": q2(balance)}


def ar_aging(company, as_of=None):
    invoices = SalesInvoice.objects.filter(company=company, status=SalesInvoice.POSTED).select_related("customer")
    items = [{"party": i.customer.name, "party_id": i.customer_id, "document_number": i.reference, "due_date": i.due_date,
              "invoice_date": i.invoice_date, "outstanding": i.outstanding} for i in invoices if i.outstanding > 0]
    return aging_buckets(items, as_of=as_of)


def sales_summary(company, date_from=None, date_to=None):
    qs = SalesInvoice.objects.filter(company=company, status=SalesInvoice.POSTED)
    if date_from:
        qs = qs.filter(invoice_date__gte=date_from)
    if date_to:
        qs = qs.filter(invoice_date__lte=date_to)
    return list(qs.values("customer__name").annotate(sales=Sum("subtotal"), tax=Sum("tax_amount"), total=Sum("total"),
                                                      credited=Sum("credited_amount"), paid=Sum("paid_amount")).order_by("-sales"))


def pending_orders(company):
    return SalesOrder.objects.filter(company=company, status__in=["confirmed", "partially_delivered"]).select_related("customer")


def gross_margin(company, date_from=None, date_to=None):
    """Revenue less cost of goods on invoiced lines (credit notes for restocked goods are not netted here)."""
    qs = SalesInvoiceLine.objects.filter(invoice__company=company, invoice__status="posted")
    if date_from:
        qs = qs.filter(invoice__invoice_date__gte=date_from)
    if date_to:
        qs = qs.filter(invoice__invoice_date__lte=date_to)
    revenue = ZERO
    cost = ZERO
    for l in qs:
        revenue += l.line_total
        cost += q2(l.quantity * l.unit_cost)
    return {"revenue": q2(revenue), "cost": q2(cost), "margin": q2(revenue - cost),
            "margin_percent": q2((revenue - cost) * 100 / revenue) if revenue else ZERO}


# ================================================================== helpers for hand-entered documents (admin)
@transaction.atomic
def recompute_order(order):
    order = SalesOrder.objects.select_for_update().select_related("tax_rate").get(pk=order.pk)
    if order.status not in (SalesOrder.DRAFT, SalesOrder.REJECTED):
        raise ERPError("Only a draft order can be edited.")
    subtotal, discount = ZERO, ZERO
    for i in order.items.all():
        if i.quantity <= 0 or i.unit_price < 0 or not 0 <= i.discount_percent <= 100:
            raise ERPError("Check quantities (> 0), prices (>= 0) and discounts (0-100%).")
        gross = q2(i.quantity * i.unit_price)
        disc = q2(gross * i.discount_percent / 100)
        subtotal += gross - disc
        discount += disc
    if subtotal <= 0 and not order.items.exists():
        raise ERPError("The order has no items.")
    tax, total = _totals(q2(subtotal), order.tax_rate)
    order.subtotal, order.discount_amount, order.tax_amount, order.total = q2(subtotal), q2(discount), tax, total
    order.save()
    return order


def auto_allocate(company, customer, amount):
    """Oldest-first allocation of `amount` over the customer's open invoices: [(invoice, amount)]."""
    left, out = q2(amount), []
    for inv in SalesInvoice.objects.filter(company=company, customer=customer, status="posted").order_by("due_date", "invoice_date", "id"):
        if left <= 0:
            break
        if inv.outstanding > 0:
            take = min(inv.outstanding, left)
            out.append((inv, take))
            left -= take
    return out
