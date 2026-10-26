"""
Procurement services. Every function is one business step and runs inside a
single transaction; accounting and stock effects go through
accounting.services / stock.services only.

Accounting recap
  GRN (goods accepted):   Dr Inventory (by warehouse type)    Cr GRNI
  Purchase invoice:       Dr GRNI (at GRN cost)  Dr/Cr PPV (price difference)
                          Dr Input tax           Cr Accounts payable (vendor)
  Vendor payment:         Dr Accounts payable    Cr Cash / Bank
  Return to vendor:       Dr Accounts payable    Cr Inventory (+ Cr Input tax)
"""
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from accounting.models import JournalLine
from accounting.reports import aging_buckets
from accounting.services import create_journal_entry, reverse_entry
from approvals.services import submit_for_approval
from audit.models import AuditLog
from audit.services import log_action
from company.utils import company_of_store, company_of_warehouse
from core.errors import ERPError
from core.money import q2, q3, q4
from masters.services import compute_tax, item_profile_of
from numbering.services import next_reference
from quality.services import create_check
from rbac.permissions import require
from stock.services import get_or_create_batch, inventory_account_key, post_stock_movement
from suppliers.models import PurchaseOrder, PurchaseOrderItem
from .models import (GoodsReceipt, GoodsReceiptLine, PurchaseInvoice, PurchaseInvoiceLine, PurchaseOrderExt,
                     PurchaseRequisition, PurchaseRequisitionItem, RFQ, RFQItem, RFQVendor, VendorPayment,
                     VendorPaymentAllocation, VendorQuotation, VendorQuotationItem, VendorReturn, VendorReturnLine)

ZERO = Decimal("0")


def _u(user):
    return user if (user is not None and getattr(user, "is_authenticated", False)) else None


def _positive_items(items, key="quantity"):
    if not items:
        raise ERPError("Add at least one item.")
    for it in items:
        if q3(it[key]) <= 0:
            raise ERPError("Quantities must be greater than zero.")


# ================================================================== requisition
@transaction.atomic
def create_requisition(company, warehouse, items, *, user=None, department="", purpose="", required_date=None):
    """items: [{"variant": v, "quantity": 10, "estimated_cost": 5, "note": ""}]"""
    _positive_items(items)
    pr = PurchaseRequisition.objects.create(
        company=company, reference=next_reference(company, "purchase_requisition"), warehouse=warehouse,
        department=department, purpose=purpose, required_date=required_date, created_by=_u(user))
    for it in items:
        PurchaseRequisitionItem.objects.create(requisition=pr, variant=it["variant"], quantity=q3(it["quantity"]),
                                               estimated_cost=q4(it.get("estimated_cost") or 0), note=it.get("note", ""))
    log_action(AuditLog.CREATED, pr, user=user, document_number=pr.reference, company=company)
    return pr


@transaction.atomic
def submit_requisition(pr, user=None):
    pr = PurchaseRequisition.objects.select_for_update().get(pk=pr.pk)
    if pr.status not in (PurchaseRequisition.DRAFT, PurchaseRequisition.REJECTED):
        raise ERPError("Only a draft requisition can be submitted.")
    amount = sum((it.quantity * it.estimated_cost for it in pr.items.all()), ZERO)
    submit_for_approval(pr, "purchase_requisition", company=pr.company, user=user, amount=q2(amount), number=pr.reference)
    pr.refresh_from_db()
    return pr


# ================================================================== RFQ & quotations
@transaction.atomic
def create_rfq(company, warehouse, items, suppliers, *, user=None, requisition=None, due_date=None, note=""):
    _positive_items(items)
    if not suppliers:
        raise ERPError("Select at least one vendor for the RFQ.")
    rfq = RFQ.objects.create(company=company, reference=next_reference(company, "rfq"), warehouse=warehouse,
                             requisition=requisition, issue_date=timezone.localdate(), due_date=due_date, note=note,
                             created_by=_u(user))
    for it in items:
        RFQItem.objects.create(rfq=rfq, variant=it["variant"], quantity=q3(it["quantity"]))
    for s in suppliers:
        RFQVendor.objects.get_or_create(rfq=rfq, supplier=s)
    log_action(AuditLog.CREATED, rfq, user=user, document_number=rfq.reference, company=company)
    return rfq


@transaction.atomic
def rfq_from_requisition(pr, suppliers, user=None, due_date=None):
    if pr.status != PurchaseRequisition.APPROVED:
        raise ERPError("Only an approved requisition can be turned into an RFQ.")
    items = [{"variant": i.variant, "quantity": i.quantity} for i in pr.items.all()]
    rfq = create_rfq(pr.company, pr.warehouse, items, suppliers, user=user, requisition=pr, due_date=due_date)
    PurchaseRequisition.objects.filter(pk=pr.pk).update(status=PurchaseRequisition.CONVERTED)
    return rfq


@transaction.atomic
def send_rfq(rfq, user=None):
    rfq = RFQ.objects.select_for_update().get(pk=rfq.pk)
    if rfq.status != RFQ.DRAFT:
        raise ERPError("Only a draft RFQ can be sent.")
    rfq.status = RFQ.SENT
    rfq.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.OTHER, rfq, user=user, note="sent to vendors", document_number=rfq.reference, company=rfq.company)
    return rfq


