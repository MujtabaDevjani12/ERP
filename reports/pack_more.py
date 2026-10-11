"""More reports for the report pack (see pack.py for the function contract): monthly sales, categories, purchases (list / daily / monthly),
expenses, register shifts, suppliers, billers, staff and low-stock alerts.

Everything is scoped to the current store. Purchases count every order that is not cancelled; sales count completed sales only
(the same rule as the six original reports)."""
from collections import OrderedDict, defaultdict
from decimal import Decimal

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Max, Q, Sum
from django.db.models.functions import TruncDate, TruncMonth

from .pack import MONEY, ZERO, _d, _sales

PURCHASE_STATUSES = [("draft", "Draft"), ("ordered", "Ordered"), ("received", "Received"), ("cancelled", "Cancelled")]


def _month(dt):
    return dt.strftime("%b %Y") if dt else ""


def _opts(pairs):
    return [(k, v) for k, v in pairs]


def _int_arg(request, name):
    v = request.GET.get(name)
    return int(v) if v and v.isdigit() else None


# --------------------------------------------------------------------------------------------------------- sales by month
def monthly_sales_report(request, store, date_from, date_to):
    qs = _sales(store, date_from, date_to)
    per = list(qs.annotate(m=TruncMonth("created_at")).values("m").annotate(
        n=Count("id"), subtotal=Sum("subtotal"), discount=Sum("discount_amount"), tax=Sum("tax_amount"), total=Sum("total_amount")).order_by("-m"))
    paid = {r["m"]: _d(r["p"]) for r in qs.annotate(m=TruncMonth("created_at")).values("m").annotate(p=Sum("payments__amount"))}
    rows = [{"month": _month(r["m"]), "n": r["n"], "subtotal": _d(r["subtotal"]), "discount": _d(r["discount"]), "tax": _d(r["tax"]), "total": _d(r["total"]),
             "paid": paid.get(r["m"], ZERO), "due": _d(r["total"]) - paid.get(r["m"], ZERO)} for r in per]
    total, count = sum((r["total"] for r in rows), ZERO), sum(r["n"] for r in rows)
    chron = list(reversed(rows))
    return {
        "kpis": [("Net sales", "money", total), ("Invoices", "int", count), ("Average invoice", "money", (total / count) if count else ZERO),
                 ("Discounts given", "money", sum((r["discount"] for r in rows), ZERO)), ("Tax collected", "money", sum((r["tax"] for r in rows), ZERO)),
                 ("Still unpaid", "money", sum((r["due"] for r in rows), ZERO))],
        "chart": {"type": "bar", "labels": [r["month"] for r in chron], "series": [{"label": "Sales", "data": [float(r["total"]) for r in chron]}]},
        "cols": [("month", "Month", "text"), ("n", "Invoices", "num"), ("subtotal", "Subtotal", "money"), ("discount", "Discount", "money"), ("tax", "Tax", "money"),
                 ("total", "Total", "money"), ("paid", "Paid", "money"), ("due", "Due", "money")],
        "rows": rows,
    }


# ----------------------------------------------------------------------------------------------------------- categories
def categories_report(request, store, date_from, date_to):
    from sales.models import SaleItem
    qs = SaleItem.objects.filter(sale__in=_sales(store, date_from, date_to))
    rows = []
    for r in qs.values("variant__product__category__name").annotate(
            qty=Sum("quantity"), revenue=Sum(ExpressionWrapper(F("quantity") * F("unit_price") - F("discount_amount"), output_field=MONEY)),
            cost=Sum(ExpressionWrapper(F("quantity") * F("variant__cost_price"), output_field=MONEY))).order_by("-revenue"):
        rev, cost = _d(r["revenue"]), _d(r["cost"])
        rows.append({"category": r["variant__product__category__name"] or "Uncategorised", "qty": _d(r["qty"]), "revenue": rev, "cost": cost, "profit": rev - cost})
    top = rows[:10]
    return {
        "kpis": [("Categories sold", "int", len(rows)), ("Units sold", "num", sum((r["qty"] for r in rows), ZERO)), ("Revenue", "money", sum((r["revenue"] for r in rows), ZERO)),
                 ("Estimated profit", "money", sum((r["profit"] for r in rows), ZERO))],
        "chart": {"type": "doughnut", "labels": [r["category"] for r in top], "series": [{"label": "Revenue", "data": [float(max(r["revenue"], ZERO)) for r in top]}]},
        "cols": [("category", "Category", "text"), ("qty", "Units sold", "num"), ("revenue", "Revenue", "money"), ("cost", "Est. cost", "money"), ("profit", "Est. profit", "money")],
        "rows": rows,
        "notes": "Cost uses each product's current cost price, so profit is an estimate.",
    }


