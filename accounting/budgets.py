"""Actual vs budget by account and month of the fiscal year."""
import calendar
from datetime import date

from django.db.models import Sum

from core.money import ZERO, q2
from .models import Account, BudgetLine, JournalLine, POSTED_STATUSES


def _month_range(company, fiscal_year, month_index):
    start_month = company.fiscal_year_start_month
    month = (start_month - 1 + month_index - 1) % 12 + 1
    year = fiscal_year + ((start_month - 1 + month_index - 1) // 12)
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def actual_vs_budget(budget):
    company = budget.company
    rows = []
    totals = {"budget": ZERO, "actual": ZERO}
    for line in BudgetLine.objects.filter(budget=budget).select_related("account").order_by("account__code", "month_index"):
        start, end = _month_range(company, budget.fiscal_year, line.month_index)
        qs = JournalLine.objects.filter(
            entry__company=company, entry__status__in=POSTED_STATUSES, account=line.account,
            entry__date__gte=start, entry__date__lte=end,
        )
        if budget.cost_center_id:
            qs = qs.filter(cost_center=budget.cost_center)
        if budget.store_id:
            qs = qs.filter(entry__store=budget.store)
        agg = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
        d, c = agg["d"] or ZERO, agg["c"] or ZERO
        actual = (d - c) if line.account.normal_balance == "debit" else (c - d)
        variance = line.amount - actual if line.account.account_type == Account.EXPENSE else actual - line.amount
        rows.append({"account": line.account.code, "name": line.account.name, "month_index": line.month_index,
                     "month": start.strftime("%b %Y"), "budget": q2(line.amount), "actual": q2(actual),
                     "variance": q2(variance)})
        totals["budget"] += line.amount
        totals["actual"] += actual
    return {"rows": rows, "total_budget": q2(totals["budget"]), "total_actual": q2(totals["actual"]),
            "total_variance": q2(totals["budget"] - totals["actual"])}