@transaction.atomic
def add_quotation(rfq, supplier, prices, *, user=None, quotation_date=None, valid_until=None, delivery_days=0,
                  payment_term=None, vendor_reference="", note=""):
    """prices: {rfq_item_id: unit_price}. A vendor must quote EVERY item so quotations stay comparable."""
    rfq = RFQ.objects.select_for_update().get(pk=rfq.pk)
    if rfq.status not in (RFQ.DRAFT, RFQ.SENT):
        raise ERPError("This RFQ no longer accepts quotations.")
    if not RFQVendor.objects.filter(rfq=rfq, supplier=supplier).exists():
        raise ERPError(f"{supplier} was not invited to this RFQ.")
    if VendorQuotation.objects.filter(rfq=rfq, supplier=supplier).exists():
        raise ERPError(f"{supplier} already gave a quotation for this RFQ.")
    item_ids = set(rfq.items.values_list("id", flat=True))
    if set(prices) != item_ids:
        raise ERPError("A quotation must give a price for every RFQ item.")
    for price in prices.values():
        if q4(price) < 0:
            raise ERPError("Unit prices cannot be negative.")
    quotation = VendorQuotation.objects.create(
        company=rfq.company, reference=next_reference(rfq.company, "vendor_quotation"), rfq=rfq, supplier=supplier,
        quotation_date=quotation_date or timezone.localdate(), valid_until=valid_until, delivery_days=delivery_days,
        payment_term=payment_term, vendor_reference=vendor_reference, note=note, created_by=_u(user))
    for item_id, price in prices.items():
        VendorQuotationItem.objects.create(quotation=quotation, rfq_item_id=item_id, unit_price=q4(price))
    return quotation


def compare_quotations(rfq):
    """Side-by-side price comparison. Returns
    {"items": [{"item", "quantity", "prices": {supplier_id: price}, "lowest_supplier_id"}],
     "vendors": [{"supplier", "quotation", "total", "delivery_days", "payment_term", "is_lowest_total"}]}"""
    quotations = list(rfq.quotations.select_related("supplier", "payment_term").prefetch_related("items"))
    items = []
    for item in rfq.items.select_related("variant"):
        prices = {}
        for q in quotations:
            for qi in q.items.all():
                if qi.rfq_item_id == item.id:
                    prices[q.supplier_id] = qi.unit_price
        lowest = min(prices, key=prices.get) if prices else None
        items.append({"item": item, "quantity": item.quantity, "prices": prices, "lowest_supplier_id": lowest})
    vendors = []
    for q in quotations:
        total = sum((qi.unit_price * qi.rfq_item.quantity for qi in q.items.all()), ZERO)
        vendors.append({"supplier": q.supplier, "quotation": q, "total": q2(total), "delivery_days": q.delivery_days,
                        "payment_term": q.payment_term})
    if vendors:
        best = min(v["total"] for v in vendors)
        for v in vendors:
            v["is_lowest_total"] = v["total"] == best
    return {"items": items, "vendors": vendors}


@transaction.atomic
def select_quotation(quotation, user=None, *, tax_rate=None, expected_date=None):
    """Awards the RFQ and creates a draft PO from the winning quotation."""
    quotation = VendorQuotation.objects.select_for_update().select_related("rfq").get(pk=quotation.pk)
    rfq = quotation.rfq
    if rfq.status == RFQ.AWARDED:
        raise ERPError("This RFQ is already awarded.")
    items = [{"variant": qi.rfq_item.variant, "quantity": qi.rfq_item.quantity, "cost_price": qi.unit_price}
             for qi in quotation.items.select_related("rfq_item__variant")]
    po = create_purchase_order(rfq.company, quotation.supplier, rfq.warehouse, items, user=user, requisition=rfq.requisition,
                               quotation=quotation, payment_term=quotation.payment_term, tax_rate=tax_rate,
                               expected_date=expected_date)
    quotation.status = VendorQuotation.SELECTED
    quotation.save(update_fields=["status", "updated_at"])
    rfq.quotations.exclude(pk=quotation.pk).update(status=VendorQuotation.REJECTED)
    rfq.status = RFQ.AWARDED
    rfq.save(update_fields=["status", "updated_at"])
    return po


# ================================================================== purchase order
def po_amounts(po):
    """(subtotal, discount, tax, total) for an ERP PO."""
    subtotal = q2(sum((i.quantity * i.cost_price for i in po.items.all()), ZERO))
    ext = po.erp
    discount = min(q2(ext.discount_amount), subtotal)
    net = subtotal - discount
    if ext.tax_rate_id:
        _, tax, total = compute_tax(net, ext.tax_rate.rate, inclusive=ext.tax_rate.is_inclusive)
        if ext.tax_rate.is_inclusive:
            return subtotal, discount, tax, q2(net)
        return subtotal, discount, tax, total
    return subtotal, discount, ZERO, q2(net)


@transaction.atomic
def create_purchase_order(company, supplier, warehouse, items, *, user=None, order_date=None, expected_date=None,
                          payment_term=None, tax_rate=None, discount_amount=0, requisition=None, quotation=None,
                          terms="", note=""):
    """items: [{"variant": v, "quantity": 10, "cost_price": 120}]"""
    _positive_items(items)
    if company_of_store(supplier.store).pk != company.pk or company_of_warehouse(warehouse).pk != company.pk:
        raise ERPError("Vendor and warehouse must belong to the same company as the order.")
    for it in items:
        if q2(it["cost_price"]) < 0:
            raise ERPError("Cost price cannot be negative.")
    po = PurchaseOrder.objects.create(
        reference=next_reference(company, "purchase_order"), supplier=supplier, warehouse=warehouse, status="draft",
        order_date=order_date or timezone.localdate(), note=note, created_by=_u(user))
    for it in items:
        PurchaseOrderItem.objects.create(purchase_order=po, variant=it["variant"], quantity=q3(it["quantity"]), cost_price=q2(it["cost_price"]))
    PurchaseOrderExt.objects.create(
        purchase_order=po, company=company, requisition=requisition, quotation=quotation, expected_date=expected_date,
        payment_term=payment_term, tax_rate=tax_rate, discount_amount=q2(discount_amount), terms=terms)
    if requisition is not None and requisition.status == PurchaseRequisition.APPROVED:
        PurchaseRequisition.objects.filter(pk=requisition.pk).update(status=PurchaseRequisition.CONVERTED)
    log_action(AuditLog.CREATED, po, user=user, document_number=po.reference, company=company)
    return po