# ----------------------------------------------------------------------------------------------------------- purchases
def _purchase_qs(store, date_from, date_to):
    from suppliers.models import PurchaseOrder
    return PurchaseOrder.objects.filter(warehouse__store=store, order_date__gte=date_from, order_date__lte=date_to).exclude(status="cancelled")


def _purchase_rows(qs):
    """One dict per purchase order with total / returned / paid / due, computed with grouped queries (no per-order queries)."""
    from suppliers.models import PurchaseOrderItem, PurchasePayment, PurchaseReturnItem
    qs = qs.select_related("supplier", "warehouse").order_by("-order_date", "-id")
    orders = list(qs)
    ids = [o.pk for o in orders]
    total = {r["purchase_order_id"]: _d(r["t"]) for r in PurchaseOrderItem.objects.filter(purchase_order_id__in=ids).values("purchase_order_id").annotate(
        t=Sum(ExpressionWrapper(F("quantity") * F("cost_price"), output_field=MONEY)))}
    paid = {r["purchase_order_id"]: _d(r["t"]) for r in PurchasePayment.objects.filter(purchase_order_id__in=ids).values("purchase_order_id").annotate(t=Sum("amount"))}
    back = {r["purchase_return__purchase_order_id"]: _d(r["t"]) for r in PurchaseReturnItem.objects.filter(purchase_return__purchase_order_id__in=ids).values(
        "purchase_return__purchase_order_id").annotate(t=Sum(ExpressionWrapper(F("quantity") * F("cost_price"), output_field=MONEY)))}
    rows = []
    for o in orders:
        t, p, b = total.get(o.pk, ZERO), paid.get(o.pk, ZERO), back.get(o.pk, ZERO)
        rows.append({"order": o, "reference": o.reference, "date": o.order_date, "supplier": o.supplier.name, "warehouse": o.warehouse.name, "status": o.get_status_display(),
                     "total": t, "returned": b, "paid": p, "due": t - b - p})
    return rows


def purchases_report(request, store, date_from, date_to):
    from suppliers.models import Supplier
    from tenants.models import Warehouse
    qs = _purchase_qs(store, date_from, date_to)
    sup, wh, status = _int_arg(request, "supplier"), _int_arg(request, "warehouse"), request.GET.get("status")
    if sup:
        qs = qs.filter(supplier_id=sup)
    if wh:
        qs = qs.filter(warehouse_id=wh)
    if status in dict(PURCHASE_STATUSES):
        qs = qs.filter(status=status)
    rows = _purchase_rows(qs)
    total = sum((r["total"] for r in rows), ZERO)
    return {
        "kpis": [("Purchases", "int", len(rows)), ("Total purchased", "money", total), ("Returned to suppliers", "money", sum((r["returned"] for r in rows), ZERO)),
                 ("Paid", "money", sum((r["paid"] for r in rows), ZERO)), ("Still owed", "money", sum((r["due"] for r in rows), ZERO))],
        "chart": None,
        "cols": [("reference", "Reference", "text"), ("date", "Date", "date"), ("supplier", "Supplier", "text"), ("warehouse", "Warehouse", "text"), ("status", "Status", "text"),
                 ("total", "Total", "money"), ("returned", "Returned", "money"), ("paid", "Paid", "money"), ("due", "Due", "money")],
        "rows": rows,
        "extra_filters": [
            {"name": "supplier", "label": "Supplier", "options": [(s.pk, s.name) for s in Supplier.objects.filter(store=store).order_by("name")]},
            {"name": "warehouse", "label": "Warehouse", "options": [(w.pk, w.name) for w in Warehouse.objects.filter(store=store).order_by("name")]},
            {"name": "status", "label": "Status", "options": [(k, v) for k, v in PURCHASE_STATUSES if k != "cancelled"]},
        ],
        "notes": "Cancelled orders are not counted.",
    }


