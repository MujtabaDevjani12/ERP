"""The report pack: six store-scoped reports (sales, products, payments, stock value, profit & loss, customers).

Every report is a function  fn(request, store, date_from, date_to) -> dict  with
    kpis   [(label, kind, value)]            kind = money | int | text
    chart  {"type": "bar|line|doughnut", "labels": [...], "series": [{"label", "data"}]} or None
    cols   [(key, label, kind)]              kind = text | money | num | date
    rows   [dict]                            one dict per table row (keys = col keys)
    notes  optional one-line caveat
Views then render any of them with the same page and CSV export, so a new report is one function and one REGISTRY line."""
from collections import OrderedDict
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Max, Q, Sum
from django.db.models.functions import TruncDate

ZERO = Decimal("0")
MONEY = DecimalField(max_digits=16, decimal_places=2)


def _d(v):
    return v if v is not None else ZERO


def _sales(store, date_from, date_to):
    from sales.models import Sale
    return Sale.objects.filter(store=store, status="completed", created_at__date__gte=date_from, created_at__date__lte=date_to)


# ----------------------------------------------------------------------------------------------------------- 1. sales
def sales_report(request, store, date_from, date_to):
    from sales.models import Sale
    qs = _sales(store, date_from, date_to)
    biller = request.GET.get("biller")
    if biller and biller.isdigit():
        qs = qs.filter(biller_id=int(biller))
    per_day = list(qs.annotate(day=TruncDate("created_at")).values("day").annotate(
        n=Count("id"), subtotal=Sum("subtotal"), discount=Sum("discount_amount"), tax=Sum("tax_amount"), total=Sum("total_amount")).order_by("-day"))
    paid = {r["day"]: _d(r["paid"]) for r in qs.annotate(day=TruncDate("created_at")).values("day").annotate(paid=Sum("payments__amount"))}
    rows = [{"day": r["day"], "n": r["n"], "subtotal": _d(r["subtotal"]), "discount": _d(r["discount"]), "tax": _d(r["tax"]), "total": _d(r["total"]),
             "paid": paid.get(r["day"], ZERO), "due": _d(r["total"]) - paid.get(r["day"], ZERO)} for r in per_day]
    total, count = sum((r["total"] for r in rows), ZERO), sum(r["n"] for r in rows)
    chron = list(reversed(rows))
    return {
        "kpis": [("Net sales", "money", total), ("Invoices", "int", count), ("Average invoice", "money", (total / count) if count else ZERO),
                 ("Discounts given", "money", sum((r["discount"] for r in rows), ZERO)), ("Tax collected", "money", sum((r["tax"] for r in rows), ZERO)),
                 ("Still unpaid", "money", sum((r["due"] for r in rows), ZERO))],
        "chart": {"type": "bar", "labels": [r["day"].strftime("%d %b") for r in chron], "series": [{"label": "Sales", "data": [float(r["total"]) for r in chron]}]},
        "cols": [("day", "Date", "date"), ("n", "Invoices", "num"), ("subtotal", "Subtotal", "money"), ("discount", "Discount", "money"), ("tax", "Tax", "money"),
                 ("total", "Total", "money"), ("paid", "Paid", "money"), ("due", "Due", "money")],
        "rows": rows,
        "extra_filters": [{"name": "biller", "label": "Biller", "options": [(u.pk, u.get_full_name() or u.username) for u in
                                                                              store.staff.filter(is_biller=True).order_by("username")]}],
    }


# -------------------------------------------------------------------------------------------------------- 2. products
def products_report(request, store, date_from, date_to):
    from sales.models import SaleItem
    qs = SaleItem.objects.filter(sale__in=_sales(store, date_from, date_to))
    rows = []
    for r in qs.values("variant_id", "variant__product__name", "variant__name", "variant__sku", "variant__cost_price").annotate(
            qty=Sum("quantity"), revenue=Sum(ExpressionWrapper(F("quantity") * F("unit_price") - F("discount_amount"), output_field=MONEY))).order_by("-revenue"):
        cost = _d(r["variant__cost_price"]) * _d(r["qty"])
        label = r["variant__product__name"] + (f" - {r['variant__name']}" if r["variant__name"] and r["variant__name"] != "Default" else "")
        rows.append({"product": label, "sku": r["variant__sku"], "qty": _d(r["qty"]), "revenue": _d(r["revenue"]), "cost": cost, "profit": _d(r["revenue"]) - cost})
    top = rows[:10]
    return {
        "kpis": [("Products sold", "int", len(rows)), ("Units sold", "num", sum((r["qty"] for r in rows), ZERO)), ("Revenue", "money", sum((r["revenue"] for r in rows), ZERO)),
                 ("Estimated profit", "money", sum((r["profit"] for r in rows), ZERO))],
        "chart": {"type": "bar", "labels": [r["product"][:22] for r in top], "series": [{"label": "Revenue", "data": [float(r["revenue"]) for r in top]}], "horizontal": True},
        "cols": [("product", "Product", "text"), ("sku", "SKU", "text"), ("qty", "Units sold", "num"), ("revenue", "Revenue", "money"), ("cost", "Est. cost", "money"), ("profit", "Est. profit", "money")],
        "rows": rows,
        "notes": "Cost uses each product's current cost price, so profit is an estimate.",
    }