@transaction.atomic
def submit_purchase_order(po, user=None):
    ext = PurchaseOrderExt.objects.select_for_update().select_related("purchase_order", "company").get(purchase_order=po)
    if ext.status not in (PurchaseOrderExt.DRAFT, PurchaseOrderExt.REJECTED):
        raise ERPError("Only a draft purchase order can be submitted.")
    if not ext.purchase_order.items.exists():
        raise ERPError("The purchase order has no items.")
    _, _, _, total = po_amounts(ext.purchase_order)
    submit_for_approval(ext, "purchase_order", company=ext.company, user=user, amount=total, number=ext.purchase_order.reference)
    ext.refresh_from_db()
    return ext


@transaction.atomic
def cancel_purchase_order(po, user=None, reason=""):
    ext = PurchaseOrderExt.objects.select_for_update().get(purchase_order=po)
    if GoodsReceipt.objects.filter(purchase_order=po).exclude(status=GoodsReceipt.REVERSED).exists():
        raise ERPError("Goods were already received against this order; it cannot be cancelled.")
    if ext.status == PurchaseOrderExt.CANCELLED:
        return ext
    ext.status = PurchaseOrderExt.CANCELLED
    ext.save(update_fields=["status", "updated_at"])
    PurchaseOrder.objects.filter(pk=po.pk).update(status="cancelled")
    log_action(AuditLog.CANCELLED, po, user=user, note=reason[:200], document_number=po.reference, company=ext.company)
    return ext


def po_receipt_status(po):
    """Per PO line: ordered, received (not reversed), pending."""
    rows = []
    for item in po.items.select_related("variant"):
        received = GoodsReceiptLine.objects.filter(po_item=item).exclude(grn__status=GoodsReceipt.REVERSED).aggregate(
            t=Sum("quantity_received"))["t"] or ZERO
        rows.append({"item": item, "ordered": item.quantity, "received": received, "pending": item.quantity - received})
    return rows


# ================================================================== goods receipt
def _post_line_stock(line, qty, user):
    """Accepted stock enters the warehouse and the GRNI liability is booked."""
    grn = line.grn
    if qty <= 0:
        return
    post_stock_movement(line.variant, grn.warehouse, qty, reference_type="GRN", reference_no=grn.reference,
                        unit_cost=line.unit_cost, batch=line.batch, user=user, remarks=f"Receipt from {grn.supplier.name}")
    value = q2(qty * line.unit_cost)
    if value > 0:
        create_journal_entry(
            grn.company, date=grn.received_date, narration=f"{grn.reference} {line.variant.sku}",
            source=("grn_line", line.pk), store=grn.warehouse.store, user=user,
            lines=[{"account": inventory_account_key(grn.warehouse), "debit": value},
                   {"account": "grni", "credit": value, "party_type": "vendor", "party_id": grn.supplier_id}])


def _refresh_grn_and_po(grn):
    grn = GoodsReceipt.objects.select_for_update().get(pk=grn.pk)
    pending = grn.lines.filter(qc_status=GoodsReceiptLine.PENDING).exists()
    grn.status = GoodsReceipt.QC_PENDING if pending else GoodsReceipt.POSTED
    grn.save(update_fields=["status", "updated_at"])
    po = grn.purchase_order
    if all(r["pending"] <= 0 for r in po_receipt_status(po)) and po.status != "received":
        PurchaseOrder.objects.filter(pk=po.pk).update(status="received")
        PurchaseOrderExt.objects.filter(purchase_order=po, status=PurchaseOrderExt.APPROVED).update(status=PurchaseOrderExt.CLOSED)
    return grn


