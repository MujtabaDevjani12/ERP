from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, F, Max, Min, Q, Sum
from django.db.models import DecimalField, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.generic import CreateView, ListView, UpdateView, DeleteView

from core.listing import PagedListMixin
from core.mixins import DeleteContextMixin, SimpleListMixin, StoreCrudMixin, StoreScopedMixin
from .forms import AttributeValueFormSet, BrandForm, CategoryForm, ProductAttributeForm, ProductForm, UnitForm, VariantFormSet, VariantFormSetEdit
from . import barcodes
from .models import Brand, Category, ComboItem, Product, ProductAttribute, ProductVariant, Unit


# ---------------------------------------------------------------- Product
class ProductListView(PagedListMixin, LoginRequiredMixin, ListView):
    """Server-side search / filters / pagination. total_stock/min_price/max_price are annotated here (one query)
    instead of being computed per-row in the template, which would N+1."""
    filter_params = ("q", "category", "brand", "status", "stock")
    model = Product
    template_name = "catalog/product_list.html"
    context_object_name = "products"

    def get_queryset(self):
        qs = (
            Product.objects.filter(store=self.request.store)
            .select_related("category", "brand", "unit")
            .prefetch_related("variants")
            .annotate(
                total_stock=Coalesce(Sum("variants__stock_items__quantity"), Value(0), output_field=DecimalField()),
                variant_count=Count("variants", filter=Q(variants__is_active=True), distinct=True),
                min_price=Min("variants__selling_price"),
                max_price=Max("variants__selling_price"),
            )
        )
        g = self.request.GET
        q = g.get("q")
        if q:
            hit = ProductVariant.objects.filter(Q(sku__icontains=q) | Q(barcode__icontains=q)).values("product_id")
            qs = qs.filter(Q(name__icontains=q) | Q(sku__icontains=q) | Q(pk__in=hit))
        if g.get("category"):
            qs = qs.filter(category_id=g["category"])
        if g.get("brand"):
            qs = qs.filter(brand_id=g["brand"])
        if g.get("status") in ("active", "inactive"):
            qs = qs.filter(is_active=g["status"] == "active")
        if g.get("stock") == "out":
            qs = qs.filter(total_stock__lte=0, item_type="standard")
        elif g.get("stock") == "low":
            qs = qs.filter(total_stock__lte=F("alert_quantity"), item_type="standard")      # services / combos have no shelf to run low
        return qs.order_by("name")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["categories"] = Category.objects.filter(store=self.request.store).order_by("name")
        ctx["brands"] = Brand.objects.filter(store=self.request.store).order_by("name")
        return ctx


def _variant_sku(product, name):
    """Unique SKU for a variant whose SKU was left empty: BASE-DEF for the default variant, BASE-NAME otherwise."""
    from django.utils.text import slugify
    tail = "DEF" if not name or name.strip().lower() == "default" else slugify(name).upper()[:24] or "VAR"
    base = f"{product.sku}-{tail}"[:74]
    sku, n = base, 1
    while ProductVariant.objects.filter(sku__iexact=sku).exists():
        n += 1
        sku = f"{base}-{n}"
    return sku


def _save_pictures(product, form):
    """Cover removal, gallery removals and new gallery uploads (the form already checked count, size and that they are images)."""
    from .models import ProductImage
    if form.cleaned_data.get("remove_image") and not form.files.get("image") and product.image:
        product.image.delete(save=False)
        product.image = None
        product.save(update_fields=["image", "updated_at"])
    for pic in form.cleaned_data.get("remove_gallery") or []:
        pic.image.delete(save=False)
        pic.delete()
    last = (product.gallery.order_by("-sort_order").values_list("sort_order", flat=True).first() or 0)
    for n, f in enumerate(form.cleaned_data.get("gallery") or [], start=1):
        ProductImage.objects.create(product=product, image=f, sort_order=last + n)