# -------------------------------------------------------------------------------------------------------- 3. payments
def payments_report(request, store, date_from, date_to):
    from payments.models import Payment, PaymentMethod, Refund
    labels = PaymentMethod.label_map(store)
    pays = Payment.objects.filter(sale__store=store, status="completed", paid_at__date__gte=date_from, paid_at__date__lte=date_to)
    refunds = Refund.objects.filter(order__store=store, created_at__date__gte=date_from, created_at__date__lte=date_to)
    got = {r["method"]: r for r in pays.values("method").annotate(n=Count("id"), total=Sum("amount"))}
    back = {r["method"]: _d(r["total"]) for r in refunds.values("method").annotate(total=Sum("amount"))}
    rows = []
    for m in sorted(set(got) | set(back), key=lambda k: -_d(got.get(k, {}).get("total"))):
        rec = _d(got.get(m, {}).get("total"))
        rows.append({"method": labels.get(m, m.replace("_", " ").title()), "n": got.get(m, {}).get("n", 0), "received": rec, "refunded": back.get(m, ZERO), "net": rec - back.get(m, ZERO)})
    return {
        "kpis": [("Received", "money", sum((r["received"] for r in rows), ZERO)), ("Refunded", "money", sum((r["refunded"] for r in rows), ZERO)),
                 ("Net collected", "money", sum((r["net"] for r in rows), ZERO)), ("Payments", "int", sum(r["n"] for r in rows))],
        "chart": {"type": "doughnut", "labels": [r["method"] for r in rows], "series": [{"label": "Net", "data": [float(max(r["net"], ZERO)) for r in rows]}]},
        "cols": [("method", "Method", "text"), ("n", "Payments", "num"), ("received", "Received", "money"), ("refunded", "Refunded", "money"), ("net", "Net", "money")],
        "rows": rows,
    }


# ------------------------------------------------------------------------------------------------------ 4. stock value
def stock_value_report(request, store, date_from, date_to):
    from inventory.models import StockItem
    from tenants.models import Warehouse
    qs = StockItem.objects.filter(warehouse__store=store, quantity__gt=0).select_related("variant", "variant__product", "warehouse")
    wh = request.GET.get("warehouse")
    if wh and wh.isdigit():
        qs = qs.filter(warehouse_id=int(wh))
    rows = []
    for s in qs.order_by("variant__product__name"):
        cost, price = _d(s.variant.cost_price), _d(s.variant.selling_price)
        rows.append({"product": str(s.variant), "sku": s.variant.sku, "warehouse": s.warehouse.name, "qty": s.quantity, "cost": cost, "value": s.quantity * cost,
                     "retail": s.quantity * price})
    by_wh = OrderedDict()
    for r in rows:
        by_wh[r["warehouse"]] = by_wh.get(r["warehouse"], ZERO) + r["value"]
    value = sum((r["value"] for r in rows), ZERO)
    retail = sum((r["retail"] for r in rows), ZERO)
    return {
        "no_dates": True,
        "kpis": [("Stock at cost", "money", value), ("Stock at selling price", "money", retail), ("Potential margin", "money", retail - value),
                 ("Units on hand", "num", sum((r["qty"] for r in rows), ZERO)), ("Products", "int", len({r["sku"] for r in rows}))],
        "chart": {"type": "doughnut", "labels": list(by_wh), "series": [{"label": "Value at cost", "data": [float(v) for v in by_wh.values()]}]},
        "cols": [("product", "Product", "text"), ("sku", "SKU", "text"), ("warehouse", "Warehouse", "text"), ("qty", "On hand", "num"), ("cost", "Unit cost", "money"),
                 ("value", "Value at cost", "money"), ("retail", "Value at price", "money")],
        "rows": rows,
        "extra_filters": [{"name": "warehouse", "label": "Warehouse", "options": [(w.pk, w.name) for w in Warehouse.objects.filter(store=store).order_by("name")]}],
        "notes": "Valued at each product's cost price today.",
    }