@transaction.atomic
def receive_goods(po, lines, *, user=None, warehouse=None, received_date=None, challan_no="", note=""):
    """lines: [{"po_item": item, "quantity": 50, "batch_no": "B-7", "expiry_date": date, "unit_cost": optional}]
    Goods needing incoming QC (item setting) are held out of stock until the QC result is entered."""
    po = PurchaseOrder.objects.select_for_update().select_related("supplier", "warehouse").get(pk=po.pk)
    ext = getattr(po, "erp", None)
    if ext is None:
        raise ERPError("This purchase order was not created through the ERP procurement module.")
    if ext.status != PurchaseOrderExt.APPROVED:
        raise ERPError(f"The purchase order must be approved before goods can be received (status: {ext.get_status_display()}).")
    if not lines:
        raise ERPError("Enter at least one received line.")
    warehouse = warehouse or po.warehouse
    company = ext.company
    if company_of_warehouse(warehouse).pk != company.pk:
        raise ERPError("The receiving warehouse belongs to a different company.")

    status_by_item = {r["item"].pk: r["pending"] for r in po_receipt_status(po)}
    seen = set()
    for l in lines:
        item = l["po_item"]
        if item.purchase_order_id != po.pk:
            raise ERPError("A line does not belong to this purchase order.")
        if item.pk in seen:
            raise ERPError("The same order line appears twice; combine the quantities.")
        seen.add(item.pk)
        qty = q3(l["quantity"])
        if qty <= 0:
            raise ERPError("Received quantity must be greater than zero.")
        if qty > status_by_item[item.pk]:
            raise ERPError(f"{item.variant}: received {qty} but only {status_by_item[item.pk]} is still pending on the order.")

    grn = GoodsReceipt.objects.create(
        company=company, reference=next_reference(company, "goods_receipt"), purchase_order=po, supplier=po.supplier,
        warehouse=warehouse, received_date=received_date or timezone.localdate(), challan_no=challan_no, note=note,
        created_by=_u(user))
    for l in lines:
        item, qty = l["po_item"], q3(l["quantity"])
        profile = item_profile_of(item.variant)
        batch = None
        if profile and (profile.batch_required or profile.expiry_required):
            if not l.get("batch_no"):
                raise ERPError(f"{item.variant}: a batch number is required.")
            if profile.expiry_required and not l.get("expiry_date"):
                raise ERPError(f"{item.variant}: an expiry date is required.")
        if l.get("batch_no"):
            batch = get_or_create_batch(item.variant, l["batch_no"], expiry_date=l.get("expiry_date"))
        cost = q4(l.get("unit_cost") if l.get("unit_cost") is not None else item.cost_price)
        needs_qc = bool(profile and profile.qc_required_on_receipt)
        line = GoodsReceiptLine.objects.create(
            grn=grn, po_item=item, variant=item.variant, batch=batch, quantity_received=qty, unit_cost=cost,
            quantity_accepted=ZERO if needs_qc else qty, qc_status=GoodsReceiptLine.PENDING if needs_qc else GoodsReceiptLine.NOT_REQUIRED)
        if needs_qc:
            create_check(company, "incoming", "grn_line", line.pk, item.variant, qty, source_reference=grn.reference, user=user)
        else:
            _post_line_stock(line, qty, user)
    log_action(AuditLog.CREATED, grn, user=user, document_number=grn.reference, company=company)
    return _refresh_grn_and_po(grn)


@transaction.atomic
def _on_grn_line_qc(check):
    """quality.complete_check -> release accepted quantity into stock, record rejected."""
    line = GoodsReceiptLine.objects.select_for_update().select_related("grn__supplier", "grn__warehouse", "variant").get(pk=check.source_id)
    if line.qc_status == GoodsReceiptLine.DONE:
        return
    line.quantity_accepted, line.quantity_rejected = check.accepted_quantity, check.rejected_quantity
    line.qc_status = GoodsReceiptLine.DONE
    line.save(update_fields=["quantity_accepted", "quantity_rejected", "qc_status"])
    _post_line_stock(line, line.quantity_accepted, check.inspector)
    _refresh_grn_and_po(line.grn)


@transaction.atomic
def reverse_goods_receipt(grn, user=None, reason=""):
    """Undo a GRN that has not been invoiced. Stock goes back out and the journals are reversed."""
    require(user, "reverse_document")
    grn = GoodsReceipt.objects.select_for_update().get(pk=grn.pk)
    if grn.status == GoodsReceipt.REVERSED:
        raise ERPError("This receipt was already reversed.")
    if grn.lines.filter(invoiced_quantity__gt=0).exists():
        raise ERPError("Part of this receipt is already invoiced; reverse or cancel the invoice first.")
    from accounting.models import JournalEntry
    for line in grn.lines.select_related("variant"):
        if line.quantity_accepted > 0:
            post_stock_movement(line.variant, grn.warehouse, -line.quantity_accepted, reference_type="GRN_REVERSAL",
                                reference_no=grn.reference, batch=line.batch, user=user, remarks=reason[:200])
            entry = JournalEntry.objects.filter(company=grn.company, source_type="grn_line", source_id=str(line.pk)).first()
            if entry and entry.status == JournalEntry.POSTED:
                reverse_entry(entry, user, reason=f"GRN {grn.reference} reversed")
    from quality.models import QualityCheck
    QualityCheck.objects.filter(source_type="grn_line", source_id__in=[str(i) for i in grn.lines.values_list("pk", flat=True)],
                                status__in=["pending", "hold"]).update(status="failed", remarks="GRN reversed")
    grn.status = GoodsReceipt.REVERSED
    grn.save(update_fields=["status", "updated_at"])
    po = grn.purchase_order
    if po.status == "received":
        PurchaseOrder.objects.filter(pk=po.pk).update(status="ordered")
        PurchaseOrderExt.objects.filter(purchase_order=po, status=PurchaseOrderExt.CLOSED).update(status=PurchaseOrderExt.APPROVED)
    log_action(AuditLog.REVERSED, grn, user=user, note=reason[:200], document_number=grn.reference, company=grn.company)
    return grn


# ================================================================== purchase invoice
def _norm_invoice_no(value):
    return " ".join((value or "").strip().upper().split())


@transaction.atomic
def _on_open_invoices(grn_line, exclude=None):
    """Quantity of this receipt line already typed onto draft / pending invoices (they are only counted as invoiced once posted,
    but a second invoice for the same goods must not be allowed to start)."""
    qs = PurchaseInvoiceLine.objects.filter(grn_line=grn_line, invoice__status__in=(PurchaseInvoice.DRAFT, PurchaseInvoice.PENDING))
    if exclude is not None:
        qs = qs.exclude(invoice=exclude)
    return qs.aggregate(t=Sum("quantity"))["t"] or ZERO


