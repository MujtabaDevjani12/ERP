"""
Production services.

Accounting recap (WIP is held per work order):
  material issue        Dr WIP                      Cr Raw-material inventory
  return to store       Dr Raw-material inventory   Cr WIP
  job card complete     Dr WIP (labour + overhead)  Cr Production overhead absorbed
  finished-goods receipt Dr Finished-goods inventory Cr WIP        (unit cost = work-order cost / planned quantity)
  close work order      Dr Production variance      Cr WIP        (cost of units never produced / rejected)
"""
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from accounting.services import create_journal_entry
from approvals.services import submit_for_approval
from audit.models import AuditLog
from audit.services import log_action
from company.utils import company_of_warehouse
from core.errors import ERPError
from core.money import q2, q3, q4
from masters.services import item_profile_of
from numbering.services import next_reference
from quality.services import create_check
from stock.services import available_qty, avg_cost, inventory_account_key, post_stock_movement
from .models import (BOM, BOMItem, BOMOperation, JobCard, MaterialIssue, MaterialIssueLine, ProductionReceipt, WorkOrder,
                     WorkOrderMaterial)

ZERO = Decimal("0")


def _u(user):
    return user if (user is not None and getattr(user, "is_authenticated", False)) else None


# ================================================================== BOM
def _creates_cycle(root_variant, component_variant, seen=None):
    """True if component_variant (directly or through its own active BOM) contains root_variant."""
    seen = seen or set()
    if component_variant.pk == root_variant.pk:
        return True
    if component_variant.pk in seen:
        return False
    seen.add(component_variant.pk)
    sub = BOM.objects.filter(variant=component_variant, status=BOM.ACTIVE).first()
    if sub is None:
        return False
    return any(_creates_cycle(root_variant, i.component, seen) for i in sub.items.select_related("component"))


@transaction.atomic
def create_bom(company, variant, components, *, operations=None, output_quantity=1, user=None, note=""):
    """components: [{"component": v, "quantity": 2.5, "scrap_percent": 3}]
    operations: [{"operation": op, "sequence": 10, "machine": m, "minutes_per_unit": 12, "labor_rate_per_hour": 300, "overhead_rate_per_hour": 100}]"""
    if not components:
        raise ERPError("A BOM needs at least one component.")
    last = BOM.objects.filter(variant=variant).order_by("-version").first()
    bom = BOM.objects.create(company=company, variant=variant, version=(last.version + 1) if last else 1, status=BOM.DRAFT,
                             output_quantity=q3(output_quantity), note=note, created_by=_u(user))
    for c in components:
        if q4(c["quantity"]) <= 0:
            raise ERPError("Component quantities must be greater than zero.")
        if _creates_cycle(variant, c["component"]):
            raise ERPError(f"{c['component']} cannot be a component of {variant} (it would contain itself).")
        BOMItem.objects.create(bom=bom, component=c["component"], quantity=q4(c["quantity"]), scrap_percent=q2(c.get("scrap_percent") or 0))
    for i, op in enumerate(operations or [], start=1):
        BOMOperation.objects.create(
            bom=bom, operation=op["operation"], sequence=op.get("sequence") or i * 10, machine=op.get("machine"),
            minutes_per_unit=q2(op.get("minutes_per_unit") or 0), labor_rate_per_hour=q2(op.get("labor_rate_per_hour") or 0),
            overhead_rate_per_hour=q2(op.get("overhead_rate_per_hour") or 0))
    log_action(AuditLog.CREATED, bom, user=user, document_number=f"{variant.sku} v{bom.version}", company=company)
    return bom


@transaction.atomic
def activate_bom(bom, user=None):
    bom = BOM.objects.select_for_update().get(pk=bom.pk)
    if bom.status == BOM.ACTIVE:
        return bom
    if bom.status != BOM.DRAFT:
        raise ERPError("Only a draft BOM can be activated.")
    if not bom.items.exists():
        raise ERPError("The BOM has no components.")
    for item in bom.items.select_related("component"):
        if _creates_cycle(bom.variant, item.component):
            raise ERPError(f"{item.component} would make this BOM circular.")
    BOM.objects.filter(variant=bom.variant, status=BOM.ACTIVE).update(status=BOM.ARCHIVED)
    bom.status = BOM.ACTIVE
    bom.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.OTHER, bom, user=user, note="activated", document_number=f"{bom.variant.sku} v{bom.version}", company=bom.company)
    return bom


