"""
Every ERP report as plain data: report(company, p) -> (headers, rows).
`p` is a Params object with date_from, date_to, as_of, store. Each report names the permission needed to see it,
so the same registry feeds the on-screen page, the Excel export and the optional PDF.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable, Optional

from core.money import q2

ZERO = Decimal("0")


@dataclass
class Params:
    date_from: date
    date_to: date
    as_of: date
    store: Optional[object] = None


@dataclass
class Report:
    key: str
    title: str
    group: str
    perm: str
    func: Callable
    uses: str = "range"          # "range" | "as_of" | "none"


REPORTS = {}


def register(key, title, group, perm, uses="range"):
    def deco(func):
        REPORTS[key] = Report(key, title, group, perm, func, uses)
        return func
    return deco


# ------------------------------------------------------------------ accounting
@register("trial_balance", "Trial balance", "Accounting", "accounting.view_journalentry", "as_of")
def trial_balance_report(company, p):
    from accounting.reports import trial_balance
    data = trial_balance(company, as_of=p.as_of, store=p.store)
    rows = [[r["code"], r["name"], r["type"], r["debit"], r["credit"]] for r in data["rows"]]
    rows.append(["", "TOTAL", "", data["total_debit"], data["total_credit"]])
    return ["Code", "Account", "Type", "Debit", "Credit"], rows


@register("profit_and_loss", "Profit & loss", "Accounting", "accounting.view_journalentry")
def pnl_report(company, p):
    from accounting.reports import profit_and_loss
    d = profit_and_loss(company, date_from=p.date_from, date_to=p.date_to, store=p.store)
    rows = [["Revenue", "", ""]] + [[r["code"], r["name"], r["amount"]] for r in d["revenue"]]
    rows += [["", "Total revenue", d["total_revenue"]], ["Expenses", "", ""]] + [[r["code"], r["name"], r["amount"]] for r in d["expenses"]]
    rows += [["", "Total expenses", d["total_expenses"]], ["", "Gross profit", d["gross_profit"]], ["", "NET PROFIT", d["net_profit"]]]
    return ["Code", "Account", "Amount"], rows


@register("balance_sheet", "Balance sheet", "Accounting", "accounting.view_journalentry", "as_of")
def bs_report(company, p):
    from accounting.reports import balance_sheet
    d = balance_sheet(company, as_of=p.as_of)
    rows = []
    for title, key, total in (("Assets", "assets", "total_assets"), ("Liabilities", "liabilities", "total_liabilities"), ("Equity", "equity", "total_equity")):
        rows.append([title, "", ""])
        rows += [[r["code"], r["name"], r["amount"]] for r in d[key]]
        rows.append(["", f"Total {title.lower()}", d[total]])
    rows.append(["", "Balanced" if d["balanced"] else "NOT BALANCED", ""])
    return ["Code", "Account", "Amount"], rows


@register("cash_flow", "Cash flow", "Accounting", "accounting.view_journalentry")
def cash_flow_report(company, p):
    from accounting.reports import cash_flow
    d = cash_flow(company, date_from=p.date_from, date_to=p.date_to)
    rows = [[k.replace("_", " ").title(), v] for k, v in d.items() if isinstance(v, (Decimal, int))]
    return ["Item", "Amount"], rows


@register("tax", "Tax summary", "Accounting", "accounting.view_journalentry")
def tax_report_view(company, p):
    from accounting.reports import tax_report
    d = tax_report(company, date_from=p.date_from, date_to=p.date_to)
    return ["Item", "Amount"], [["Output tax (collected)", d["output_tax"]], ["Input tax (paid)", d["input_tax"]], ["Net payable", d["net_payable"]]]


# ------------------------------------------------------------------ sales
@register("ar_aging", "Customer receivables ageing", "Sales", "distribution.view_salesinvoice", "as_of")
def ar_aging_report(company, p):
    from distribution.services import ar_aging
    d = ar_aging(company, as_of=p.as_of)
    rows = [[party, r["0-30"], r["31-60"], r["61-90"], r["90+"], r["total"]] for party, r in d["by_party"].items()]
    s = d["summary"]
    rows.append(["TOTAL", s["0-30"], s["31-60"], s["61-90"], s["90+"], d["total"]])
    return ["Customer", "0-30", "31-60", "61-90", "90+", "Total"], rows


@register("sales_by_customer", "Sales by customer", "Sales", "distribution.view_salesinvoice")
def sales_by_customer(company, p):
    from distribution.services import sales_summary
    rows = [[r["customer__name"], r["sales"], r["tax"], r["total"], r["credited"], r["paid"]] for r in sales_summary(company, p.date_from, p.date_to)]
    return ["Customer", "Sales", "Tax", "Invoiced", "Credited", "Paid"], rows


@register("gross_margin", "Gross margin on invoices", "Sales", "distribution.view_salesinvoice")
def margin_report(company, p):
    from distribution.services import gross_margin
    d = gross_margin(company, p.date_from, p.date_to)
    return ["Item", "Value"], [["Revenue", d["revenue"]], ["Cost of goods", d["cost"]], ["Margin", d["margin"]], ["Margin %", d["margin_percent"]]]


@register("pos_daily", "POS daily summary (end of range)", "Sales", "sales.view_sale")
def pos_daily(company, p):
    from pos_erp.services import daily_sales_summary
    store = p.store
    if store is None:
        from tenants.models import Store
        store = Store.objects.filter(company=company).first()
    if store is None:
        return ["Item", "Value"], []
    d = daily_sales_summary(store, p.date_to)
    rows = [["Sales count", d["count"]], ["Gross", d["gross"]], ["Discount", d["discount"]], ["Tax", d["tax"]], ["Total", d["total"]],
            ["Returns refunded", d["returns"]]] + [[f"Paid by {m}", v] for m, v in d["by_method"].items()]
    return ["Item", "Value"], rows


# ------------------------------------------------------------------ purchasing
@register("ap_aging", "Supplier payables ageing", "Purchasing", "procurement.view_purchaseinvoice", "as_of")
def ap_aging_report(company, p):
    from procurement.services import ap_aging
    d = ap_aging(company, as_of=p.as_of)
    rows = [[party, r["0-30"], r["31-60"], r["61-90"], r["90+"], r["total"]] for party, r in d["by_party"].items()]
    s = d["summary"]
    rows.append(["TOTAL", s["0-30"], s["31-60"], s["61-90"], s["90+"], d["total"]])
    return ["Supplier", "0-30", "31-60", "61-90", "90+", "Total"], rows


@register("purchase_by_vendor", "Purchases by supplier", "Purchasing", "procurement.view_purchaseinvoice")
def purchases_by_vendor(company, p):
    from procurement.services import purchase_summary
    rows = [[r["supplier__name"], r["invoices"], r["tax"], r["paid"]] for r in purchase_summary(company, p.date_from, p.date_to)]
    return ["Supplier", "Invoiced", "Tax", "Paid"], rows


# ------------------------------------------------------------------ inventory
@register("stock_valuation", "Stock valuation (weighted average)", "Inventory", "stock.view_stockledgerentry", "none")
def stock_valuation_report(company, p):
    from stock.services import stock_valuation
    rows, total = stock_valuation(company)
    out = [[str(r["variant"]), str(r["warehouse"]), r["quantity"], r["avg_cost"], r["value"]] for r in rows]
    out.append(["TOTAL", "", "", "", total])
    return ["Item", "Warehouse", "Quantity", "Avg cost", "Value"], out


@register("gl_vs_stock", "Inventory: ledger vs stock", "Inventory", "stock.view_stockledgerentry", "none")
def gl_vs_stock(company, p):
    from stock.services import inventory_gl_vs_stock
    return ["Account", "GL", "Stock", "Difference"], [[r["account"], r["gl"], r["stock"], r["difference"]] for r in inventory_gl_vs_stock(company)]


@register("reorder", "Reorder alerts", "Inventory", "stock.view_stockledgerentry", "none")
def reorder(company, p):
    from stock.services import reorder_alerts
    return ["Item", "On hand", "Reorder level"], [[str(r["variant"]), r["on_hand"], r["reorder_level"]] for r in reorder_alerts(company)]


@register("expiring", "Batches expiring within 30 days", "Inventory", "stock.view_stockledgerentry", "none")
def expiring(company, p):
    from stock.services import expiring_batches
    return ["Item", "Batch", "Warehouse", "Expiry", "Quantity"], [
        [str(b.batch.variant), b.batch.batch_no, str(b.warehouse), b.batch.expiry_date, b.quantity] for b in expiring_batches(company)]


# ------------------------------------------------------------------ production
@register("work_orders", "Work orders", "Production", "production.view_workorder", "none")
def work_orders(company, p):
    from production.reports import work_order_status_report
    return ["Work order", "Item", "Status", "Planned", "Received", "Pending", "Due", "Cost to date"], [
        [r["reference"], r["item"], r["status"], r["planned"], r["received"], r["pending"], r["due_date"], r["cost_to_date"]]
        for r in work_order_status_report(company)]


@register("wip", "Work in progress", "Production", "production.view_workorder", "none")
def wip(company, p):
    from production.reports import wip_report
    rows, total = wip_report(company)
    out = [[r["reference"], r["item"], r["wip"]] for r in rows] + [["TOTAL", "", total]]
    return ["Work order", "Item", "WIP value"], out


@register("scrap", "Scrap & rejects by operation", "Production", "production.view_workorder", "none")
def scrap(company, p):
    from production.reports import scrap_report
    return ["Operation", "Rejected quantity", "Job cards"], [[r["operation__name"], r["rejected"], r["cards"]] for r in scrap_report(company)]


@register("machines", "Machine utilisation", "Production", "production.view_workorder", "none")
def machines(company, p):
    from production.reports import machine_utilization
    return ["Machine", "Name", "Hours", "Jobs"], [[r["machine__code"], r["machine__name"], r["hours"], r["jobs"]] for r in machine_utilization(company)]


# ------------------------------------------------------------------ helpers
def visible_reports(user):
    return [r for r in REPORTS.values() if user.is_superuser or user.has_perm(r.perm)]


def run(key, company, params):
    report = REPORTS[key]
    headers, rows = report.func(company, params)
    return report, headers, rows


# extra HR / payroll / accounting reports live in their own module (they register themselves on import)
from . import extra_reports  # noqa: E402,F401