def create_purchase_invoice(company, supplier, vendor_invoice_no, invoice_date, lines, *, user=None, due_date=None,
                            tax_rate=None, purchase_order=None, note=""):
    """lines: [{"grn_line": gl, "quantity": 10, "unit_price": 12.5}]       (stock, matched to a receipt)
              [{"description": "Freight", "account": acct, "quantity": 1, "unit_price": 500}]   (non-stock)"""
    vendor_invoice_no = _norm_invoice_no(vendor_invoice_no)
    if not vendor_invoice_no:
        raise ERPError("The vendor's invoice number is required.")
    if PurchaseInvoice.objects.filter(company=company, supplier=supplier, vendor_invoice_no=vendor_invoice_no).exclude(status="cancelled").exists():
        raise ERPError(f"Invoice {vendor_invoice_no} from {supplier} has already been entered (duplicate invoice).")
    if not lines:
        raise ERPError("Add at least one invoice line.")
    if company_of_store(supplier.store).pk != company.pk:
        raise ERPError("The vendor belongs to a different company.")
    if purchase_order is not None and (purchase_order.supplier_id != supplier.pk or company_of_warehouse(purchase_order.warehouse).pk != company.pk):
        raise ERPError(f"Purchase order {purchase_order.reference} was placed with another vendor, so this invoice cannot be tied to it.")

    clean, subtotal = [], ZERO
    consumed = {}
    for l in lines:
        qty, price = q3(l["quantity"]), q4(l["unit_price"])
        if qty <= 0 or price < 0:
            raise ERPError("Invoice quantity must be positive and price cannot be negative.")
        gl = l.get("grn_line")
        if gl is not None:
            gl = GoodsReceiptLine.objects.select_for_update().select_related("grn", "variant").get(pk=gl.pk)
            if gl.grn.supplier_id != supplier.pk or gl.grn.company_id != company.pk:
                raise ERPError("A receipt line belongs to a different vendor.")
            if gl.grn.status == GoodsReceipt.REVERSED:
                raise ERPError(f"Receipt {gl.grn.reference} was reversed and cannot be invoiced.")
            consumed[gl.pk] = consumed.get(gl.pk, ZERO) + qty
            room = gl.uninvoiced_quantity - _on_open_invoices(gl)
            if consumed[gl.pk] > room:
                raise ERPError(f"{gl.variant}: invoiced {consumed[gl.pk]} but only {max(room, ZERO)} accepted and not yet invoiced.")
            clean.append({"grn_line": gl, "variant": gl.variant, "description": str(gl.variant)[:255], "quantity": qty, "unit_price": price})
        else:
            if l.get("account") is None:
                raise ERPError("A non-stock invoice line needs an expense account.")
            clean.append({"grn_line": None, "variant": None, "account": l["account"], "description": (l.get("description") or "")[:255],
                          "quantity": qty, "unit_price": price})
        subtotal += q2(qty * price)
    subtotal = q2(subtotal)
    tax = ZERO
    if tax_rate is not None:
        if tax_rate.is_inclusive:
            raise ERPError("Purchase invoices need a tax-exclusive tax rate (enter prices without tax).")
        _, tax, _ = compute_tax(subtotal, tax_rate.rate)
    total = q2(subtotal + tax)
    if due_date is None:
        profile = getattr(supplier, "erp_profile", None)
        days = profile.payment_term.days if (profile and profile.payment_term_id) else 0
        due_date = invoice_date + timedelta(days=days)
    inv = PurchaseInvoice.objects.create(
        company=company, reference=next_reference(company, "purchase_invoice", on_date=invoice_date), supplier=supplier,
        vendor_invoice_no=vendor_invoice_no, invoice_date=invoice_date, due_date=due_date, purchase_order=purchase_order,
        tax_rate=tax_rate, subtotal=subtotal, tax_amount=tax, total=total, note=note, created_by=_u(user))
    for c in clean:
        PurchaseInvoiceLine.objects.create(
            invoice=inv, grn_line=c["grn_line"], variant=c["variant"], description=c["description"], account=c.get("account"),
            quantity=c["quantity"], unit_price=c["unit_price"], line_total=q2(c["quantity"] * c["unit_price"]))
    log_action(AuditLog.CREATED, inv, user=user, document_number=inv.reference, company=company)
    return inv


def three_way_match(invoice):
    """PO vs receipt vs invoice, per stock line, so the buyer can see variances before approving."""
    rows = []
    for l in invoice.lines.select_related("grn_line__po_item", "grn_line__grn", "variant"):
        if l.grn_line is None:
            rows.append({"line": l, "po_price": None, "grn_qty": None, "invoice_qty": l.quantity, "invoice_price": l.unit_price,
                         "price_variance": ZERO, "flag": "non-stock"})
            continue
        po_price = l.grn_line.po_item.cost_price
        variance = q2((l.unit_price - l.grn_line.unit_cost) * l.quantity)
        rows.append({"line": l, "po_price": po_price, "grn_qty": l.grn_line.quantity_accepted, "invoice_qty": l.quantity,
                     "invoice_price": l.unit_price, "price_variance": variance, "flag": "price difference" if variance != 0 else "ok"})
    return rows


@transaction.atomic
def submit_purchase_invoice(invoice, user=None):
    inv = PurchaseInvoice.objects.select_for_update().get(pk=invoice.pk)
    if inv.status not in (PurchaseInvoice.DRAFT, PurchaseInvoice.REJECTED):
        raise ERPError("Only a draft invoice can be submitted.")
    submit_for_approval(inv, "purchase_invoice", company=inv.company, user=user, amount=inv.total, number=inv.reference)
    inv.refresh_from_db()
    return inv