@transaction.atomic
def new_bom_version(bom, user=None):
    """Copy a BOM into a new draft version (the active one keeps running until the new one is activated)."""
    comps = [{"component": i.component, "quantity": i.quantity, "scrap_percent": i.scrap_percent} for i in bom.items.all()]
    ops = [{"operation": o.operation, "sequence": o.sequence, "machine": o.machine, "minutes_per_unit": o.minutes_per_unit,
            "labor_rate_per_hour": o.labor_rate_per_hour, "overhead_rate_per_hour": o.overhead_rate_per_hour} for o in bom.operations.all()]
    return create_bom(bom.company, bom.variant, comps, operations=ops, output_quantity=bom.output_quantity, user=user,
                      note=f"Copy of v{bom.version}")


def bom_cost(bom, warehouse=None, quantity=1):
    """Standard cost estimate for `quantity` finished units: materials at current average (or cost price / standard cost)
    plus labour and overhead from the operations."""
    factor = q4(quantity) / bom.output_quantity
    material = ZERO
    lines = []
    for item in bom.items.select_related("component"):
        need = item.quantity * factor * (1 + item.scrap_percent / 100)
        if warehouse is not None:
            unit = avg_cost(item.component, warehouse)
        else:
            profile = item_profile_of(item.component)
            unit = (profile.standard_cost if profile and profile.standard_cost > 0 else item.component.cost_price)
        lines.append({"component": item.component, "quantity": q4(need), "unit_cost": q4(unit), "cost": q2(need * unit)})
        material += need * unit
    labor = overhead = ZERO
    for op in bom.operations.all():
        hours = op.minutes_per_unit * q4(quantity) / 60
        labor += hours * op.labor_rate_per_hour
        overhead += hours * op.overhead_rate_per_hour
    material, labor, overhead = q2(material), q2(labor), q2(overhead)
    total = q2(material + labor + overhead)
    return {"material": material, "labor": labor, "overhead": overhead, "total": total,
            "per_unit": q4(total / q4(quantity)) if q4(quantity) else ZERO, "lines": lines}


# ================================================================== work order
@transaction.atomic
def create_work_order(bom, planned_quantity, source_warehouse, fg_warehouse, *, user=None, planned_start=None, due_date=None,
                      priority=3, note=""):
    planned_quantity = q3(planned_quantity)
    if planned_quantity <= 0:
        raise ERPError("Planned quantity must be greater than zero.")
    bom = BOM.objects.get(pk=bom.pk)                                  # the caller's copy may be stale (status changed meanwhile)
    if bom.status != BOM.ACTIVE:
        raise ERPError("A work order needs an ACTIVE BOM.")
    company = bom.company
    if company_of_warehouse(source_warehouse).pk != company.pk or company_of_warehouse(fg_warehouse).pk != company.pk:
        raise ERPError("Warehouses must belong to the BOM's company.")
    wo = WorkOrder.objects.create(
        company=company, reference=next_reference(company, "work_order"), bom=bom, variant=bom.variant,
        planned_quantity=planned_quantity, source_warehouse=source_warehouse, fg_warehouse=fg_warehouse,
        planned_start=planned_start, due_date=due_date, priority=priority, note=note, created_by=_u(user))
    factor = planned_quantity / bom.output_quantity
    for item in bom.items.all():
        WorkOrderMaterial.objects.create(work_order=wo, component=item.component,
                                         required_quantity=q3(item.quantity * factor * (1 + item.scrap_percent / 100)))
    log_action(AuditLog.CREATED, wo, user=user, document_number=wo.reference, company=company)
    return wo


def material_shortage(wo):
    rows = []
    for m in wo.materials.select_related("component"):
        pending = m.pending_quantity
        have = available_qty(m.component, wo.source_warehouse)
        rows.append({"material": m, "required": m.required_quantity, "issued": m.issued_quantity, "pending": pending,
                     "available": have, "shortage": max(pending - max(have, ZERO), ZERO)})
    return rows