def _stock_on_hand(formset):
    """Attach `on_hand` (all warehouses) to each saved variant form, for the Stock column of the edit page."""
    from django.db.models import Sum
    from inventory.models import StockItem
    ids = [f.instance.pk for f in formset.forms if f.instance.pk]
    totals = dict(StockItem.objects.filter(variant_id__in=ids).values_list("variant_id").annotate(t=Sum("quantity"))) if ids else {}
    for f in formset.forms:
        f.on_hand = totals.get(f.instance.pk, 0) if f.instance.pk else None
    return formset


@login_required
def barcode_preview(request):
    """PNG of the barcode for the text typed on the product form (400 when it cannot be drawn as a Code128)."""
    from core.utils import generate_barcode_png
    code = request.GET.get("code", "").strip()
    symbology = request.GET.get("type", "code128")
    if not code or len(code) > 64 or barcodes.validate(symbology, code):
        return HttpResponse(status=400)
    try:
        png = generate_barcode_png(code, symbology)
    except Exception:  # noqa: BLE001 - characters Code128 cannot encode
        return HttpResponse(status=400)
    return HttpResponse(png, content_type="image/png")


@login_required
def barcode_generate(request):
    """An unused code (internal "2" prefix, valid check digit) for items that carry no manufacturer barcode: EAN-13 unless
    `?type=` asks for EAN-8 / UPC-A. Code 128 and Code 39 can hold any text, so they get an EAN-13 number too."""
    kind = request.GET.get("type", "ean13")
    kind = kind if kind in barcodes.DIGITS else "ean13"
    try:
        code = barcodes.generate(kind, lambda c: ProductVariant.objects.filter(barcode=c).exists())
    except RuntimeError:
        return JsonResponse({"error": "Could not find a free code, please try again."}, status=503)
    return JsonResponse({"code": code})


def _stock_rows(instance):
    """Per variant and warehouse stock of a saved product (the "Stock now" card of the edit page)."""
    from inventory.models import StockItem
    if not instance:
        return []
    items = StockItem.objects.filter(variant__product=instance).select_related("variant", "warehouse").order_by("warehouse__name", "variant__name")
    return [{"variant": i.variant.name, "warehouse": i.warehouse.name, "qty": i.quantity,
             "low": instance.alert_quantity is not None and i.quantity <= instance.alert_quantity} for i in items]


def _extra_checks(form, formset):
    """Things that need the header AND the variant rows together: every barcode fits the chosen barcode type, and a promotion
    price is really lower than the selling price. Errors land on the fields, so the page shows them in place."""
    symbology = form.cleaned_data.get("barcode_symbology") or "code128"
    good = True
    for f in formset.live_forms:
        code = f.cleaned_data.get("barcode")
        message = barcodes.validate(symbology, code) if code else None
        if message:
            f.add_error("barcode", message + " Pick another barcode type or press Generate.")
            good = False
    promo = form.cleaned_data.get("promo_price")
    if promo is not None and formset.live_forms:
        price = formset.live_forms[0].cleaned_data.get("selling_price")
        if price is not None and promo >= price:
            form.add_error("promo_price", f"The promotion price must be lower than the selling price ({price:.2f}).")
            good = False
    return good


def _save_combo(product, form):
    if product.item_type != Product.ITEM_COMBO:
        product.combo_items.all().delete()
        return
    wanted = {v.pk: q for v, q in form.cleaned_data.get("combo_items") or []}
    product.combo_items.exclude(component_id__in=wanted).delete()
    for variant_id, qty in wanted.items():
        ComboItem.objects.update_or_create(combo=product, component_id=variant_id, defaults={"quantity": qty})