@transaction.atomic
def post_purchase_invoice(invoice, user=None):
    inv = PurchaseInvoice.objects.select_for_update().get(pk=invoice.pk)
    if inv.status == PurchaseInvoice.POSTED:
        return inv
    if inv.status not in (PurchaseInvoice.PENDING, PurchaseInvoice.DRAFT):
        raise ERPError("This invoice cannot be posted in its current status.")
    jl, grni_total, ppv_total, expense = [], ZERO, ZERO, {}
    for l in inv.lines.select_related("grn_line", "account"):
        if l.grn_line is not None:
            gl = GoodsReceiptLine.objects.select_for_update().get(pk=l.grn_line_id)
            if l.quantity > gl.uninvoiced_quantity:
                raise ERPError("A receipt line was already invoiced in the meantime.")
            gl.invoiced_quantity += l.quantity
            gl.save(update_fields=["invoiced_quantity"])
            at_cost = q2(l.quantity * gl.unit_cost)
            grni_total += at_cost
            ppv_total += q2(l.line_total - at_cost)
        else:
            expense[l.account_id] = expense.get(l.account_id, ZERO) + l.line_total
    if grni_total > 0:
        jl.append({"account": "grni", "debit": grni_total, "party_type": "vendor", "party_id": inv.supplier_id})
    if ppv_total > 0:
        jl.append({"account": "ppv", "debit": ppv_total})
    elif ppv_total < 0:
        jl.append({"account": "ppv", "credit": -ppv_total})
    from accounting.models import Account
    for account_id, amount in expense.items():
        jl.append({"account": Account.objects.get(pk=account_id), "debit": q2(amount)})
    if inv.tax_amount > 0:
        jl.append({"account": "input_tax", "debit": inv.tax_amount})
    jl.append({"account": "ap", "credit": inv.total, "party_type": "vendor", "party_id": inv.supplier_id,
               "description": f"{inv.supplier.name} inv {inv.vendor_invoice_no}"})
    create_journal_entry(inv.company, date=inv.invoice_date, narration=f"{inv.reference} / {inv.vendor_invoice_no}",
                         source=("purchase_invoice", inv.pk), user=user, lines=jl)
    inv.status = PurchaseInvoice.POSTED
    inv.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.POSTED, inv, user=user, document_number=inv.reference, company=inv.company)
    return inv


@transaction.atomic
def cancel_purchase_invoice(invoice, user=None, reason=""):
    """Draft/pending invoices are simply cancelled. A posted invoice is reversed (only if unpaid)."""
    inv = PurchaseInvoice.objects.select_for_update().get(pk=invoice.pk)
    if inv.status == PurchaseInvoice.CANCELLED:
        return inv
    if inv.paid_amount > 0:
        raise ERPError("This invoice has payments against it; reverse the payment first.")
    if inv.status == PurchaseInvoice.POSTED:
        require(user, "reverse_document")
        from accounting.models import JournalEntry
        entry = JournalEntry.objects.filter(company=inv.company, source_type="purchase_invoice", source_id=str(inv.pk)).first()
        if entry:
            reverse_entry(entry, user, reason=f"Invoice {inv.reference} cancelled. {reason}")
        for l in inv.lines.select_related("grn_line"):
            if l.grn_line_id:
                GoodsReceiptLine.objects.filter(pk=l.grn_line_id).update(invoiced_quantity=F("invoiced_quantity") - l.quantity)
    inv.status = PurchaseInvoice.CANCELLED
    inv.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.CANCELLED, inv, user=user, note=reason[:200], document_number=inv.reference, company=inv.company)
    return inv


# ================================================================== vendor payment
def _payment_account(payment):
    if payment.method == "cash":
        return "cash"
    if payment.bank_account_id:
        return payment.bank_account.ledger_account
    return "bank"


@transaction.atomic
def create_vendor_payment(company, supplier, amount, *, user=None, method="bank", bank_account=None, payment_date=None,
                          allocations=None, cheque_no="", note=""):
    """allocations: [(invoice, amount)]. Any part of `amount` not allocated stays as an advance on the vendor account."""
    require(user, "pay_document")
    amount = q2(amount)
    if amount <= 0:
        raise ERPError("Payment amount must be greater than zero.")
    allocations = allocations or []
    total_alloc = ZERO
    seen = set()
    for inv, amt in allocations:
        inv = PurchaseInvoice.objects.select_for_update().get(pk=inv.pk)
        amt = q2(amt)
        if inv.pk in seen:
            raise ERPError("An invoice is listed twice in the allocation.")
        seen.add(inv.pk)
        if inv.supplier_id != supplier.pk:
            raise ERPError(f"{inv.reference} belongs to a different vendor.")
        if inv.status != PurchaseInvoice.POSTED:
            raise ERPError(f"{inv.reference} is not posted yet.")
        if amt <= 0 or amt > inv.outstanding:
            raise ERPError(f"{inv.reference}: allocation must be between 0.01 and the outstanding {inv.outstanding}.")
        total_alloc += amt
    if total_alloc > amount:
        raise ERPError("The allocated amounts exceed the payment amount.")
    payment = VendorPayment.objects.create(
        company=company, reference=next_reference(company, "vendor_payment"), supplier=supplier,
        payment_date=payment_date or timezone.localdate(), amount=amount, method=method, bank_account=bank_account,
        cheque_no=cheque_no, note=note, created_by=_u(user))
    for inv, amt in allocations:
        VendorPaymentAllocation.objects.create(payment=payment, invoice_id=inv.pk, amount=q2(amt))
    log_action(AuditLog.CREATED, payment, user=user, document_number=payment.reference, company=company)
    return payment


