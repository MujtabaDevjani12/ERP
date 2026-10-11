"""
Global search behind the Ctrl+K command palette.

GET /erp/search/?q=...  ->  {"results": [{"group", "title", "subtitle", "url"}, ...]}

Searches the original POS data (products, customers, suppliers, purchase orders, sales, gift cards — scoped to the
user's store) and every ERP register that defines `search_fields` in its admin (scoped by that admin's own queryset and
permissions). Every block is isolated: one failing model never breaks the box.
"""
from django.contrib import admin
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.urls import NoReverseMatch, reverse
from django.utils.text import capfirst

from . import registry

PER_LEGACY = 4
PER_ERP = 3
MAX_ERP = 15


def _url(name, *args):
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return ""


def _legacy(request, q):
    out = []
    store = getattr(request, "store", None)
    user = request.user
    if store is None:
        return out

    def run(label, fn):
        try:
            for title, subtitle, url in fn():
                if url:
                    out.append({"group": label, "title": title, "subtitle": subtitle, "url": url})
        except Exception:                                           # noqa: BLE001 - search must never raise
            pass

    def products():
        from catalog.models import Product, ProductVariant
        seen = set()
        for p in Product.objects.filter(store=store).filter(Q(name__icontains=q) | Q(sku__icontains=q))[:PER_LEGACY]:
            seen.add(p.pk)
            yield p.name, "SKU %s" % p.sku, _url("catalog:product_edit", p.pk)
        if len(seen) < PER_LEGACY:
            for v in ProductVariant.objects.filter(product__store=store).filter(Q(sku__icontains=q) | Q(barcode__icontains=q)).select_related("product")[:PER_LEGACY]:
                if v.product_id not in seen:
                    seen.add(v.product_id)
                    yield v.product.name, "%s · %s" % (v.name, v.barcode or v.sku), _url("catalog:product_edit", v.product_id)

    def customers():
        from customers.models import Customer
        for c in Customer.objects.filter(store=store).filter(Q(name__icontains=q) | Q(phone__icontains=q) | Q(email__icontains=q))[:PER_LEGACY]:
            yield c.name, c.phone or c.email, _url("customers:customer_edit", c.pk)

    def suppliers():
        from suppliers.models import Supplier
        for s in Supplier.objects.filter(store=store).filter(Q(name__icontains=q) | Q(phone__icontains=q) | Q(email__icontains=q))[:PER_LEGACY]:
            yield s.name, s.phone or s.email, _url("suppliers:supplier_edit", s.pk)

    def purchases():
        from suppliers.models import PurchaseOrder
        for po in PurchaseOrder.objects.filter(supplier__store=store, reference__icontains=q).select_related("supplier")[:PER_LEGACY]:
            yield po.reference, "%s · %s" % (po.supplier.name, po.get_status_display()), _url("suppliers:purchase_detail", po.pk)

    def sales():
        from sales.models import Sale
        if not user.has_perm("sales.view_sale"):
            return
        for s in Sale.objects.filter(store=store, reference__icontains=q).select_related("customer")[:PER_LEGACY]:
            yield s.reference, "%s · %s" % (s.get_status_display(), s.total_amount), _url("sales:sale_detail", s.pk)

    def gift_cards():
        from customers.models import GiftCard
        for g in GiftCard.objects.filter(store=store, code__icontains=q)[:PER_LEGACY]:
            yield g.code, "Gift card", _url("customers:giftcard_edit", g.pk)

    run("Products", products)
    run("Customers", customers)
    run("Sales", sales)
    run("Suppliers", suppliers)
    run("Purchase orders", purchases)
    run("Gift cards", gift_cards)
    return out


def _erp(request, q):
    out = []
    for model, ma in registry.model_registry():
        if len(out) >= MAX_ERP:
            break
        if not getattr(ma, "search_fields", None):
            continue
        try:
            if not ma.has_view_permission(request):
                continue
            qs, _ = ma.get_search_results(request, ma.get_queryset(request), q)
            label = capfirst(str(model._meta.verbose_name_plural))
            for obj in qs[:PER_ERP]:
                out.append({"group": label, "title": str(obj), "subtitle": registry.MODULE_TITLES.get(model._meta.app_label, ""),
                            "url": registry.edit_url(model, obj.pk)})
        except Exception:                                           # noqa: BLE001
            continue
    return out


def _create_links(request, q):
    """'new purchase order' style shortcuts for ERP registers the user may add to."""
    needle = q.lower()
    if not any(w in needle for w in ("new", "add", "create")) and len(needle) < 4:
        return []
    out = []
    for model, ma in registry.model_registry():
        name = str(model._meta.verbose_name)
        if needle.replace("new", "").replace("add", "").replace("create", "").strip() not in name.lower():
            continue
        try:
            if ma.has_add_permission(request):
                out.append({"group": "Create", "title": "New %s" % name, "subtitle": registry.MODULE_TITLES.get(model._meta.app_label, ""),
                            "url": registry.add_url(model)})
        except Exception:                                           # noqa: BLE001
            continue
        if len(out) >= 6:
            break
    return out


@login_required
def search(request):
    q = (request.GET.get("q") or "").strip()[:80]
    if len(q) < 2:
        return JsonResponse({"results": []})
    results = _legacy(request, q) + _erp(request, q) + _create_links(request, q)
    return JsonResponse({"results": results[:40]})
