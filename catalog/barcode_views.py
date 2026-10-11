"""Barcode label printing: pick products/variants, choose a sheet or roll layout and what to print on each label."""
from decimal import Decimal

from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse

from configuration.models import Currency

from .models import Category, ProductVariant

MAX_ITEMS = 300


def _int_list(values):
    out = []
    for raw in values:
        for part in str(raw).split(","):
            part = part.strip()
            if part.isdigit():
                out.append(int(part))
    return out


def _qty(value):
    text = f"{Decimal(value or 0):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _item(v):
    return {
        "id": v.id, "product_id": v.product_id, "product": v.product.name,
        "variant": "" if v.name.strip().lower() == "default" else v.name,
        "sku": v.sku, "barcode": v.barcode, "price": f"{v.current_price:.2f}", "symbology": v.product.barcode_symbology,
        "category": v.product.category.name if v.product.category_id else "",
        "brand": v.product.brand.name if v.product.brand_id else "",
        "unit": v.product.unit.short_name, "stock": _qty(getattr(v, "total_stock", 0)),
        "active": v.is_active and v.product.is_active,
    }


def _variants(store):
    return (ProductVariant.objects.filter(product__store=store)
            .select_related("product", "product__category", "product__brand", "product__unit")
            .annotate(total_stock=Coalesce(Sum("stock_items__quantity"), Value(0), output_field=DecimalField())))


@login_required
@permission_required("catalog.view_product", raise_exception=True)
def barcode_items(request):
    """JSON for the picker: `q` searches name/SKU/barcode, `category` filters, `product`/`variant` (ids, repeatable or comma separated)
    return exactly those. Inactive products are included (labels may be printed before a product goes on sale)."""
    qs = _variants(request.store)
    products = _int_list(request.GET.getlist("product"))
    variants = _int_list(request.GET.getlist("variant"))
    q = request.GET.get("q", "").strip()
    category = request.GET.get("category", "").strip()
    if products or variants:
        qs = qs.filter(Q(product_id__in=products) | Q(id__in=variants))
    else:
        if category.isdigit():
            qs = qs.filter(product__category_id=int(category))
        if q:
            qs = qs.filter(Q(barcode__iexact=q) | Q(sku__icontains=q) | Q(name__icontains=q) | Q(product__name__icontains=q) | Q(barcode__icontains=q))
        if not q and not category.isdigit() and not request.GET.get("all"):
            return JsonResponse({"results": [], "truncated": False})
    qs = qs.order_by("product__name", "name")
    rows = list(qs[:MAX_ITEMS + 1])
    return JsonResponse({"results": [_item(v) for v in rows[:MAX_ITEMS]], "truncated": len(rows) > MAX_ITEMS})


@login_required
@permission_required("catalog.view_product", raise_exception=True)
def barcode_print(request):
    store = request.store
    preselect = {"product": _int_list(request.GET.getlist("product")), "variant": _int_list(request.GET.getlist("variant"))}
    company = store.company.name if getattr(store, "company_id", None) else store.name
    initial = []
    if preselect["product"] or preselect["variant"]:
        qs = _variants(store).filter(Q(product_id__in=preselect["product"]) | Q(id__in=preselect["variant"])).order_by("product__name", "name")
        initial = [_item(v) for v in qs[:MAX_ITEMS]]
    currencies = [{"code": store.currency_code, "symbol": store.currency_symbol, "rate": "1", "decimals": 2, "base": True}]
    currencies += [{"code": c.code, "symbol": c.symbol, "rate": f"{c.rate:f}", "decimals": c.decimals, "base": False}
                   for c in Currency.objects.filter(store=store, is_active=True).order_by("code")]
    return render(request, "catalog/barcode_print.html", {
        "categories": Category.objects.filter(store=store).order_by("name"),
        "config": {
            "company": company, "store": store.name, "currencySymbol": store.currency_symbol, "currencyCode": store.currency_code,
            "itemsUrl": reverse("catalog:barcode_items"), "initial": initial, "currencies": currencies,
            "currenciesUrl": reverse("configuration:currency_list"),
        },
    })