# ------------------------------------------------------------------------------------------------------- 5. profit & loss
def profit_loss_report(request, store, date_from, date_to):
    from expenses.models import Expense
    from sales.models import SaleItem, SaleReturnItem
    sales = _sales(store, date_from, date_to)
    revenue = _d(sales.aggregate(t=Sum("total_amount"))["t"])
    tax = _d(sales.aggregate(t=Sum("tax_amount"))["t"])
    items = SaleItem.objects.filter(sale__in=sales)
    cogs = sum((_d(r["cost"]) * _d(r["qty"]) for r in items.values("variant__cost_price").annotate(qty=Sum("quantity")).values("variant__cost_price", "qty")
                .annotate(cost=F("variant__cost_price"))), ZERO)
    returns = _d(SaleReturnItem.objects.filter(sale_return__sale__store=store, sale_return__created_at__date__gte=date_from, sale_return__created_at__date__lte=date_to)
                 .aggregate(t=Sum(ExpressionWrapper(F("quantity") * F("sale_item__unit_price"), output_field=MONEY)))["t"])
    exp = Expense.objects.filter(store=store, expense_date__gte=date_from, expense_date__lte=date_to)
    expenses = _d(exp.aggregate(t=Sum("amount"))["t"])
    gross = revenue - tax - returns - cogs
    net = gross - expenses
    rows = [{"line": "Sales (incl. tax)", "amount": revenue, "kind": "in"}, {"line": "Less: tax collected", "amount": -tax, "kind": "out"},
            {"line": "Less: returned goods", "amount": -returns, "kind": "out"}, {"line": "Less: cost of goods sold (estimate)", "amount": -cogs, "kind": "out"},
            {"line": "Gross profit", "amount": gross, "kind": "total"}]
    for r in exp.values("category__name").annotate(t=Sum("amount")).order_by("-t"):
        rows.append({"line": f"Expense - {r['category__name']}", "amount": -_d(r["t"]), "kind": "out"})
    rows.append({"line": "Net profit", "amount": net, "kind": "total"})
    return {
        "kpis": [("Sales", "money", revenue), ("Gross profit", "money", gross), ("Expenses", "money", expenses), ("Net profit", "money", net),
                 ("Margin", "text", f"{(net / (revenue - tax) * 100):.1f}%" if revenue - tax else "-")],
        "chart": {"type": "bar", "labels": ["Net sales", "Cost of goods", "Expenses", "Net profit"],
                  "series": [{"label": "Amount", "data": [float(revenue - tax - returns), float(cogs), float(expenses), float(net)]}]},
        "cols": [("line", "Line", "text"), ("amount", "Amount", "money")],
        "rows": rows,
        "notes": "Cost of goods uses each product's current cost price, so this is an estimate.",
    }


# ------------------------------------------------------------------------------------------------------- 6. customers
def customers_report(request, store, date_from, date_to):
    qs = _sales(store, date_from, date_to).filter(customer__isnull=False)
    paid = {r["customer_id"]: _d(r["p"]) for r in qs.values("customer_id").annotate(p=Sum("payments__amount"))}
    rows = []
    for r in qs.values("customer_id", "customer__name", "customer__phone").annotate(n=Count("id", distinct=True), total=Sum("total_amount"), last=Max("created_at")).order_by("-total"):
        pd = paid.get(r["customer_id"], ZERO)
        rows.append({"customer": r["customer__name"], "phone": r["customer__phone"], "n": r["n"], "total": _d(r["total"]), "paid": pd, "due": _d(r["total"]) - pd,
                     "last": r["last"].date() if r["last"] else None})
    walk = _d(_sales(store, date_from, date_to).filter(customer__isnull=True).aggregate(t=Sum("total_amount"))["t"])
    top = rows[:10]
    return {
        "kpis": [("Customers who bought", "int", len(rows)), ("From customers", "money", sum((r["total"] for r in rows), ZERO)), ("Walk-in sales", "money", walk),
                 ("Customers owing", "int", sum(1 for r in rows if r["due"] > 0)), ("Total owed", "money", sum((r["due"] for r in rows if r["due"] > 0), ZERO))],
        "chart": {"type": "bar", "labels": [r["customer"][:20] for r in top], "series": [{"label": "Bought", "data": [float(r["total"]) for r in top]}], "horizontal": True},
        "cols": [("customer", "Customer", "text"), ("phone", "Phone", "text"), ("n", "Invoices", "num"), ("total", "Bought", "money"), ("paid", "Paid", "money"), ("due", "Owes", "money"),
                 ("last", "Last purchase", "date")],
        "rows": rows,
    }