@login_required
@permission_required("catalog.add_product", raise_exception=True)
def product_form(request, pk=None):
    from django.db import transaction
    from django.db.models import ProtectedError
    from inventory.models import StockItem

    store = request.store
    instance = get_object_or_404(Product, pk=pk, store=store) if pk else None
    was_variant = bool(instance and instance.product_type == "variant")     # read before the form touches the instance

    if request.method == "POST":
        form = ProductForm(request.POST, request.FILES, instance=instance, store=store)
        formset = (VariantFormSetEdit if instance and instance.variants.exists() else VariantFormSet)(request.POST, request.FILES, instance=instance or Product(store=store), prefix="variants", store=store)
        ok = form.is_valid()
        ok = formset.is_valid() and ok                      # validate both so every problem is shown at once
        if ok:
            ok = _extra_checks(form, formset)
        collapsed = []
        if ok and form.cleaned_data["product_type"] == "single" and len(formset.live_forms) > 1:
            # "This product has variants" switched off: the first row stays as the product's one variant, the others go
            # (a variant that was already sold or bought is kept but switched off, see below)
            keeper, *extras = formset.live_forms
            for f in extras:
                f.cleaned_data["DELETE"] = True
                collapsed.append(f)
            formset.live_forms = [keeper]
            formset.__dict__.pop("_deleted_form_indexes", None)       # Django cached the delete list during validation: rebuild it
        if ok:
            new_forms = [f for f in formset.live_forms if f.instance.pk is None]
            with transaction.atomic():
                product = form.save(commit=False)
                product.store = store
                product._skip_default_variant = True
                product.save()
                form.save_m2m()
                formset.instance = product
                changed = formset.save(commit=False)
                retired = []
                for gone in formset.deleted_objects:
                    try:
                        with transaction.atomic():
                            gone.delete()
                    except ProtectedError:                 # already sold / bought: keep the history, just stop selling it
                        gone.is_active = False
                        gone.save(update_fields=["is_active", "updated_at"])
                        retired.append(gone.name)
                if collapsed or (was_variant and product.product_type == "single"):
                    keep = formset.live_forms[0].instance
                    if keep.name != "Default":
                        keep.name = "Default"
                        if keep.pk and keep not in changed:
                            keep.save(update_fields=["name", "updated_at"])
                for variant in changed:
                    if not variant.sku:
                        variant.sku = _variant_sku(product, variant.name)
                    if not variant.barcode and product.barcode_symbology in barcodes.DIGITS:      # EAN / UPC need a real numeric code
                        variant.barcode = barcodes.generate(product.barcode_symbology, lambda c: ProductVariant.objects.filter(barcode=c).exists())
                    variant.save()
                formset.save_m2m()
                _save_combo(product, form)
                warehouse = form.cleaned_data.get("stock_location")
                if warehouse is not None and product.tracks_stock:
                    for f in new_forms:
                        qty = f.cleaned_data.get("opening_stock")
                        if qty and qty > 0:
                            item, _ = StockItem.objects.get_or_create(variant=f.instance, warehouse=warehouse)
                            item.adjust(qty, reference_type="OPENING_STOCK", reference_no=product.sku, user=request.user,
                                        remarks="Opening stock", unit_cost=f.instance.cost_price)
                _save_pictures(product, form)
            count = product.variants.filter(is_active=True).count()
            msg = f"Product “{product.name}” saved with {count} active variant(s)."
            if collapsed and not retired:
                msg += f" {len(collapsed)} other variant(s) were removed."
            if retired:
                msg += f" {', '.join(retired)} could not be deleted because it has sales or purchases, so it was switched off instead."
            messages.success(request, msg)
            if "save_and_print" in request.POST:
                return redirect(f"{reverse('catalog:barcode_print')}?product={product.pk}")
            if "save_and_new" in request.POST:
                return redirect("catalog:product_add")
            return redirect("catalog:product_list")
    else:
        form = ProductForm(instance=instance, store=store)
        formset = (VariantFormSetEdit if instance and instance.variants.exists() else VariantFormSet)(instance=instance or Product(store=store), prefix="variants", store=store)

    _stock_on_hand(formset)
    return render(request, "catalog/product_form.html", {
        "form": form, "formset": formset, "instance": instance, "gallery_max": 8, "combo_rows": form.combo_rows(),
        "gallery": list(instance.gallery.all()) if instance else [],
        "attributes": ProductAttribute.objects.filter(store=store).prefetch_related("values"),
        "category_tree": list(Category.objects.filter(store=store).order_by("name").values("id", "name", "parent_id")),
        "stock_rows": _stock_rows(instance),
    })