@transaction.atomic
def submit_work_order(wo, user=None):
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    if wo.status not in (WorkOrder.DRAFT, WorkOrder.REJECTED):
        raise ERPError("Only a draft work order can be released.")
    estimate = bom_cost(wo.bom, wo.source_warehouse, wo.planned_quantity)["total"]
    submit_for_approval(wo, "work_order", company=wo.company, user=user, amount=estimate, number=wo.reference)
    wo.refresh_from_db()
    return wo


@transaction.atomic
def create_job_cards(wo):
    """One job card per BOM operation (idempotent). Called when the work order is released."""
    if wo.job_cards.exists():
        return list(wo.job_cards.all())
    cards = []
    for op in wo.bom.operations.select_related("operation", "machine"):
        cards.append(JobCard.objects.create(
            work_order=wo, reference=next_reference(wo.company, "job_card"), operation=op.operation, sequence=op.sequence,
            machine=op.machine, planned_quantity=wo.planned_quantity, standard_minutes=q2(op.minutes_per_unit * wo.planned_quantity),
            labor_rate_per_hour=op.labor_rate_per_hour, overhead_rate_per_hour=op.overhead_rate_per_hour))
    return cards


def _touch_started(wo):
    if wo.status == WorkOrder.RELEASED:
        wo.status, wo.started_at = WorkOrder.IN_PROGRESS, timezone.now()
        wo.save(update_fields=["status", "started_at", "updated_at"])


@transaction.atomic
def issue_materials(wo, quantities=None, *, user=None):
    """quantities: {material_id: qty}; None issues everything still pending. Over-issue is blocked
    (PRODUCTION_ISSUE_TOLERANCE_PERCENT in settings allows a small excess)."""
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    if wo.status not in (WorkOrder.RELEASED, WorkOrder.IN_PROGRESS):
        raise ERPError("Materials can only be issued to a released / in-progress work order.")
    tolerance = Decimal(str(getattr(settings, "PRODUCTION_ISSUE_TOLERANCE_PERCENT", 0)))
    mats = {m.pk: m for m in wo.materials.select_for_update().select_related("component")}
    if quantities is None:
        quantities = {pk: m.pending_quantity for pk, m in mats.items() if m.pending_quantity > 0}
    if not quantities:
        raise ERPError("Nothing to issue.")
    issue = MaterialIssue.objects.create(company=wo.company, reference=next_reference(wo.company, "material_issue"), work_order=wo,
                                         kind=MaterialIssue.ISSUE, created_by=_u(user))
    total = ZERO
    for pk, qty in quantities.items():
        m, qty = mats.get(pk), q3(qty)
        if m is None:
            raise ERPError("A material does not belong to this work order.")
        if qty <= 0:
            raise ERPError("Issue quantities must be greater than zero.")
        limit = m.required_quantity * (1 + tolerance / 100)
        if m.issued_quantity + qty > limit:
            raise ERPError(f"{m.component}: issuing {qty} would exceed the required {m.required_quantity} (already issued {m.issued_quantity}).")
        entry = post_stock_movement(m.component, wo.source_warehouse, -qty, reference_type="WO_ISSUE", reference_no=issue.reference,
                                    user=user, remarks=wo.reference)
        value = -entry.value_change
        m.issued_quantity += qty
        m.issued_value += value
        m.save(update_fields=["issued_quantity", "issued_value"])
        MaterialIssueLine.objects.create(issue=issue, material=m, quantity=qty, unit_cost=entry.unit_cost)
        total += value
    total = q2(total)
    issue.total_value = total
    issue.save(update_fields=["total_value", "updated_at"])
    if total > 0:
        create_journal_entry(wo.company, date=timezone.localdate(), narration=f"{issue.reference} issue to {wo.reference}",
                             source=("material_issue", issue.pk), user=user, store=wo.source_warehouse.store,
                             lines=[{"account": "wip_inventory", "debit": total},
                                    {"account": inventory_account_key(wo.source_warehouse), "credit": total}])
    wo.material_cost += total
    wo.save(update_fields=["material_cost", "updated_at"])
    _touch_started(wo)
    log_action(AuditLog.CREATED, issue, user=user, document_number=issue.reference, company=wo.company)
    return issue