def _purchases_by(request, store, date_from, date_to, monthly):
    rows_all = _purchase_rows(_purchase_qs(store, date_from, date_to))
    groups = OrderedDict()
    for r in rows_all:
        key = r["date"].replace(day=1) if monthly else r["date"]
        g = groups.setdefault(key, {"n": 0, "total": ZERO, "returned": ZERO, "paid": ZERO, "due": ZERO})
        g["n"] += 1
        for k in ("total", "returned", "paid", "due"):
            g[k] += r[k]
    rows = [{("month" if monthly else "day"): (_month(k) if monthly else k), **v} for k, v in sorted(groups.items(), reverse=True)]
    chron = list(reversed(rows))
    label_key = "month" if monthly else "day"
    total = sum((r["total"] for r in rows), ZERO)
    return {
        "kpis": [("Purchases", "int", sum(r["n"] for r in rows)), ("Total purchased", "money", total), ("Paid", "money", sum((r["paid"] for r in rows), ZERO)),
                 ("Still owed", "money", sum((r["due"] for r in rows), ZERO))],
        "chart": {"type": "bar", "labels": [r[label_key] if monthly else r[label_key].strftime("%d %b") for r in chron],
                  "series": [{"label": "Purchased", "data": [float(r["total"]) for r in chron]}]},
        "cols": [(label_key, "Month" if monthly else "Date", "text" if monthly else "date"), ("n", "Purchases", "num"), ("total", "Total", "money"), ("returned", "Returned", "money"),
                 ("paid", "Paid", "money"), ("due", "Due", "money")],
        "rows": rows,
        "notes": "Cancelled orders are not counted.",
    }


def daily_purchases_report(request, store, date_from, date_to):
    return _purchases_by(request, store, date_from, date_to, monthly=False)


def monthly_purchases_report(request, store, date_from, date_to):
    return _purchases_by(request, store, date_from, date_to, monthly=True)


# ----------------------------------------------------------------------------------------------------------- expenses
def expenses_report(request, store, date_from, date_to):
    from expenses.models import Expense, ExpenseCategory
    from payments.models import METHOD_CHOICES
    qs = Expense.objects.filter(store=store, expense_date__gte=date_from, expense_date__lte=date_to).select_related("category", "created_by")
    cat, method = _int_arg(request, "category"), request.GET.get("method")
    if cat:
        qs = qs.filter(category_id=cat)
    if method in dict(METHOD_CHOICES):
        qs = qs.filter(method=method)
    rows = [{"reference": e.reference, "date": e.expense_date, "category": e.category.name, "paid_to": e.paid_to, "method": e.get_method_display(), "amount": e.amount,
             "note": e.note, "by": (e.created_by.get_full_name() or e.created_by.username) if e.created_by_id else ""} for e in qs.order_by("-expense_date", "-id")]
    by_cat = OrderedDict()
    for r in rows:
        by_cat[r["category"]] = by_cat.get(r["category"], ZERO) + r["amount"]
    total = sum((r["amount"] for r in rows), ZERO)
    days = (date_to - date_from).days + 1
    return {
        "kpis": [("Total expenses", "money", total), ("Entries", "int", len(rows)), ("Categories", "int", len(by_cat)),
                 ("Daily average", "money", (total / days) if days > 0 else ZERO)],
        "chart": {"type": "doughnut", "labels": list(by_cat), "series": [{"label": "Expenses", "data": [float(v) for v in by_cat.values()]}]},
        "cols": [("reference", "Reference", "text"), ("date", "Date", "date"), ("category", "Category", "text"), ("paid_to", "Paid to", "text"), ("method", "Method", "text"),
                 ("amount", "Amount", "money"), ("by", "Entered by", "text"), ("note", "Note", "text")],
        "rows": rows,
        "extra_filters": [
            {"name": "category", "label": "Category", "options": [(c.pk, c.name) for c in ExpenseCategory.objects.filter(store=store).order_by("name")]},
            {"name": "method", "label": "Method", "options": list(METHOD_CHOICES)},
        ],
    }


