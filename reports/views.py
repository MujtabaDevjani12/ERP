"""Reports app has no models of its own — everything here is aggregated
on the fly from Sales / Inventory / Expenses / Suppliers, plus the CSV
product import/export feature."""
import csv
import io
import json
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import F, Sum
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from catalog.models import Product, ProductVariant, Unit
from expenses.models import Expense
from inventory.models import StockItem
from sales.models import Sale, SaleItem
from suppliers.models import PurchaseOrderItem


def _date_range(request):
    """Reports default to the trailing 30 days; ?from=YYYY-MM-DD&to=YYYY-MM-DD overrides."""
    today = timezone.localdate()
    try:
        date_from = datetime.strptime(request.GET.get("from", ""), "%Y-%m-%d").date()
    except ValueError:
        date_from = today - timedelta(days=29)
    try:
        date_to = datetime.strptime(request.GET.get("to", ""), "%Y-%m-%d").date()
    except ValueError:
        date_to = today
    return date_from, date_to


@login_required
@permission_required("sales.view_sale", raise_exception=True)
def dashboard(request):
    store = request.store
    date_from, date_to = _date_range(request)

    sales_qs = Sale.objects.filter(
        store=store, status="completed",
        created_at__date__gte=date_from, created_at__date__lte=date_to,
    )

    totals = sales_qs.aggregate(
        revenue=Sum("total_amount"), discount=Sum("discount_amount"), tax=Sum("tax_amount"),
    )
    total_revenue = totals["revenue"] or Decimal("0")
    total_discount = totals["discount"] or Decimal("0")
    total_tax = totals["tax"] or Decimal("0")
    sale_count = sales_qs.count()
    total_due = sum((s.due_amount for s in sales_qs), start=Decimal("0"))

    by_day = (
        sales_qs.annotate(day=TruncDate("created_at"))
        .values("day").annotate(revenue=Sum("total_amount")).order_by("day")
    )
    chart_labels = [row["day"].strftime("%d %b") for row in by_day]
    chart_values = [float(row["revenue"] or 0) for row in by_day]

    top_products = (
        SaleItem.objects.filter(sale__in=sales_qs)
        .values("variant__product__name")
        .annotate(qty=Sum("quantity"), revenue=Sum(F("quantity") * F("unit_price")))
        .order_by("-revenue")[:8]
    )

    expenses_total = Expense.objects.filter(
        store=store, expense_date__gte=date_from, expense_date__lte=date_to,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")

    purchases_total = PurchaseOrderItem.objects.filter(
        purchase_order__warehouse__store=store, purchase_order__status="received",
        purchase_order__order_date__gte=date_from, purchase_order__order_date__lte=date_to,
    ).aggregate(total=Sum(F("quantity") * F("cost_price")))["total"] or Decimal("0")

    low_stock = (
        StockItem.objects.filter(warehouse__store=store, variant__product__item_type="standard", quantity__lte=F("variant__product__alert_quantity"))
        .select_related("variant__product", "warehouse")
        .order_by("quantity")[:15]
    )

    payment_methods = (
        sales_qs.values("payments__method").annotate(total=Sum("payments__amount")).order_by("-total")
    )

    context = {
        "date_from": date_from, "date_to": date_to,
        "total_revenue": total_revenue, "total_discount": total_discount, "total_tax": total_tax,
        "sale_count": sale_count, "total_due": total_due,
        "expenses_total": expenses_total, "purchases_total": purchases_total,
        "net_profit_estimate": total_revenue - expenses_total,
        "top_products": top_products, "low_stock": low_stock, "payment_methods": payment_methods,
        "chart_labels_json": json.dumps(chart_labels, cls=DjangoJSONEncoder),
        "chart_values_json": json.dumps(chart_values, cls=DjangoJSONEncoder),
    }
    return render(request, "reports/dashboard.html", context)


@login_required
@permission_required("sales.view_sale", raise_exception=True)
def export_sales_csv(request):
    store = request.store
    date_from, date_to = _date_range(request)
    sales_qs = Sale.objects.filter(
        store=store, created_at__date__gte=date_from, created_at__date__lte=date_to,
    ).select_related("customer", "biller").order_by("created_at")

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="sales_{date_from}_{date_to}.csv"'
    writer = csv.writer(response)
    writer.writerow(["Reference", "Date", "Customer", "Biller", "Status", "Payment status", "Subtotal", "Discount", "Tax", "Total", "Paid", "Due"])
    for s in sales_qs:
        writer.writerow([
            s.reference, s.created_at.strftime("%Y-%m-%d %H:%M"), s.customer.name if s.customer else "Walk-in",
            s.biller.get_full_name() or s.biller.username, s.get_status_display(), s.payment_status,
            s.subtotal, s.discount_amount, s.tax_amount, s.total_amount, s.paid_amount, s.due_amount,
        ])
    return response


@login_required
@permission_required("catalog.view_product", raise_exception=True)
def export_products_csv(request):
    store = request.store
    variants = (
        ProductVariant.objects.filter(product__store=store)
        .select_related("product__category", "product__brand", "product__unit")
        .order_by("product__name", "name")
    )

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="products.csv"'
    writer = csv.writer(response)
    writer.writerow(["SKU", "Product", "Variant", "Category", "Brand", "Unit", "Barcode", "Cost price", "Selling price", "Active"])
    for v in variants:
        writer.writerow([
            v.sku, v.product.name, v.name, v.product.category.name if v.product.category else "",
            v.product.brand.name if v.product.brand else "", v.product.unit.short_name,
            v.barcode, v.cost_price, v.selling_price, "Yes" if v.is_active else "No",
        ])
    return response


@login_required
@permission_required("catalog.add_product", raise_exception=True)
def import_products_csv(request):
    """Simple product importer — matches export_products_csv's columns.
    An existing SKU updates prices/barcode; an unseen SKU creates a new
    single-variant product (default unit = store's first Unit)."""
    if request.method != "POST":
        return render(request, "reports/import_products.html")

    upload = request.FILES.get("file")
    if not upload:
        messages.error(request, "No file was selected.")
        return redirect("reports:import_products_csv")

    store = request.store
    default_unit = Unit.objects.filter(store=store).first()
    if default_unit is None:
        messages.error(request, "Pehle kam az kam ek Unit bana lein (Settings → Units).")
        return redirect("reports:import_products_csv")

    decoded = io.TextIOWrapper(upload.file, encoding="utf-8-sig")
    reader = csv.DictReader(decoded)
    created, updated, skipped = 0, 0, 0

    for row in reader:
        sku = (row.get("SKU") or row.get("sku") or "").strip()
        name = (row.get("Product") or row.get("name") or "").strip()
        if not sku or not name:
            skipped += 1
            continue
        try:
            cost_price = Decimal(row.get("Cost price") or row.get("cost_price") or "0")
            selling_price = Decimal(row.get("Selling price") or row.get("selling_price") or "0")
        except InvalidOperation:
            skipped += 1
            continue

        variant = ProductVariant.objects.filter(sku=sku, product__store=store).first()
        if variant:
            variant.cost_price = cost_price
            variant.selling_price = selling_price
            variant.save(update_fields=["cost_price", "selling_price", "updated_at"])
            updated += 1
        else:
            product = Product.objects.create(
                store=store, name=name, sku=sku, unit=default_unit,
            )
            # Product.save() already auto-creates a 'Default' variant via
            # the ensure_default_variant signal — fill in its prices rather
            # than creating a second one (which would collide on SKU).
            default_variant = product.variants.first()
            default_variant.cost_price = cost_price
            default_variant.selling_price = selling_price
            default_variant.save(update_fields=["cost_price", "selling_price", "updated_at"])
            created += 1

    messages.success(request, f"Import complete — {created} new products, {updated} updated, {skipped} skipped.")
    return redirect("catalog:product_list")


# ---------------------------------------------------------------------------------------------------- report pack
def _pack_context(request, slug):
    from django.core.exceptions import PermissionDenied
    from django.http import Http404
    from . import pack
    entry = pack.REGISTRY.get(slug)
    if entry is None:
        raise Http404("No such report")
    if not request.user.has_perm(entry["perm"]):
        raise PermissionDenied
    date_from, date_to = _date_range(request)
    if date_from > date_to:
        date_from, date_to = date_to, date_from
    data = entry["fn"](request, request.store, date_from, date_to)
    return entry, data, date_from, date_to


def _plain(value, kind):
    if value is None:
        return ""
    if kind == "date":
        return value.strftime("%Y-%m-%d")
    return value


@login_required
def pack_index(request):
    from . import pack
    groups = pack.grouped(request.user)
    if not groups:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    return render(request, "reports/pack_index.html", {"groups": groups})


@login_required
def pack_report(request, slug):
    from django.core.paginator import Paginator
    from . import pack
    entry, data, date_from, date_to = _pack_context(request, slug)
    page_obj = Paginator(data["rows"], 50).get_page(request.GET.get("page"))
    chart = data.get("chart")
    for f in data.get("extra_filters", []):
        f["value"] = request.GET.get(f["name"], "")
    qs = request.GET.copy()
    qs.pop("page", None)
    return render(request, "reports/pack_report.html", {
        "slug": slug, "title": entry["title"], "data": data, "page_obj": page_obj, "date_from": date_from, "date_to": date_to,
        "chart_json": json.dumps(chart, cls=DjangoJSONEncoder) if chart and chart["labels"] else "",
        "groups": pack.grouped(request.user), "group": entry["group"],
        "csv_query": qs.urlencode(),
        "rows_view": [[(r.get(k), kind) for k, _l, kind in data["cols"]] for r in page_obj.object_list],
        "kpis": [(label, _kpi_text(kind, value, request.store)) for label, kind, value in data["kpis"]],
    })


@login_required
def pack_report_csv(request, slug):
    entry, data, date_from, date_to = _pack_context(request, slug)
    response = HttpResponse(content_type="text/csv")
    suffix = "" if data.get("no_dates") else f"_{date_from}_{date_to}"
    response["Content-Disposition"] = f'attachment; filename="{slug}{suffix}.csv"'
    response.write("﻿")                              # lets Excel read non-English names correctly
    writer = csv.writer(response)
    writer.writerow([label for _k, label, _kind in data["cols"]])
    for row in data["rows"]:
        writer.writerow([_csv_safe(_plain(row.get(k), kind)) for k, _l, kind in data["cols"]])
    return response


@login_required
def pack_report_export(request, slug, fmt):
    """Excel (.xlsx) or PDF download of one report, with the same dates and filters as the page."""
    from django.http import Http404
    from . import xlsx as xl
    from .pdf import render_report_pdf, safe_filename
    if fmt not in ("xlsx", "pdf"):
        raise Http404("Unknown format")
    entry, data, date_from, date_to = _pack_context(request, slug)
    store = request.store
    suffix = "" if data.get("no_dates") else f"{date_from}_{date_to}"
    name = safe_filename(slug, suffix)
    try:
        if fmt == "pdf":
            pdf = render_report_pdf(entry["title"], data, store, date_from, date_to, request.user)
            resp = HttpResponse(pdf, content_type="application/pdf")
            resp["Content-Disposition"] = f'attachment; filename="{name}.pdf"'
            resp["Cache-Control"] = "no-store"
            return resp
        cols = data["cols"]
        money_cols = [i for i, (_k, _l, kind) in enumerate(cols) if kind == "money"]
        notes = [] if data.get("no_dates") else [f"{date_from:%d %b %Y} to {date_to:%d %b %Y}"]
        if data.get("notes"):
            notes.append(data["notes"])
        sheet = {"title": entry["title"], "heading": f"{store.name} - {entry['title']}", "notes": notes,
                 "summary": [(label, value if kind in ("money", "num", "int") else str(value)) for label, kind, value in data["kpis"]],
                 "headers": [label for _k, label, _kind in cols], "money_cols": money_cols,
                 "rows": [[r.get(k) for k, _l, _kind in cols] for r in data["rows"]]}
        return xl.xlsx_response(name, [sheet])
    except xl.ExcelError as exc:
        messages.error(request, str(exc))
        return redirect(f"{reverse('reports:pack_report', args=[slug])}?{request.GET.urlencode()}")


def _csv_safe(value):
    """A cell that starts with = + - @ would be run as a formula by Excel: prefix it so it stays text."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _kpi_text(kind, value, store):
    if kind == "money":
        return f"{getattr(store, 'currency_symbol', '') or ''}{value:,.2f}"
    if kind == "num":
        return f"{value:,.3f}".rstrip("0").rstrip(".")
    if kind == "int":
        return f"{int(value):,}"
    return str(value)