@transaction.atomic
def submit_vendor_payment(payment, user=None):
    pay = VendorPayment.objects.select_for_update().get(pk=payment.pk)
    if pay.status not in (VendorPayment.DRAFT, VendorPayment.REJECTED):
        raise ERPError("Only a draft payment can be submitted.")
    submit_for_approval(pay, "vendor_payment", company=pay.company, user=user, amount=pay.amount, number=pay.reference)
    pay.refresh_from_db()
    return pay


@transaction.atomic
def post_vendor_payment(payment, user=None):
    pay = VendorPayment.objects.select_for_update().select_related("bank_account__ledger_account", "supplier").get(pk=payment.pk)
    if pay.status == VendorPayment.POSTED:
        return pay
    if pay.status not in (VendorPayment.PENDING, VendorPayment.DRAFT):
        raise ERPError("This payment cannot be posted in its current status.")
    for alloc in pay.allocations.select_related("invoice"):
        inv = PurchaseInvoice.objects.select_for_update().get(pk=alloc.invoice_id)
        if alloc.amount > inv.outstanding:
            raise ERPError(f"{inv.reference}: outstanding changed to {inv.outstanding}; allocation {alloc.amount} is too high.")
        inv.paid_amount += alloc.amount
        inv.save(update_fields=["paid_amount", "updated_at"])
    create_journal_entry(
        pay.company, voucher_type="PV", date=pay.payment_date, narration=f"{pay.reference} to {pay.supplier.name}",
        source=("vendor_payment", pay.pk), user=user,
        lines=[{"account": "ap", "debit": pay.amount, "party_type": "vendor", "party_id": pay.supplier_id},
               {"account": _payment_account(pay), "credit": pay.amount}])
    pay.status = VendorPayment.POSTED
    pay.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.POSTED, pay, user=user, document_number=pay.reference, company=pay.company)
    return pay


@transaction.atomic
def reverse_vendor_payment(payment, user=None, reason=""):
    require(user, "reverse_document")
    pay = VendorPayment.objects.select_for_update().get(pk=payment.pk)
    if pay.status != VendorPayment.POSTED:
        raise ERPError("Only a posted payment can be reversed.")
    from accounting.models import JournalEntry
    entry = JournalEntry.objects.filter(company=pay.company, source_type="vendor_payment", source_id=str(pay.pk)).first()
    if entry:
        reverse_entry(entry, user, reason=f"Payment {pay.reference} reversed. {reason}")
    for alloc in pay.allocations.all():
        inv = PurchaseInvoice.objects.select_for_update().get(pk=alloc.invoice_id)
        inv.paid_amount = max(inv.paid_amount - alloc.amount, ZERO)
        inv.save(update_fields=["paid_amount", "updated_at"])
    pay.status = VendorPayment.CANCELLED
    pay.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.REVERSED, pay, user=user, note=reason[:200], document_number=pay.reference, company=pay.company)
    return pay


# ================================================================== return to vendor
@transaction.atomic
def return_to_vendor(company, supplier, warehouse, items, *, user=None, grn=None, tax_rate=None, reason="", return_date=None):
    """items: [{"variant": v, "quantity": 5, "unit_cost": 100}] - the credit price agreed with the vendor.
    Stock leaves at the current average cost; any difference to the credit price goes to PPV."""
    _positive_items(items)
    vr = VendorReturn.objects.create(company=company, reference=next_reference(company, "purchase_return"), supplier=supplier,
                                     warehouse=warehouse, grn=grn, return_date=return_date or timezone.localdate(),
                                     tax_rate=tax_rate, reason=reason, created_by=_u(user))
    credit_value, stock_value = ZERO, ZERO
    for it in items:
        qty, price = q3(it["quantity"]), q4(it["unit_cost"])
        entry = post_stock_movement(it["variant"], warehouse, -qty, reference_type="VENDOR_RETURN", reference_no=vr.reference,
                                    user=user, remarks=reason[:200])
        VendorReturnLine.objects.create(vendor_return=vr, variant=it["variant"], quantity=qty, unit_cost=price)
        credit_value += q2(qty * price)
        stock_value += -entry.value_change
    credit_value, stock_value = q2(credit_value), q2(stock_value)
    tax = ZERO
    if tax_rate is not None:
        _, tax, _ = compute_tax(credit_value, tax_rate.rate)
    total = q2(credit_value + tax)
    lines = [{"account": "ap", "debit": total, "party_type": "vendor", "party_id": supplier.pk},
             {"account": inventory_account_key(warehouse), "credit": stock_value}]
    if tax > 0:
        lines.append({"account": "input_tax", "credit": tax})
    diff = credit_value - stock_value
    if diff > 0:
        lines.append({"account": "ppv", "credit": diff})
    elif diff < 0:
        lines.append({"account": "ppv", "debit": -diff})
    create_journal_entry(company, voucher_type="DEBIT_NOTE", date=vr.return_date, narration=f"{vr.reference} return to {supplier.name}",
                         source=("vendor_return", vr.pk), user=user, store=warehouse.store, lines=lines)
    vr.total = total
    vr.save(update_fields=["total", "updated_at"])
    log_action(AuditLog.CREATED, vr, user=user, document_number=vr.reference, company=company)
    return vr