# ---------------------------------------------------------------------------------------------------------- registers
def registers_report(request, store, date_from, date_to):
    from register.models import Register, RegisterSession
    qs = RegisterSession.objects.filter(register__store=store, opened_at__date__gte=date_from, opened_at__date__lte=date_to).select_related("register", "opened_by", "closed_by")
    reg, status = _int_arg(request, "register"), request.GET.get("status")
    if reg:
        qs = qs.filter(register_id=reg)
    if status in ("open", "closed"):
        qs = qs.filter(status=status)
    sessions = list(qs.order_by("-opened_at")[:1000])
    ids = [s.pk for s in sessions]
    from sales.models import Sale
    sales = {r["register_session_id"]: r for r in Sale.objects.filter(register_session_id__in=ids, status="completed").values("register_session_id").annotate(
        n=Count("id"), t=Sum("total_amount"))}
    rows = []
    for s in sessions:
        sale = sales.get(s.pk, {})
        closed = s.status == "closed"
        rows.append({
            "number": s.number, "register": s.register.name, "cashier": s.opened_by.get_full_name() or s.opened_by.username, "opened": s.opened_at, "closed": s.closed_at,
            "status": s.get_status_display(), "opening": s.opening_cash, "n": sale.get("n", 0), "sales": _d(sale.get("t")),
            "cash_sales": s.cash_sales_total, "expenses": s.cash_expenses_total,
            "expected": s.expected_cash_at_close if closed and s.expected_cash_at_close is not None else s.expected_cash,
            "counted": s.closing_cash_counted, "variance": s.variance})
    closed_rows = [r for r in rows if r["variance"] is not None]
    return {
        "no_dates": False,
        "kpis": [("Shifts", "int", len(rows)), ("Still open", "int", sum(1 for r in rows if r["status"] == "Open")), ("Sales in these shifts", "money", sum((r["sales"] for r in rows), ZERO)),
                 ("Cash sales", "money", sum((r["cash_sales"] for r in rows), ZERO)), ("Net cash difference", "money", sum((r["variance"] for r in closed_rows), ZERO))],
        "chart": None,
        "cols": [("number", "Shift", "text"), ("register", "Register", "text"), ("cashier", "Cashier", "text"), ("opened", "Opened", "datetime"), ("closed", "Closed", "datetime"),
                 ("status", "Status", "text"), ("opening", "Opening cash", "money"), ("n", "Sales", "num"), ("sales", "Sales total", "money"), ("cash_sales", "Cash sales", "money"),
                 ("expenses", "Cash expenses", "money"), ("expected", "Expected cash", "money"), ("counted", "Counted", "money"), ("variance", "Difference", "money")],
        "rows": rows,
        "extra_filters": [
            {"name": "register", "label": "Register", "options": [(r.pk, r.name) for r in Register.objects.filter(store=store).order_by("name")]},
            {"name": "status", "label": "Status", "options": [("open", "Open"), ("closed", "Closed")]},
        ],
        "notes": "Difference = cash counted at closing minus the cash the system expected. Negative means cash is short.",
    }