REGISTRY = OrderedDict([
    ("sales", {"group": "Sales", "perm": "sales.view_sale", "title": "Sales", "icon": "receipt", "text": "Day-by-day sales, discounts, tax and what is still unpaid.", "fn": sales_report}),
    ("products", {"group": "Sales", "perm": "sales.view_sale", "title": "Products", "icon": "cube", "text": "Best sellers with units, revenue and estimated profit.", "fn": products_report}),
    ("payments", {"group": "Sales", "perm": "payments.view_payment", "title": "Payments", "icon": "credit-card", "text": "Money collected and refunded, by payment method.", "fn": payments_report}),
    ("stock-value", {"group": "Inventory", "perm": "inventory.view_stockitem", "title": "Stock value", "icon": "archive", "text": "What your stock on hand is worth at cost and at selling price.", "fn": stock_value_report}),
    ("profit-loss", {"group": "Finance", "perm": "expenses.view_expense", "title": "Profit & loss", "icon": "chart", "text": "Sales less cost of goods and expenses for the period.", "fn": profit_loss_report}),
    ("customers", {"group": "People", "perm": "customers.view_customer", "title": "Customers", "icon": "users", "text": "Who buys most and who still owes you.", "fn": customers_report}),
])


# --- the extra reports live in pack_more.py (imported last so it can reuse the helpers above) ---
from . import pack_more as _m  # noqa: E402

GROUP_ORDER = ["Sales", "Purchases", "Inventory", "Finance", "People", "Registers"]


def _add(slug, group, perm, title, icon, text, fn):
    REGISTRY[slug] = {"group": group, "perm": perm, "title": title, "icon": icon, "text": text, "fn": fn}


_add("monthly-sales", "Sales", "sales.view_sale", "Monthly sales", "chart", "Sales, discounts and tax month by month.", _m.monthly_sales_report)
_add("categories", "Sales", "sales.view_sale", "Categories", "tag", "Which product categories sell best, with estimated profit.", _m.categories_report)
_add("purchases", "Purchases", "suppliers.view_purchaseorder", "Purchases", "truck", "Every purchase order with what was returned, paid and is still owed.", _m.purchases_report)
_add("daily-purchases", "Purchases", "suppliers.view_purchaseorder", "Daily purchases", "truck", "Purchases totalled day by day.", _m.daily_purchases_report)
_add("monthly-purchases", "Purchases", "suppliers.view_purchaseorder", "Monthly purchases", "truck", "Purchases totalled month by month.", _m.monthly_purchases_report)
_add("suppliers", "Purchases", "suppliers.view_supplier", "Suppliers", "users", "What you bought from each supplier, and what you still owe.", _m.suppliers_report)
_add("stock-alerts", "Inventory", "inventory.view_stockitem", "Low stock", "archive", "Products at or below their low-stock warning level.", _m.stock_alerts_report)
_add("expenses", "Finance", "expenses.view_expense", "Expenses", "cash", "Every expense, by category and payment method.", _m.expenses_report)
_add("registers", "Registers", "register.view_register", "Register shifts", "cash", "Each open-to-close shift: sales, cash expected, cash counted and the difference.", _m.registers_report)
_add("billers", "People", "sales.view_sale", "Billers", "user", "Sales, discounts and average invoice for each biller.", _m.billers_report)
_add("staff", "People", "accounts.view_user", "Staff", "users", "Per-staff sales, shifts opened and expenses entered.", _m.staff_report)


def grouped(user):
    """[(group, [{slug, title, icon, text}])] of the reports this user may open, in menu order."""
    out = OrderedDict((g, []) for g in GROUP_ORDER)
    for slug, v in REGISTRY.items():
        if user.has_perm(v["perm"]):
            out.setdefault(v["group"], []).append({"slug": slug, "title": v["title"], "icon": v["icon"], "text": v["text"]})
    return [(g, items) for g, items in out.items() if items]

