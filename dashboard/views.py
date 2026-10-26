import json
from datetime import date, timedelta

from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import DecimalField, F, Sum
from django.db.models.functions import TruncDate, TruncMonth
from django.shortcuts import render
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from core.ui_nav import plan_allows
from expenses.models import Expense
from payments.models import Payment
from register.views import get_active_session
from sales.models import Sale, SaleItem
from sales.stats import COUNTED, returned_items, returns_total, unpaid_total
from suppliers.models import PurchaseOrderItem


def _month_bounds(d):
    """(start-of-month, start-of-next-month) for the month containing d —
    a half-open [start, end) range that's simple to filter a date field
    against and never double-counts the boundary day."""
    start = d.replace(day=1)
    end = date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)
    return start, end


def _pct_change(this, last):
    """Month-over-month % change, formatted for a badge. None when there's
    no prior-month baseline to compare against (badge is hidden in that
    case) rather than showing a misleading +/-100%."""
    if last:
        return round((float(this) - float(last)) / float(last) * 100, 1)
    return None


@login_required
def home_view(request):
    store = request.store
    today = timezone.localdate()

    # --- 2x2 bold stat cards: this month vs last month, real aggregates
    # only (Total Sales / Total Purchases / Total Due / Total Expenses). ---
    this_start, this_end = _month_bounds(today)
    last_month_day = this_start - timedelta(days=1)
    last_start, last_end = _month_bounds(last_month_day)

    def sales_total(start, end):
        gross = Sale.objects.filter(
            store=store, status__in=COUNTED, created_at__date__gte=start, created_at__date__lt=end,
        ).aggregate(t=Sum("total_amount"))["t"] or 0
        return gross - returns_total(store, start, end - timedelta(days=1))["value"]      # minus goods returned in that period

    def purchases_total(start, end):
        return PurchaseOrderItem.objects.filter(
            purchase_order__warehouse__store=store,
            purchase_order__order_date__gte=start, purchase_order__order_date__lt=end,
        ).aggregate(t=Sum(F("quantity") * F("cost_price"), output_field=DecimalField()))["t"] or 0

    def due_total(start, end):
        month_sales = Sale.objects.filter(
            store=store, status__in=COUNTED, created_at__date__gte=start, created_at__date__lt=end,
        )
        paid = Payment.objects.filter(sale__in=month_sales).exclude(status="voided").aggregate(t=Sum("amount"))["t"] or 0
        return unpaid_total(month_sales, paid)

    def expenses_total(start, end):
        return Expense.objects.filter(
            store=store, expense_date__gte=start, expense_date__lt=end,
        ).aggregate(t=Sum("amount"))["t"] or 0

    stat_sales_this, stat_sales_last = sales_total(this_start, this_end), sales_total(last_start, last_end)
    stat_purchases_this, stat_purchases_last = purchases_total(this_start, this_end), purchases_total(last_start, last_end)
    stat_due_this, stat_due_last = due_total(this_start, this_end), due_total(last_start, last_end)
    stat_expenses_this, stat_expenses_last = expenses_total(this_start, this_end), expenses_total(last_start, last_end)

    # --- Overview chart: Sales (bar) vs Purchases (line) for the trailing
    # 12 months, with each month's tax collected available for the tooltip. ---
    month_starts = []
    y, m = today.year, today.month
    for i in range(11, -1, -1):
        mm, yy = m - i, y
        while mm <= 0:
            mm += 12
            yy -= 1
        month_starts.append(date(yy, mm, 1))
    chart_start = month_starts[0]

    sales_by_month = {
        row["month"]: row
        for row in (
            Sale.objects.filter(store=store, status__in=COUNTED, created_at__date__gte=chart_start)
            .annotate(month=TruncMonth("created_at"))
            .values("month")
            .annotate(total=Sum("total_amount"), tax=Sum("tax_amount"))
            .order_by("month")
        )
    }
    for r in returned_items(store, chart_start):                       # goods returned in a month come off that month
        key = r["day"].replace(day=1)
        row = next((v for k, v in sales_by_month.items() if k is not None and (k.date() if hasattr(k, "date") else k) == key), None)
        if row is None:
            row = sales_by_month.setdefault(key, {"total": 0, "tax": 0})
        row["total"] = (row["total"] or 0) - r["value"]
        row["tax"] = (row["tax"] or 0) - r["tax"]
    purchases_by_month = {
        row["month"]: row["total"]
        for row in (
            PurchaseOrderItem.objects.filter(
                purchase_order__warehouse__store=store,
                purchase_order__order_date__gte=chart_start,
            )
            .annotate(month=TruncMonth("purchase_order__order_date"))
            .values("month")
            .annotate(total=Sum(F("quantity") * F("cost_price"), output_field=DecimalField()))
            .order_by("month")
        )
    }

    chart_labels = [d.strftime("%b %y") for d in month_starts]
    chart_sales = [float((sales_by_month.get(d) or {}).get("total") or 0) for d in month_starts]
    chart_tax = [float((sales_by_month.get(d) or {}).get("tax") or 0) for d in month_starts]
    chart_purchases = [float(purchases_by_month.get(d) or 0) for d in month_starts]

    # --- Payment status donut: this month's completed sales, using the
    # same Due/Partial/Paid classification the POS payment flow shows live. ---
    month_sales = Sale.objects.filter(
        store=store, status__in=COUNTED, created_at__date__gte=today.replace(day=1),
    ).prefetch_related("payments", "refunds", "items__return_items")
    status_paid = status_partial = status_due = 0
    for s in month_sales:
        st = s.payment_status
        if st == "paid":
            status_paid += 1
        elif st == "partial":
            status_partial += 1
        else:
            status_due += 1

    # --- Everything below only feeds the extra dashboard widgets (read-only aggregates). ---
    completed = Sale.objects.filter(store=store, status__in=COUNTED)

    today_total = (completed.filter(created_at__date=today).aggregate(t=Sum("total_amount"))["t"] or 0) - returns_total(store, today, today)["value"]
    today_orders = completed.filter(created_at__date=today).count()
    yesterday_total = (completed.filter(created_at__date=today - timedelta(days=1)).aggregate(t=Sum("total_amount"))["t"] or 0) - returns_total(
        store, today - timedelta(days=1), today - timedelta(days=1))["value"]

    # payment methods used this month
    from payments.models import PaymentMethod
    labels = PaymentMethod.label_map(store) if store else {}
    pay_rows = (
        Payment.objects.filter(sale__store=store, sale__status__in=COUNTED, paid_at__date__gte=this_start)
        .exclude(status="voided").values("method").annotate(t=Sum("amount")).order_by("-t")
    )
    pay_methods = [{"label": labels.get(r["method"], str(r["method"]).title()), "total": r["t"] or 0} for r in pay_rows][:4]

    # last 30 days, one bar per day (days without sales show as 0)
    d30_start = today - timedelta(days=29)
    by_day = {
        r["day"]: r["t"] or 0
        for r in completed.filter(created_at__date__gte=d30_start).annotate(day=TruncDate("created_at"))
        .values("day").annotate(t=Sum("total_amount"))
    }
    days30 = [d30_start + timedelta(days=i) for i in range(30)]
    d30_labels = [d.strftime("%d %b") for d in days30]
    d30_values = [float(by_day.get(d, 0)) for d in days30]

    # top products (30 days)
    top_products = list(
        SaleItem.objects.filter(sale__store=store, sale__status__in=COUNTED, sale__created_at__date__gte=d30_start)
        .values("variant__product__name").annotate(qty=Sum("quantity"), revenue=Sum(F("quantity") * F("unit_price"), output_field=DecimalField()))
        .order_by("-revenue")[:6]
    )
    top_max = max([float(r["revenue"] or 0) for r in top_products] + [0]) or 1
    for r in top_products:
        r["pct"] = max(4, round(float(r["revenue"] or 0) / top_max * 100))

    # low stock: products with an alert level whose on-hand quantity has fallen to it
    from inventory.models import StockItem
    low_qs = StockItem.objects.filter(
        warehouse__store=store, variant__product__item_type="standard", variant__product__alert_quantity__gt=0, quantity__lte=F("variant__product__alert_quantity"),
    )
    low_count = low_qs.count()
    low_stock = list(low_qs.select_related("variant__product", "warehouse").order_by("quantity")[:6])

    # quick actions the signed-in user is allowed to use
    def _url(name):
        try:
            return reverse(name)
        except NoReverseMatch:
            return None
    quick = []
    for label, name, perm, icon in (
        ("New sale", "dashboard:pos", None, "register"), ("Add product", "catalog:product_add", "catalog.add_product", "cube"),
        ("Add customer", "customers:customer_add", "customers.add_customer", "users"), ("New purchase", "suppliers:purchase_add", "suppliers.add_purchaseorder", "cart"),
        ("Stock adjust", "inventory:quantity_adjustment", "inventory.add_stockadjustment", "archive"), ("Reports", "reports:dashboard", None, "chart"),
    ):
        url = _url(name)
        if url and (perm is None or request.user.has_perm(perm)) and plan_allows(request, "sales" if name == "dashboard:pos" else name.split(":")[0]):
            quick.append({"label": label, "url": url, "icon": icon})

    from customers.models import Customer
    perm = request.user.has_perm
    context = {
        "can_sales": perm("sales.view_sale") and plan_allows(request, "sales"),
        "can_purchases": perm("suppliers.view_purchaseorder") and plan_allows(request, "suppliers"),
        "can_expenses": perm("expenses.view_expense") and plan_allows(request, "expenses"),
        "can_stock": perm("inventory.view_stockitem") and plan_allows(request, "inventory"),
        "today_total": today_total, "today_orders": today_orders,
        "today_change": _pct_change(today_total, yesterday_total),
        "pay_methods": pay_methods,
        "d30_labels_json": json.dumps(d30_labels), "d30_values_json": json.dumps(d30_values), "d30_total": sum(d30_values),
        "top_products": top_products, "low_stock": low_stock, "low_count": low_count, "quick_actions": quick,
        "customers_count": Customer.objects.filter(store=store).count() if store else 0,
        "recent_sales": Sale.objects.filter(store=store).select_related("biller", "customer").order_by("-created_at")[:8],
        "active_session": get_active_session(request),
        "chart_labels_json": json.dumps(chart_labels),
        "chart_sales_json": json.dumps(chart_sales),
        "chart_tax_json": json.dumps(chart_tax),
        "chart_purchases_json": json.dumps(chart_purchases),
        "status_paid": status_paid,
        "status_partial": status_partial,
        "status_due": status_due,
        "stat_sales_total": stat_sales_this,
        "stat_sales_change": _pct_change(stat_sales_this, stat_sales_last),
        "stat_purchases_total": stat_purchases_this,
        "stat_purchases_change": _pct_change(stat_purchases_this, stat_purchases_last),
        "stat_due_total": stat_due_this,
        "stat_due_change": _pct_change(stat_due_this, stat_due_last),
        "stat_expenses_total": stat_expenses_this,
        "stat_expenses_change": _pct_change(stat_expenses_this, stat_expenses_last),
    }
    return render(request, "dashboard/home.html", context)