@transaction.atomic
def return_materials(wo, quantities, *, user=None):
    """Unused material goes back to the store at the cost it was issued at."""
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    if wo.status not in (WorkOrder.RELEASED, WorkOrder.IN_PROGRESS, WorkOrder.COMPLETED):
        raise ERPError("Materials cannot be returned in this status.")
    mats = {m.pk: m for m in wo.materials.select_for_update()}
    issue = MaterialIssue.objects.create(company=wo.company, reference=next_reference(wo.company, "material_issue"), work_order=wo,
                                         kind=MaterialIssue.RETURN, created_by=_u(user))
    total = ZERO
    for pk, qty in quantities.items():
        m, qty = mats.get(pk), q3(qty)
        if m is None:
            raise ERPError("A material does not belong to this work order.")
        if qty <= 0 or qty > m.issued_quantity:
            raise ERPError(f"{m.component}: can return between 0.001 and {m.issued_quantity}.")
        unit = (m.issued_value / m.issued_quantity) if m.issued_quantity else ZERO
        value = q2(unit * qty) if qty != m.issued_quantity else m.issued_value
        post_stock_movement(m.component, wo.source_warehouse, qty, reference_type="WO_RETURN", reference_no=issue.reference,
                            unit_cost=q4(unit), user=user, remarks=wo.reference)
        m.issued_quantity -= qty
        m.issued_value -= value
        m.save(update_fields=["issued_quantity", "issued_value"])
        MaterialIssueLine.objects.create(issue=issue, material=m, quantity=qty, unit_cost=q4(unit))
        total += value
    total = q2(total)
    issue.total_value = total
    issue.save(update_fields=["total_value", "updated_at"])
    if total > 0:
        create_journal_entry(wo.company, date=timezone.localdate(), narration=f"{issue.reference} return from {wo.reference}",
                             source=("material_return", issue.pk), user=user, store=wo.source_warehouse.store,
                             lines=[{"account": inventory_account_key(wo.source_warehouse), "debit": total},
                                    {"account": "wip_inventory", "credit": total}])
    wo.material_cost -= total
    wo.save(update_fields=["material_cost", "updated_at"])
    return issue


# ================================================================== job cards
@transaction.atomic
def start_job_card(jc, *, operator=None, machine=None, user=None):
    jc = JobCard.objects.select_for_update().select_related("work_order").get(pk=jc.pk)
    wo = jc.work_order
    if wo.status not in (WorkOrder.RELEASED, WorkOrder.IN_PROGRESS):
        raise ERPError("The work order is not released.")
    if jc.status != JobCard.PENDING:
        raise ERPError("This job card was already started.")
    previous = JobCard.objects.filter(work_order=wo, sequence__lt=jc.sequence).exclude(status=JobCard.COMPLETED)
    if previous.exists():
        raise ERPError(f"Finish the earlier operation first ({previous.order_by('sequence').first().operation}).")
    if operator is not None and operator.company_id != wo.company_id:
        raise ERPError("The operator belongs to a different company.")
    if operator is not None and operator.status != "active":
        raise ERPError("The operator is not active.")
    jc.operator, jc.machine = operator or jc.operator, machine or jc.machine
    jc.status, jc.started_at = JobCard.IN_PROGRESS, timezone.now()
    jc.save()
    _touch_started(wo)
    return jc