# ---------------------------------------------------------------------------------------------------------- suppliers
def suppliers_report(request, store, date_from, date_to):
    from suppliers.models import Supplier
    rows_all = _purchase_rows(_purchase_qs(store, date_from, date_to))
    by = defaultdict(lambda: {"n": 0, "total": ZERO, "returned": ZERO, "paid": ZERO, "due": ZERO, "last": None})
    for r in rows_all:
        g = by[r["order"].supplier_id]
        g["n"] += 1
        for k in ("total", "returned", "paid", "due"):
            g[k] += r[k]
        g["last"] = max(g["last"], r["date"]) if g["last"] else r["date"]
    names = {s.pk: s for s in Supplier.objects.filter(pk__in=by.keys())}
    rows = [{"supplier": names[k].name, "phone": names[k].phone, "n": v["n"], "total": v["total"], "returned": v["returned"], "paid": v["paid"], "due": v["due"], "last": v["last"]}
            for k, v in by.items()]
    rows.sort(key=lambda r: -r["total"])
    top = rows[:10]
    return {
        "kpis": [("Suppliers", "int", len(rows)), ("Total purchased", "money", sum((r["total"] for r in rows), ZERO)), ("Paid", "money", sum((r["paid"] for r in rows), ZERO)),
                 ("Still owed", "money", sum((r["due"] for r in rows), ZERO))],
        "chart": {"type": "bar", "labels": [r["supplier"][:20] for r in top], "series": [{"label": "Purchased", "data": [float(r["total"]) for r in top]}], "horizontal": True},
        "cols": [("supplier", "Supplier", "text"), ("phone", "Phone", "text"), ("n", "Purchases", "num"), ("total", "Purchased", "money"), ("returned", "Returned", "money"),
                 ("paid", "Paid", "money"), ("due", "Owed", "money"), ("last", "Last purchase", "date")],
        "rows": rows,
    }


# ------------------------------------------------------------------------------------------------------------ billers
def billers_report(request, store, date_from, date_to):
    qs = _sales(store, date_from, date_to)
    paid = {r["biller_id"]: _d(r["p"]) for r in qs.values("biller_id").annotate(p=Sum("payments__amount"))}
    rows = []
    for r in qs.values("biller_id", "biller__first_name", "biller__last_name", "biller__username").annotate(
            n=Count("id", distinct=True), total=Sum("total_amount"), discount=Sum("discount_amount"), tax=Sum("tax_amount")).order_by("-total"):
        name = f"{r['biller__first_name']} {r['biller__last_name']}".strip() or r["biller__username"]
        t, p = _d(r["total"]), paid.get(r["biller_id"], ZERO)
        rows.append({"biller": name, "n": r["n"], "total": t, "discount": _d(r["discount"]), "tax": _d(r["tax"]), "paid": p, "due": t - p, "average": (t / r["n"]) if r["n"] else ZERO})
    top = rows[:10]
    return {
        "kpis": [("Billers", "int", len(rows)), ("Invoices", "int", sum(r["n"] for r in rows)), ("Sales", "money", sum((r["total"] for r in rows), ZERO))],
        "chart": {"type": "bar", "labels": [r["biller"][:20] for r in top], "series": [{"label": "Sales", "data": [float(r["total"]) for r in top]}]},
        "cols": [("biller", "Biller", "text"), ("n", "Invoices", "num"), ("total", "Sales total", "money"), ("discount", "Discount", "money"), ("tax", "Tax", "money"),
                 ("paid", "Paid", "money"), ("due", "Due", "money"), ("average", "Average invoice", "money")],
        "rows": rows,
    }


