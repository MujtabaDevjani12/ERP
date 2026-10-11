"""Role-aware dashboard: each block only appears for users who may see that module's data."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from core.money import q2

ZERO = Decimal("0")


def _can(user, perm):
    return user.is_superuser or user.has_perm(perm)


def _safe(func, default="n/a"):
    try:
        return func()
    except Exception:                                    # noqa: BLE001 - one broken module must not blank the whole dashboard
        return default


def dashboard(company, user, store=None):
    today = timezone.localdate()
    month_start = today.replace(day=1)
    blocks = []

    if _can(user, "accounting.view_journalentry"):
        from accounting.reports import balance_of, profit_and_loss
        pnl = _safe(lambda: profit_and_loss(company, date_from=month_start, date_to=today, store=store), None)
        blocks.append({"title": "Finance", "kpis": [
            {"label": "Cash in hand", "value": _safe(lambda: balance_of(company, "cash"))},
            {"label": "Bank", "value": _safe(lambda: balance_of(company, "bank"))},
            {"label": "Receivable", "value": _safe(lambda: balance_of(company, "ar"))},
            {"label": "Payable", "value": _safe(lambda: balance_of(company, "ap"))},
            {"label": "Revenue (this month)", "value": pnl["total_revenue"] if pnl else "n/a"},
            {"label": "Net profit (this month)", "value": pnl["net_profit"] if pnl else "n/a"}]})

    if _can(user, "sales.view_sale"):
        from sales.models import Sale
        qs = Sale.objects.exclude(status__in=["held", "cancelled"])
        if store is not None:
            qs = qs.filter(store=store)
        elif company is not None:
            qs = qs.filter(store__company=company)
        today_total = qs.filter(created_at__date=today).aggregate(t=Sum("total_amount"))["t"] or ZERO
        month_total = qs.filter(created_at__date__gte=month_start).aggregate(t=Sum("total_amount"))["t"] or ZERO
        blocks.append({"title": "Retail (POS)", "kpis": [
            {"label": "Sales today", "value": q2(today_total)}, {"label": "Sales this month", "value": q2(month_total)},
            {"label": "Bills today", "value": qs.filter(created_at__date=today).count()}]})

    if _can(user, "distribution.view_salesorder"):
        from distribution.models import SalesOrder
        from distribution.services import ar_aging
        blocks.append({"title": "Wholesale", "kpis": [
            {"label": "Orders to deliver", "value": SalesOrder.objects.filter(company=company, status__in=["confirmed", "partially_delivered"]).count()},
            {"label": "Waiting for approval", "value": SalesOrder.objects.filter(company=company, status="pending_approval").count()},
            {"label": "Overdue 90+ days", "value": _safe(lambda: ar_aging(company)["summary"]["90+"])}]})

    if _can(user, "procurement.view_purchaseorderext"):
        from procurement.models import GoodsReceipt, PurchaseOrderExt, PurchaseRequisition
        blocks.append({"title": "Purchasing", "kpis": [
            {"label": "Requisitions pending", "value": PurchaseRequisition.objects.filter(company=company, status="pending_approval").count()},
            {"label": "POs pending approval", "value": PurchaseOrderExt.objects.filter(company=company, status="pending_approval").count()},
            {"label": "Receipts waiting for QC", "value": GoodsReceipt.objects.filter(company=company, status="qc_pending").count()}]})

    if _can(user, "stock.view_stockledgerentry"):
        from stock.services import reorder_alerts, stock_valuation
        blocks.append({"title": "Inventory", "kpis": [
            {"label": "Stock value", "value": _safe(lambda: stock_valuation(company)[1])},
            {"label": "Items to reorder", "value": _safe(lambda: len(reorder_alerts(company)))}]})

    if _can(user, "production.view_workorder"):
        from production.models import WorkOrder
        active = WorkOrder.objects.filter(company=company, status__in=["released", "in_progress"])
        blocks.append({"title": "Production", "kpis": [
            {"label": "Active work orders", "value": active.count()},
            {"label": "Overdue", "value": active.filter(due_date__lt=today).count()},
            {"label": "Awaiting approval", "value": WorkOrder.objects.filter(company=company, status="pending_approval").count()}]})

    if _can(user, "hr.view_employee"):
        from hr.models import Employee, LeaveRequest
        blocks.append({"title": "People", "kpis": [
            {"label": "Active employees", "value": Employee.objects.filter(company=company, status="active").count()},
            {"label": "Leave requests pending", "value": LeaveRequest.objects.filter(employee__company=company, status="pending_approval").count()}]})

    if _can(user, "approvals.view_approvalrequest"):
        from approvals.services import pending_for
        blocks.append({"title": "My approvals", "kpis": [{"label": "Waiting for me", "value": _safe(lambda: len(list(pending_for(user))))}]})

    if _can(user, "pos_erp.view_pospostingissue"):
        from pos_erp.models import PosPostingIssue
        n = PosPostingIssue.objects.filter(company=company, resolved=False).count()
        if n:
            blocks.append({"title": "Attention", "kpis": [{"label": "POS documents that failed to post", "value": n}]})
    return blocks


def sales_trend(company, days=14, store=None):
    """Daily POS totals for a small bar chart."""
    from sales.models import Sale
    today = timezone.localdate()
    out = []
    qs = Sale.objects.exclude(status__in=["held", "cancelled"]).filter(store__company=company)
    if store is not None:
        qs = qs.filter(store=store)
    for i in range(days - 1, -1, -1):
        day = today - timedelta(days=i)
        out.append({"day": day, "total": q2(qs.filter(created_at__date=day).aggregate(t=Sum("total_amount"))["t"] or ZERO)})
    return out