@transaction.atomic
def complete_job_card(jc, completed, rejected=0, minutes=None, *, user=None):
    jc = JobCard.objects.select_for_update().select_related("work_order", "operation").get(pk=jc.pk)
    wo = jc.work_order
    if jc.status != JobCard.IN_PROGRESS:
        raise ERPError("Start the job card first.")
    completed, rejected = q3(completed), q3(rejected)
    if completed < 0 or rejected < 0 or completed + rejected == 0:
        raise ERPError("Enter the completed and/or rejected quantity.")
    previous = JobCard.objects.filter(work_order=wo, sequence__lt=jc.sequence).order_by("-sequence").first()
    available = previous.completed_quantity if previous else wo.planned_quantity
    if completed + rejected > available:
        raise ERPError(f"Only {available} unit(s) arrived from the previous step; cannot process {completed + rejected}.")
    if minutes is None:
        minutes = jc.standard_minutes * (completed + rejected) / wo.planned_quantity if wo.planned_quantity else ZERO
    minutes = q2(minutes)
    if minutes < 0:
        raise ERPError("Minutes cannot be negative.")
    jc.actual_minutes, jc.finished_at = minutes, timezone.now()
    jc.completed_quantity, jc.rejected_quantity = completed, rejected

    hours = minutes / 60
    labor, overhead = q2(hours * jc.labor_rate_per_hour), q2(hours * jc.overhead_rate_per_hour)
    if labor + overhead > 0:
        create_journal_entry(wo.company, date=timezone.localdate(), narration=f"{jc.reference} labour & overhead on {wo.reference}",
                             source=("job_card", jc.pk), user=user,
                             lines=[{"account": "wip_inventory", "debit": labor + overhead},
                                    {"account": "production_overhead_absorbed", "credit": labor + overhead}])
        wo.labor_cost += labor
        wo.overhead_cost += overhead
        wo.save(update_fields=["labor_cost", "overhead_cost", "updated_at"])
    if jc.operation.qc_required and completed > 0:
        jc.status = JobCard.QC_PENDING
        jc.save()
        create_check(wo.company, "in_process", "job_card", jc.pk, wo.variant, completed, source_reference=jc.reference, user=user)
    else:
        jc.status = JobCard.COMPLETED
        jc.save()
    return jc


@transaction.atomic
def _on_job_card_qc(check):
    jc = JobCard.objects.select_for_update().get(pk=check.source_id)
    if jc.status != JobCard.QC_PENDING:
        return
    jc.rejected_quantity += check.rejected_quantity
    jc.completed_quantity = check.accepted_quantity
    jc.status = JobCard.COMPLETED
    jc.save(update_fields=["rejected_quantity", "completed_quantity", "status", "updated_at"])


# ================================================================== finished goods
def _pending_qc_qty(wo):
    return sum((r.quantity for r in wo.receipts.filter(status=ProductionReceipt.QC_PENDING)), ZERO)


def _post_receipt(receipt, accepted, rejected, user=None):
    wo = WorkOrder.objects.select_for_update().get(pk=receipt.work_order_id)
    unit_cost = q4(wo.total_cost / wo.planned_quantity) if wo.planned_quantity else ZERO
    value = min(q2(unit_cost * accepted), wo.wip_balance)
    value = max(value, ZERO)
    if accepted > 0:
        post_stock_movement(wo.variant, wo.fg_warehouse, q3(accepted), reference_type="WO_RECEIPT", reference_no=receipt.reference,
                            unit_cost=q4(value / accepted) if accepted else ZERO, user=user, remarks=wo.reference)
    if value > 0:
        create_journal_entry(wo.company, date=timezone.localdate(), narration=f"{receipt.reference} finished goods from {wo.reference}",
                             source=("production_receipt", receipt.pk), user=user, store=wo.fg_warehouse.store,
                             lines=[{"account": inventory_account_key(wo.fg_warehouse), "debit": value},
                                    {"account": "wip_inventory", "credit": value}])
    wo.received_quantity += accepted
    wo.fg_value_received += value
    wo.save(update_fields=["received_quantity", "fg_value_received", "updated_at"])
    receipt.accepted_quantity, receipt.rejected_quantity, receipt.value = accepted, rejected, value
    receipt.status = ProductionReceipt.POSTED
    receipt.save()