# ------------------------------------------------------------------------------------------------------------- staff
def staff_report(request, store, date_from, date_to):
    from expenses.models import Expense
    from register.models import RegisterSession
    from django.contrib.auth import get_user_model
    User = get_user_model()
    qs = _sales(store, date_from, date_to)
    sales = {r["biller_id"]: r for r in qs.values("biller_id").annotate(n=Count("id"), total=Sum("total_amount"), discount=Sum("discount_amount"))}
    shifts = {r["opened_by_id"]: r["n"] for r in RegisterSession.objects.filter(register__store=store, opened_at__date__gte=date_from, opened_at__date__lte=date_to)
              .values("opened_by_id").annotate(n=Count("id"))}
    exps = {r["created_by_id"]: _d(r["t"]) for r in Expense.objects.filter(store=store, expense_date__gte=date_from, expense_date__lte=date_to)
            .values("created_by_id").annotate(t=Sum("amount"))}
    ids = set(sales) | set(shifts) | set(exps)
    ids.discard(None)
    users = {u.pk: u for u in User.objects.filter(pk__in=ids).prefetch_related("groups")}
    rows = []
    for uid, u in users.items():
        s = sales.get(uid, {})
        n, t = s.get("n", 0), _d(s.get("total"))
        rows.append({"staff": u.get_full_name() or u.username, "role": ", ".join(g.name for g in u.groups.all()) or ("Owner" if u.is_superuser else "—"), "n": n, "total": t,
                     "discount": _d(s.get("discount")), "average": (t / n) if n else ZERO, "shifts": shifts.get(uid, 0), "expenses": exps.get(uid, ZERO)})
    rows.sort(key=lambda r: -r["total"])
    top = [r for r in rows if r["total"] > 0][:10]
    return {
        "kpis": [("Active staff", "int", len(rows)), ("Invoices", "int", sum(r["n"] for r in rows)), ("Sales", "money", sum((r["total"] for r in rows), ZERO)),
                 ("Shifts opened", "int", sum(r["shifts"] for r in rows))],
        "chart": {"type": "bar", "labels": [r["staff"][:20] for r in top], "series": [{"label": "Sales", "data": [float(r["total"]) for r in top]}]},
        "cols": [("staff", "Staff", "text"), ("role", "Role", "text"), ("n", "Invoices", "num"), ("total", "Sales total", "money"), ("discount", "Discount given", "money"),
                 ("average", "Average invoice", "money"), ("shifts", "Shifts opened", "num"), ("expenses", "Expenses entered", "money")],
        "rows": rows,
    }


# ------------------------------------------------------------------------------------------------------- low stock
def stock_alerts_report(request, store, date_from, date_to):
    from inventory.models import StockItem
    from tenants.models import Warehouse
    qs = StockItem.objects.filter(warehouse__store=store, variant__is_active=True, variant__product__is_active=True, variant__product__item_type="standard",
                                  quantity__lte=F("variant__product__alert_quantity")).select_related("variant", "variant__product", "variant__product__category", "warehouse")
    wh = _int_arg(request, "warehouse")
    if wh:
        qs = qs.filter(warehouse_id=wh)
    rows = []
    for s in qs.order_by("quantity")[:3000]:
        alert = s.variant.product.alert_quantity
        rows.append({"product": str(s.variant), "sku": s.variant.sku, "category": s.variant.product.category.name if s.variant.product.category_id else "—", "warehouse": s.warehouse.name,
                     "qty": s.quantity, "alert": alert, "short": max(alert - s.quantity, ZERO)})
    return {
        "no_dates": True,
        "kpis": [("Items at or below alert level", "int", len(rows)), ("Out of stock", "int", sum(1 for r in rows if r["qty"] <= 0))],
        "chart": None,
        "cols": [("product", "Product", "text"), ("sku", "SKU", "text"), ("category", "Category", "text"), ("warehouse", "Warehouse", "text"), ("qty", "On hand", "num"),
                 ("alert", "Alert level", "num"), ("short", "Short by", "num")],
        "rows": rows,
        "extra_filters": [{"name": "warehouse", "label": "Warehouse", "options": [(w.pk, w.name) for w in Warehouse.objects.filter(store=store).order_by("name")]}],
        "notes": "Set the alert level on each product (Products > Edit > Low-stock warning).",
    }