# ================================================================== reports
def vendor_statement(company, supplier, date_from=None, date_to=None):
    """Ledger of what we owe one vendor, from posted AP journal lines."""
    qs = JournalLine.objects.filter(entry__company=company, entry__status__in=["posted", "reversed"], account__system_key="ap",
                                    party_type="vendor", party_id=supplier.pk).select_related("entry").order_by("entry__date", "entry_id", "id")
    opening = ZERO
    if date_from:
        agg = qs.filter(entry__date__lt=date_from).aggregate(d=Sum("debit"), c=Sum("credit"))
        opening = (agg["c"] or ZERO) - (agg["d"] or ZERO)
        qs = qs.filter(entry__date__gte=date_from)
    if date_to:
        qs = qs.filter(entry__date__lte=date_to)
    rows, balance = [], opening
    for l in qs:
        balance += l.credit - l.debit
        rows.append({"date": l.entry.date, "entry": l.entry.entry_number, "narration": l.entry.narration,
                     "debit": l.debit, "credit": l.credit, "balance": q2(balance)})
    return {"opening": q2(opening), "rows": rows, "closing": q2(balance)}


def ap_aging(company, as_of=None):
    invoices = PurchaseInvoice.objects.filter(company=company, status=PurchaseInvoice.POSTED).select_related("supplier")
    items = [{"party": i.supplier.name, "party_id": i.supplier_id, "document_number": i.reference, "due_date": i.due_date,
              "invoice_date": i.invoice_date, "outstanding": i.outstanding} for i in invoices if i.outstanding > 0]
    return aging_buckets(items, as_of=as_of)


def purchase_summary(company, date_from=None, date_to=None):
    qs = PurchaseInvoice.objects.filter(company=company, status=PurchaseInvoice.POSTED)
    if date_from:
        qs = qs.filter(invoice_date__gte=date_from)
    if date_to:
        qs = qs.filter(invoice_date__lte=date_to)
    return list(qs.values("supplier__name").annotate(invoices=Sum("total"), tax=Sum("tax_amount"), paid=Sum("paid_amount")).order_by("-invoices"))


# ================================================================== admin helpers (draft documents typed in by hand)
@transaction.atomic
def recompute_invoice(invoice):
    """Validate a hand-entered draft invoice and fill in its totals."""
    inv = PurchaseInvoice.objects.select_for_update().get(pk=invoice.pk)
    if inv.status != PurchaseInvoice.DRAFT:
        raise ERPError("Only a draft invoice can be edited.")
    inv.vendor_invoice_no = _norm_invoice_no(inv.vendor_invoice_no)
    if PurchaseInvoice.objects.filter(company=inv.company, supplier=inv.supplier, vendor_invoice_no=inv.vendor_invoice_no).exclude(
            status="cancelled").exclude(pk=inv.pk).exists():
        raise ERPError(f"Invoice {inv.vendor_invoice_no} from {inv.supplier} has already been entered (duplicate invoice).")
    subtotal, consumed = ZERO, {}
    for l in inv.lines.select_related("grn_line__grn", "variant"):
        if l.quantity <= 0 or l.unit_price < 0:
            raise ERPError("Invoice quantity must be positive and price cannot be negative.")
        if l.grn_line_id:
            gl = l.grn_line
            if gl.grn.supplier_id != inv.supplier_id:
                raise ERPError("A receipt line belongs to a different vendor.")
            consumed[gl.pk] = consumed.get(gl.pk, ZERO) + l.quantity
            room = gl.uninvoiced_quantity - _on_open_invoices(gl, exclude=inv)
            if consumed[gl.pk] > room:
                raise ERPError(f"{gl.variant}: only {max(room, ZERO)} accepted and not yet invoiced.")
            l.variant = gl.variant
        elif l.account_id is None:
            raise ERPError("A non-stock line needs an expense account.")
        l.line_total = q2(l.quantity * l.unit_price)
        l.save(update_fields=["variant", "line_total"])
        subtotal += l.line_total
    if subtotal <= 0:
        raise ERPError("The invoice has no amount.")
    tax = ZERO
    if inv.tax_rate_id:
        if inv.tax_rate.is_inclusive:
            raise ERPError("Purchase invoices need a tax-exclusive tax rate (enter prices without tax).")
        _, tax, _ = compute_tax(subtotal, inv.tax_rate.rate)
    inv.subtotal, inv.tax_amount, inv.total = q2(subtotal), tax, q2(subtotal + tax)
    inv.save()
    return inv


@transaction.atomic
def recompute_payment(payment):
    pay = VendorPayment.objects.select_for_update().get(pk=payment.pk)
    if pay.status != VendorPayment.DRAFT:
        raise ERPError("Only a draft payment can be edited.")
    total, seen = ZERO, set()
    for a in pay.allocations.select_related("invoice"):
        if a.invoice.supplier_id != pay.supplier_id:
            raise ERPError(f"{a.invoice.reference} belongs to a different vendor.")
        if a.invoice.status != PurchaseInvoice.POSTED:
            raise ERPError(f"{a.invoice.reference} is not posted yet.")
        if a.invoice_id in seen:
            raise ERPError("An invoice is listed twice.")
        seen.add(a.invoice_id)
        if a.amount <= 0 or a.amount > a.invoice.outstanding:
            raise ERPError(f"{a.invoice.reference}: allocation must be between 0.01 and {a.invoice.outstanding}.")
        total += a.amount
    if total > pay.amount:
        raise ERPError("The allocated amounts exceed the payment amount.")
    return pay