@transaction.atomic
def receive_finished_goods(wo, quantity, *, user=None):
    """Book finished units into stock. Items flagged 'QC required' wait in a final QC check first."""
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    if wo.status not in (WorkOrder.IN_PROGRESS, WorkOrder.COMPLETED):
        raise ERPError("Finished goods can only be received on an in-progress or completed work order.")
    quantity = q3(quantity)
    if quantity <= 0:
        raise ERPError("Quantity must be greater than zero.")
    if wo.total_cost <= 0:
        raise ERPError("No material or labour cost has been charged yet; issue materials first.")
    open_qty = wo.planned_quantity - wo.received_quantity - _pending_qc_qty(wo)
    if quantity > open_qty:
        raise ERPError(f"Only {open_qty} unit(s) remain on the order (planned {wo.planned_quantity}). Raise another work order for extra output.")
    last = wo.job_cards.order_by("-sequence").first()
    if last is not None:
        done_upstream = last.completed_quantity if last.status == JobCard.COMPLETED else ZERO
        already = wo.received_quantity + _pending_qc_qty(wo)
        if last.status != JobCard.COMPLETED:
            raise ERPError("The last operation is not completed yet.")
        if already + quantity > done_upstream:
            raise ERPError(f"Only {done_upstream - already} unit(s) have passed the last operation.")
    receipt = ProductionReceipt.objects.create(company=wo.company, reference=next_reference(wo.company, "production_receipt"),
                                               work_order=wo, quantity=quantity, created_by=_u(user))
    profile = item_profile_of(wo.variant)
    if profile and profile.qc_required_on_receipt:
        receipt.status = ProductionReceipt.QC_PENDING
        receipt.save(update_fields=["status", "updated_at"])
        create_check(wo.company, "final", "production_receipt", receipt.pk, wo.variant, quantity, source_reference=receipt.reference, user=user)
    else:
        _post_receipt(receipt, quantity, ZERO, user)
    log_action(AuditLog.CREATED, receipt, user=user, document_number=receipt.reference, company=wo.company)
    return receipt


@transaction.atomic
def _on_receipt_qc(check):
    receipt = ProductionReceipt.objects.select_for_update().get(pk=check.source_id)
    if receipt.status != ProductionReceipt.QC_PENDING:
        return
    _post_receipt(receipt, check.accepted_quantity, check.rejected_quantity, check.inspector)


@transaction.atomic
def complete_work_order(wo, user=None):
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    if wo.status != WorkOrder.IN_PROGRESS:
        raise ERPError("Only an in-progress work order can be completed.")
    if wo.job_cards.exclude(status=JobCard.COMPLETED).exists():
        raise ERPError("Some job cards are not completed.")
    wo.status = WorkOrder.COMPLETED
    wo.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.OTHER, wo, user=user, note="completed", document_number=wo.reference, company=wo.company)
    return wo


@transaction.atomic
def close_work_order(wo, user=None):
    """Writes the leftover WIP (cost of units not produced / rejected / unused material) to Production Variance."""
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    if wo.status not in (WorkOrder.IN_PROGRESS, WorkOrder.COMPLETED):
        raise ERPError("Only an in-progress or completed work order can be closed.")
    if _pending_qc_qty(wo) > 0:
        raise ERPError("Finished goods are still waiting for QC.")
    if wo.job_cards.filter(status=JobCard.QC_PENDING).exists():
        raise ERPError("A job card is still waiting for QC.")
    leftover = q2(wo.wip_balance)
    if leftover > 0:
        create_journal_entry(wo.company, date=timezone.localdate(), narration=f"Close {wo.reference}: production variance",
                             source=("wo_close", wo.pk), user=user,
                             lines=[{"account": "production_variance", "debit": leftover}, {"account": "wip_inventory", "credit": leftover}])
        wo.variance_written_off = leftover
    wo.status, wo.closed_at = WorkOrder.CLOSED, timezone.now()
    wo.save()
    log_action(AuditLog.OTHER, wo, user=user, note="closed", document_number=wo.reference, company=wo.company)
    return wo


@transaction.atomic
def cancel_work_order(wo, user=None, reason=""):
    wo = WorkOrder.objects.select_for_update().get(pk=wo.pk)
    if wo.status in (WorkOrder.CLOSED, WorkOrder.CANCELLED):
        raise ERPError("This work order is already finished.")
    if wo.materials.filter(issued_quantity__gt=0).exists():
        raise ERPError("Materials are issued; return them to the store first.")
    if wo.job_cards.exclude(status=JobCard.PENDING).exists() or wo.receipts.exists():
        raise ERPError("Production has started; close the work order instead.")
    wo.status = WorkOrder.CANCELLED
    wo.save(update_fields=["status", "updated_at"])
    log_action(AuditLog.CANCELLED, wo, user=user, note=reason[:200], document_number=wo.reference, company=wo.company)
    return wo