@login_required
def variant_barcode_png(request, pk):
    from core.utils import generate_barcode_png
    variant = get_object_or_404(ProductVariant.objects.select_related("product"), pk=pk, product__store=request.store)
    try:
        png = generate_barcode_png(variant.barcode, variant.product.barcode_symbology)
    except Exception:  # noqa: BLE001 - an old code that does not fit the chosen symbology: fall back to Code 128
        png = generate_barcode_png(variant.barcode)
    return HttpResponse(png, content_type="image/png")


@login_required
def variant_lookup(request):
    """Used by the POS screen and the quantity-adjustment scanner: type or
    scan a barcode/SKU/name to search, and/or pass a category to filter —
    with neither, it browses the store's active products, which is what
    lets the POS product grid show items immediately (behind the category
    rail) instead of staying empty until someone types something.

    `pos=1` (sent by the POS screen only) returns just the products the counter can actually sell: active, and with stock
    above zero in the counter's warehouse. Out-of-stock products stay visible everywhere else (ERP product/inventory pages,
    the scanner)."""
    from sales.pos_stock import annotate_pos_stock, only_in_stock
    from register.views import get_active_session

    q = request.GET.get("q", "").strip()
    category_id = request.GET.get("category", "").strip()
    pos_mode = request.GET.get("pos") == "1"
    stock_wh = int(request.GET["warehouse"]) if request.GET.get("warehouse", "").isdigit() and not pos_mode else None

    variants = ProductVariant.objects.filter(product__store=request.store, is_active=True)
    warehouse = None
    if pos_mode:
        session = get_active_session(request)
        warehouse = session.register.warehouse if session else None
        variants = variants.filter(product__is_active=True)
        if warehouse is not None:
            variants = only_in_stock(variants, warehouse)
    if category_id:
        variants = variants.filter(product__category_id=category_id)
    product_id = request.GET.get("product", "").strip()
    # the POS variant picker asks for every variant of one product, empty shelves included (shown greyed out, so the cashier sees them)
    include_empty = pos_mode and request.GET.get("all") == "1" and product_id.isdigit()
    if include_empty and warehouse is not None:
        variants = ProductVariant.objects.filter(product__store=request.store, is_active=True, product__is_active=True)
    if product_id.isdigit():                                       # every variant of one product (the POS quick view)
        variants = variants.filter(product_id=int(product_id))
    if q and request.GET.get("exact") == "1":           # a scanned code: exactly this barcode or SKU, nothing fuzzy
        variants = variants.filter(Q(barcode__iexact=q) | Q(sku__iexact=q))
    elif q:
        lookup = Q(barcode__iexact=q) | Q(barcode__icontains=q) | Q(sku__icontains=q) | Q(name__icontains=q) | Q(product__name__icontains=q)
        variants = variants.filter(lookup)
    variants = variants.select_related("product", "product__unit", "product__category", "product__brand").prefetch_related("attribute_values__attribute")
    if warehouse is not None:
        variants = annotate_pos_stock(variants, warehouse)
    if q and request.GET.get("exact") != "1":                    # an exact barcode / SKU hit comes first, then name order
        from django.db.models import Case, IntegerField, When
        variants = variants.annotate(_rank=Case(When(barcode__iexact=q, then=0), When(sku__iexact=q, then=1), default=2, output_field=IntegerField()))
        variants = variants.order_by("_rank", "product__name")[:60]
    else:
        variants = variants.order_by("product__name")[:60]
    results = []
    rows = list(variants)
    from sales.pos_stock import UNLIMITED, stock_map
    sellable = stock_map(warehouse, [v.id for v in rows]) if warehouse is not None else {}
    # how many variants each product has on offer (the POS shows one tile per product and asks which variant to add)
    siblings = ProductVariant.objects.filter(product_id__in={v.product_id for v in rows}, is_active=True, product__is_active=True)
    variant_counts = {}
    for pid in siblings.values_list("product_id", flat=True):
        variant_counts[pid] = variant_counts.get(pid, 0) + 1
    for v in rows:
        kind = v.product.item_type
        if warehouse is not None:
            qty = sellable.get(v.id, 0)                  # services: unlimited; combos: whole bundles the parts allow
        elif kind == Product.ITEM_STANDARD:
            stock = v.stock_items.filter(warehouse__store=request.store)
            if stock_wh:                                  # purchase / adjustment screens: what THAT warehouse holds
                stock = stock.filter(warehouse_id=stock_wh)
            qty = sum((s.quantity for s in stock), start=0)
        else:
            qty = UNLIMITED if kind != Product.ITEM_COMBO else 0
        if pos_mode and qty <= 0 and not include_empty:  # no counter warehouse known: still never offer empty shelves
            continue
        results.append({
            "id": v.id,
            "name": str(v),
            "sku": v.sku,
            "barcode": v.barcode,
            "price": str(v.current_price),
            "regular_price": str(v.selling_price),
            "cost": str(v.cost_price),
            "on_promo": v.on_promotion,
            "item_type": kind,
            "tracked": kind == Product.ITEM_STANDARD,
            "unit": v.product.unit.short_name,
            "stock": str(qty),
            "out_of_stock": bool(pos_mode and qty <= 0),
            "image": v.image.url if v.image else (v.product.image.url if v.product.image else None),
            "product_id": v.product_id,
            "variant_count": variant_counts.get(v.product_id, 1),
            "product": v.product.name,
            "variant": "" if v.name.strip().lower() == "default" else v.name,
            "category": v.product.category.name if v.product.category_id else "",
            "brand": v.product.brand.name if v.product.brand_id else "",
            "description": (v.product.description or "")[:240],
            "attrs": [{"name": av.attribute.name, "value": av.value} for av in sorted(v.attribute_values.all(), key=lambda a: a.attribute.name)],
        })
    return JsonResponse({"results": results})


