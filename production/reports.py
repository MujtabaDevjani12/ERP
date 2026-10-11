"""Production reports (plain data; Excel export lives in reports_erp)."""
from decimal import Decimal

from django.db.models import Count, Q, Sum

from core.money import q2
from .models import JobCard, WorkOrder

ZERO = Decimal("0")


def work_order_status_report(company, status=None):
    qs = WorkOrder.objects.filter(company=company).select_related("variant__product")
    if status:
        qs = qs.filter(status=status)
    rows = []
    for wo in qs:
        rows.append({"reference": wo.reference, "item": str(wo.variant), "status": wo.status, "planned": wo.planned_quantity,
                     "received": wo.received_quantity, "pending": wo.planned_quantity - wo.received_quantity, "due_date": wo.due_date,
                     "cost_to_date": q2(wo.total_cost)})
    return rows


def pending_work_orders(company):
    return WorkOrder.objects.filter(company=company, status__in=["released", "in_progress"]).select_related("variant")


def delayed_work_orders(company, today):
    return pending_work_orders(company).filter(due_date__lt=today)


def material_consumption(wo):
    """Issued vs required per component, with variance."""
    return [{"component": m.component, "required": m.required_quantity, "issued": m.issued_quantity,
             "variance": m.issued_quantity - m.required_quantity, "value": m.issued_value} for m in wo.materials.select_related("component")]


def work_order_cost(wo):
    produced = wo.received_quantity
    return {"material": q2(wo.material_cost), "labor": q2(wo.labor_cost), "overhead": q2(wo.overhead_cost), "total": q2(wo.total_cost),
            "received_value": q2(wo.fg_value_received), "variance_written_off": q2(wo.variance_written_off),
            "wip_balance": q2(wo.wip_balance), "unit_cost": (q2(wo.fg_value_received / produced) if produced else ZERO)}


def production_efficiency(company, date_from=None, date_to=None):
    qs = WorkOrder.objects.filter(company=company).exclude(status__in=["draft", "cancelled", "rejected"])
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    agg = qs.aggregate(planned=Sum("planned_quantity"), received=Sum("received_quantity"))
    planned, received = agg["planned"] or ZERO, agg["received"] or ZERO
    return {"planned": planned, "received": received, "efficiency_percent": q2(received * 100 / planned) if planned else ZERO}


def scrap_report(company):
    return list(JobCard.objects.filter(work_order__company=company, rejected_quantity__gt=0).values(
        "operation__name").annotate(rejected=Sum("rejected_quantity"), cards=Count("id")).order_by("-rejected"))


def machine_utilization(company):
    rows = JobCard.objects.filter(work_order__company=company, machine__isnull=False, status="completed").values(
        "machine__code", "machine__name").annotate(minutes=Sum("actual_minutes"), jobs=Count("id")).order_by("-minutes")
    return [{**r, "hours": q2((r["minutes"] or ZERO) / 60)} for r in rows]


def operator_performance(company):
    rows = JobCard.objects.filter(work_order__company=company, operator__isnull=False, status="completed").values(
        "operator__employee_code", "operator__full_name").annotate(
        completed=Sum("completed_quantity"), rejected=Sum("rejected_quantity"), minutes=Sum("actual_minutes"), standard=Sum("standard_minutes"))
    out = []
    for r in rows:
        eff = q2((r["standard"] or ZERO) * 100 / r["minutes"]) if r["minutes"] else ZERO
        out.append({**r, "efficiency_percent": eff})
    return out


def wip_report(company):
    open_orders = WorkOrder.objects.filter(company=company, status__in=["released", "in_progress", "completed"])
    rows = [{"reference": w.reference, "item": str(w.variant), "wip": q2(w.wip_balance)} for w in open_orders if w.wip_balance != 0]
    return rows, q2(sum((r["wip"] for r in rows), ZERO))