@login_required
@permission_required("sales.add_sale", raise_exception=True)
def pos_view(request):
    """The billing screen. ActiveRegisterMiddleware guarantees an open register session exists by the time we get here.
    The cart itself is loaded by the page from the server (sales.pos_cart), so a refresh never loses it."""
    from payments.models import PaymentMethod
    store = request.store
    session = get_active_session(request)

    categories = store.categories.filter(is_active=True, parent__isnull=True).order_by("name")
    billers = store.staff.filter(is_biller=True, is_active=True)
    customer_groups = store.customer_groups.all().order_by("name")
    methods = [{"code": m.code, "name": m.name, "requires_reference": m.requires_reference, "is_cash": m.is_cash}
               for m in PaymentMethod.active_for(store)]
    default_method = session.register.default_payment_method
    if default_method not in {m["code"] for m in methods}:
        default_method = methods[0]["code"] if methods else "cash"

    exchange = None
    ref = (request.GET.get("exchange_return") or "").strip()[:30]
    if ref:                                                  # just came back from an exchange return: remind the cashier what was taken back
        from sales.models import SaleReturn
        from sales.returns import refund_value
        ret = SaleReturn.objects.filter(reference=ref, sale__store=store).select_related("sale").prefetch_related("items__sale_item").first()
        if ret is not None:
            exchange = {"return": ret.reference, "sale": ret.sale.reference,
                        "refund": str(sum((refund_value(i.sale_item, i.quantity) for i in ret.items.all()), 0))}
    context = {
        "exchange": exchange,
        "session": session,
        "categories": categories,
        "billers": billers,
        "customer_groups": customer_groups,
        "expense_categories": store.expense_categories.order_by("name") if request.user.has_perm("expenses.add_expense") else [],
        "payment_methods": methods,
        "default_method": default_method,
        "quick_cash": [10, 20, 50, 100, 500, 1000, 5000],
        "shortcuts": [
            ("F2 / Ctrl+K", "Focus product search / scan"), ("F4", "Choose customer"), ("F6", "Discount"), ("F7", "Hold sale"),
            ("F8", "Payment"), ("F9", "Held sales / resume"), ("Ctrl+Enter", "Complete the sale (in the payment window)"),
            ("Ctrl+Shift+X", "Clear cart"), ("Esc", "Close the open window / clear search"), ("?", "Show this help"),
        ],
    }
    return render(request, "dashboard/pos.html", context)