# ---------------------------------------------------------------- Category / Brand / Unit
class CategoryListView(StoreScopedMixin, SimpleListMixin, ListView):
    model = Category
    page_title = "Categories"
    excel_key = "categories"
    add_url_name = "catalog:category_add"
    edit_url_name = "catalog:category_edit"
    delete_url_name = "catalog:category_delete"

    def get_queryset(self):
        return super().get_queryset().order_by("name")


class CategoryCreateView(StoreCrudMixin, CreateView):
    model = Category
    form_class = CategoryForm
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("catalog:category_list")
    permission_required = "catalog.add_category"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["store"] = self.request.store
        return kwargs

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Add category", back_url=self.success_url, **kwargs)


class CategoryUpdateView(StoreCrudMixin, UpdateView):
    model = Category
    form_class = CategoryForm
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("catalog:category_list")
    permission_required = "catalog.change_category"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["store"] = self.request.store
        return kwargs

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Edit category", back_url=self.success_url, **kwargs)


class CategoryDeleteView(StoreCrudMixin, DeleteContextMixin, DeleteView):
    model = Category
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("catalog:category_list")
    permission_required = "catalog.delete_category"


class BrandListView(StoreScopedMixin, SimpleListMixin, ListView):
    model = Brand
    page_title = "Brands"
    excel_key = "brands"
    add_url_name = "catalog:brand_add"
    edit_url_name = "catalog:brand_edit"
    delete_url_name = "catalog:brand_delete"


class BrandCreateView(StoreCrudMixin, CreateView):
    model = Brand
    form_class = BrandForm
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("catalog:brand_list")
    permission_required = "catalog.add_brand"

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Add brand", back_url=self.success_url, **kwargs)


class BrandUpdateView(StoreCrudMixin, UpdateView):
    model = Brand
    form_class = BrandForm
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("catalog:brand_list")
    permission_required = "catalog.change_brand"

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Edit brand", back_url=self.success_url, **kwargs)


class BrandDeleteView(StoreCrudMixin, DeleteContextMixin, DeleteView):
    model = Brand
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("catalog:brand_list")
    permission_required = "catalog.delete_brand"


class UnitListView(StoreScopedMixin, SimpleListMixin, ListView):
    model = Unit
    page_title = "Units"
    add_url_name = "catalog:unit_add"
    edit_url_name = "catalog:unit_edit"
    delete_url_name = "catalog:unit_delete"


class UnitCreateView(StoreCrudMixin, CreateView):
    model = Unit
    form_class = UnitForm
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("catalog:unit_list")
    permission_required = "catalog.add_unit"

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Add unit", back_url=self.success_url, **kwargs)


class UnitUpdateView(StoreCrudMixin, UpdateView):
    model = Unit
    form_class = UnitForm
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("catalog:unit_list")
    permission_required = "catalog.change_unit"

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Edit unit", back_url=self.success_url, **kwargs)


class UnitDeleteView(StoreCrudMixin, DeleteContextMixin, DeleteView):
    model = Unit
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("catalog:unit_list")
    permission_required = "catalog.delete_unit"


def _split_values(text):
    """'Small, Medium\nLarge' -> ['Small', 'Medium', 'Large'] (blanks and repeats dropped, original order kept)."""
    out, seen = [], set()
    for part in str(text or "").replace("\n", ",").replace(";", ",").split(","):
        v = part.strip()[:60]
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out


def _add_values(attribute, values):
    from .models import ProductAttributeValue
    have = {v.lower() for v in attribute.values.values_list("value", flat=True)}
    made = []
    for v in values:
        if v.lower() not in have:
            ProductAttributeValue.objects.create(attribute=attribute, value=v)
            have.add(v.lower())
            made.append(v)
    return made


@login_required
@permission_required("catalog.add_productattribute", raise_exception=True)
def attribute_list(request):
    """Attributes are the questions a variant answers (Size, Color, Karat); each has its values (S, M, L). Both are added here:
    type the name and, optionally, its values separated by commas."""
    store = request.store
    if request.method == "POST":
        form = ProductAttributeForm(request.POST)
        if form.is_valid():
            name = form.cleaned_data["name"].strip()
            if ProductAttribute.objects.filter(store=store, name__iexact=name).exists():
                form.add_error("name", "This attribute already exists. Open it below to add more values.")
            else:
                attr = form.save(commit=False)
                attr.name = name
                attr.store = store
                attr.save()
                made = _add_values(attr, _split_values(request.POST.get("values")))
                messages.success(request, f"Attribute “{attr.name}” added" + (f" with {len(made)} value(s)." if made else " — now add its values."))
                return redirect("catalog:attribute_list" if made else "catalog:attribute_values", **({} if made else {"pk": attr.pk}))
    else:
        form = ProductAttributeForm()
    attributes = ProductAttribute.objects.filter(store=store).prefetch_related("values")
    return render(request, "catalog/attribute_list.html", {"attributes": attributes, "form": form, "typed_values": request.POST.get("values", "")})


@login_required
@permission_required("catalog.change_productattribute", raise_exception=True)
def attribute_values(request, pk):
    attribute = get_object_or_404(ProductAttribute, pk=pk, store=request.store)
    if request.method == "POST":
        if "bulk" in request.POST:                               # "Add several at once": S, M, L
            made = _add_values(attribute, _split_values(request.POST.get("bulk")))
            messages.success(request, f"Added {len(made)} value(s)." if made else "Nothing new to add: those values already exist.")
            return redirect("catalog:attribute_values", pk=attribute.pk)
        formset = AttributeValueFormSet(request.POST, instance=attribute)
        if formset.is_valid():
            formset.save()
            messages.success(request, "Values saved.")
            return redirect("catalog:attribute_list")
    else:
        formset = AttributeValueFormSet(instance=attribute)
    return render(request, "catalog/attribute_values.html", {"attribute": attribute, "formset": formset})
