import json
from decimal import Decimal, InvalidOperation

from django import forms
from django.forms import inlineformset_factory

from tenants.models import Warehouse
from .models import Brand, Category, Product, ProductAttribute, ProductAttributeValue, ProductImage, ProductVariant, Unit



MAX_PICTURE_BYTES = 3 * 1024 * 1024


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleImageField(forms.ImageField):
    """Several pictures from one <input multiple>; every file is checked to be a real image."""
    widget = MultipleFileInput

    def clean(self, data, initial=None):
        single = super().clean
        if isinstance(data, (list, tuple)):
            return [single(d, initial) for d in data if d]
        return [single(data, initial)] if data else []


def _check_picture_size(file, label):
    if file and getattr(file, "size", 0) > MAX_PICTURE_BYTES:
        raise forms.ValidationError(f"{label} is larger than 3 MB. Please use a smaller picture.")


class ProductForm(forms.ModelForm):
    """Product header. `stock_location` is not stored on the product: it is where the opening stock typed on the variant
    rows is booked (new variants only)."""

    gallery = MultipleImageField(required=False, label="Gallery pictures", widget=MultipleFileInput(attrs={"accept": "image/*", "multiple": True}))
    remove_gallery = forms.ModelMultipleChoiceField(queryset=ProductImage.objects.none(), required=False)
    remove_image = forms.BooleanField(required=False, label="Remove the cover picture")
    combo_items = forms.CharField(required=False, widget=forms.HiddenInput(),
                                  help_text="JSON list of {variant, qty} - what a combo is made of (edited by the combo box on the page).")
    stock_location = forms.ModelChoiceField(queryset=Warehouse.objects.none(), required=False, empty_label=None, label="Opening stock location",
                                            help_text="Where the opening quantity is added.")

    class Meta:
        model = Product
        fields = ["category", "brand", "unit", "tax", "name", "sku", "description", "product_type", "item_type", "barcode_symbology",
                  "promo_price", "promo_start", "promo_end", "image", "alert_quantity", "is_active"]
        labels = {"sku": "SKU", "alert_quantity": "Low-stock alert at", "is_active": "Active (can be sold)", "item_type": "Product type",
                  "barcode_symbology": "Barcode type", "promo_price": "Promotion price", "promo_start": "Starts on", "promo_end": "Ends on"}
        help_texts = {"sku": "Product code. Leave empty and one is made from the name.",
                      "alert_quantity": "A low-stock badge shows when stock falls to this level.",
                      "tax": "Leave empty for tax-free products."}
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Basmati Rice 5kg", "autocomplete": "off"}),
            "sku": forms.TextInput(attrs={"placeholder": "e.g. RICE-5KG", "autocomplete": "off"}),
            "description": forms.Textarea(attrs={"rows": 3}),
            "product_type": forms.Select(),
            "promo_price": forms.NumberInput(attrs={"step": "0.01", "min": "0", "placeholder": "e.g. 450"}),
            "promo_start": forms.DateInput(attrs={"type": "date"}), "promo_end": forms.DateInput(attrs={"type": "date"}),
            "alert_quantity": forms.NumberInput(attrs={"step": "0.001", "min": "0"}),
            "image": forms.FileInput(attrs={"accept": "image/*"}),
            "category": forms.Select(), "description": forms.Textarea(attrs={"rows": 3, "placeholder": "Short description (optional)"}),
        }

    def __init__(self, *args, store=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store
        if self.instance.pk:
            self.fields["remove_gallery"].queryset = self.instance.gallery.all()
        if store is not None:
            self.fields["category"].queryset = Category.objects.filter(store=store)
            self.fields["brand"].queryset = Brand.objects.filter(store=store)
            self.fields["unit"].queryset = Unit.objects.filter(store=store)
            self.fields["tax"].queryset = self.fields["tax"].queryset.filter(store=store)
            warehouses = Warehouse.objects.filter(store=store).order_by("-is_default", "name")
            self.fields["stock_location"].queryset = warehouses
            if not self.instance.pk:
                self.fields["stock_location"].initial = warehouses.first()
                units = self.fields["unit"].queryset
                self.fields["unit"].initial = (units.filter(name__iexact="piece").first() or units.order_by("pk").first())
        self.fields["sku"].required = False                          # empty = made from the name (see clean_sku)
        self.fields["item_type"].required = False                    # an older form / import that does not send them keeps the saved value
        self.fields["barcode_symbology"].required = False
        self.fields["category"].empty_label = "— No category —"
        self.fields["brand"].empty_label = "— No brand —"
        self.fields["tax"].empty_label = "— No tax —"
        self.fields["unit"].empty_label = "— Choose a unit —"

    def clean_name(self):
        return (self.cleaned_data.get("name") or "").strip()

    def clean_image(self):
        image = self.cleaned_data.get("image")
        if image and hasattr(image, "size"):                   # a freshly uploaded file (an unchanged one is a FieldFile)
            _check_picture_size(image, "The cover picture")
        return image

    def clean_gallery(self):
        files = self.cleaned_data.get("gallery") or []
        for f in files:
            _check_picture_size(f, f"“{getattr(f, 'name', 'A picture')}”")
        return files

    def clean_item_type(self):
        return self.cleaned_data.get("item_type") or (self.instance.item_type if self.instance.pk else Product.ITEM_STANDARD)

    def clean_barcode_symbology(self):
        return self.cleaned_data.get("barcode_symbology") or (self.instance.barcode_symbology if self.instance.pk else "code128")

    def clean_combo_items(self):
        """-> [(ProductVariant, Decimal quantity)] from the JSON the combo box posts (checked against this store's stocked products)."""
        raw = (self.cleaned_data.get("combo_items") or "").strip()
        if not raw:
            return []
        try:
            rows = json.loads(raw)
            wanted = [(int(r["variant"]), Decimal(str(r["qty"]))) for r in rows]
        except (ValueError, TypeError, KeyError, InvalidOperation):
            raise forms.ValidationError("The combo contents could not be read. Reload the page and try again.")
        ids = [v for v, _ in wanted]
        if len(set(ids)) != len(ids):
            raise forms.ValidationError("A product is listed twice in the combo. Raise its quantity instead.")
        if any((not q.is_finite()) or q <= 0 or q > 1000000 for _, q in wanted):
            raise forms.ValidationError("Every product in the combo needs a quantity above zero.")
        qs = ProductVariant.objects.filter(pk__in=ids, product__item_type=Product.ITEM_STANDARD).select_related("product")
        if self.store is not None:
            qs = qs.filter(product__store=self.store)
        if self.instance.pk:
            qs = qs.exclude(product=self.instance)
        found = {v.pk: v for v in qs}
        if len(found) != len(ids):
            raise forms.ValidationError("A product in the combo is not available. Only ordinary products (not services, digital items, combos or this product itself) can be inside a combo.")
        return [(found[i], q.quantize(Decimal("0.001"))) for i, q in wanted]

    def combo_rows(self):
        """Rows for the combo box: [{variant, name, sku, unit, qty}] - what was posted when the form is bound, else what is saved."""
        if self.is_bound:
            try:
                posted = [(int(r["variant"]), str(r["qty"])) for r in json.loads(self.data.get("combo_items") or "[]")]
            except (ValueError, TypeError, KeyError):
                posted = []
            found = {v.pk: v for v in ProductVariant.objects.filter(pk__in=[i for i, _ in posted]).select_related("product__unit")}
            return [self._combo_row(found[i], q) for i, q in posted if i in found]
        if not self.instance.pk:
            return []
        return [self._combo_row(ci.component, f"{ci.quantity:f}".rstrip("0").rstrip(".") or "1")
                for ci in self.instance.combo_items.select_related("component__product__unit")]

    @staticmethod
    def _combo_row(variant, qty):
        return {"variant": variant.pk, "name": str(variant), "sku": variant.sku, "unit": variant.product.unit.short_name, "qty": qty}

    def clean(self):
        data = super().clean()
        kind = data.get("item_type") or Product.ITEM_STANDARD
        if kind == Product.ITEM_COMBO:
            if data.get("product_type") == "variant":
                self.add_error("item_type", "A combo is one product, so “This product has variants” must be off.")
            if not data.get("combo_items") and "combo_items" not in self.errors:
                self.add_error("combo_items", "Add at least one product to the combo.")
        elif self.instance.pk and kind != Product.ITEM_STANDARD:
            used = self.instance.variants.filter(used_in_combos__isnull=False).values_list("used_in_combos__combo__name", flat=True).first()
            if used:
                self.add_error("item_type", f"This product is part of the combo “{used}”, so it must stay a standard product.")
        promo, start, end = data.get("promo_price"), data.get("promo_start"), data.get("promo_end")
        if promo is None and (start or end):
            self.add_error("promo_price", "Enter the promotion price, or clear the dates.")
        if promo is not None and data.get("product_type") == "variant":
            self.add_error("promo_price", "A promotion price works on a single product. For a product with variants, change each variant's selling price instead.")
        if start and end and end < start:
            self.add_error("promo_end", "The promotion cannot end before it starts.")
        files = data.get("gallery") or []
        if files:
            keep = self.instance.gallery.count() - len(data.get("remove_gallery") or []) if self.instance.pk else 0
            if keep + len(files) > ProductImage.MAX_PER_PRODUCT:
                self.add_error("gallery", f"A product can have at most {ProductImage.MAX_PER_PRODUCT} gallery pictures "
                                          f"({max(keep, 0)} already kept, {len(files)} new).")
        return data

    def clean_sku(self):
        sku = (self.cleaned_data.get("sku") or "").strip()
        if not sku:
            if self.instance.pk:                                   # clearing the box on an existing product keeps its code
                return self.instance.sku
            sku = self._sku_from_name()
        if self.store is not None:
            clash = Product.objects.filter(store=self.store, sku__iexact=sku).exclude(pk=self.instance.pk)
            if clash.exists():
                raise forms.ValidationError("Another product in this store already uses this SKU.")
        return sku

    def _sku_from_name(self):
        """A free product code made from the name: 'Green Tea 100' -> GREEN-TEA-100, then -2, -3 ... when taken."""
        from django.utils.text import slugify
        base = (slugify(self.cleaned_data.get("name") or "").upper() or "ITEM")[:40]
        sku, n = base, 1
        while self.store is not None and Product.objects.filter(store=self.store, sku__iexact=sku).exists():
            n += 1
            sku = f"{base}-{n}"
        return sku


class VariantForm(forms.ModelForm):
    """One sellable variant. SKU and barcode may be left empty: they are generated on save. `opening_stock` only applies
    to a variant that does not exist yet."""

    opening_stock = forms.DecimalField(required=False, min_value=0, max_digits=14, decimal_places=3, label="Opening stock",
                                       widget=forms.NumberInput(attrs={"step": "any", "min": "0", "placeholder": "0"}))

    class Meta:
        model = ProductVariant
        fields = ["name", "sku", "barcode", "attribute_values", "cost_price", "selling_price", "image", "is_active"]
        labels = {"name": "Variant name", "sku": "SKU", "barcode": "Barcode", "cost_price": "Cost price", "selling_price": "Selling price",
                  "attribute_values": "Attributes", "is_active": "Active"}
        widgets = {
            "name": forms.TextInput(attrs={"placeholder": "e.g. Small / Red"}),
            "sku": forms.TextInput(attrs={"placeholder": "Auto"}),
            "barcode": forms.TextInput(attrs={"placeholder": "Scan or type", "autocomplete": "off"}),
            "attribute_values": forms.CheckboxSelectMultiple,
            "cost_price": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
            "selling_price": forms.NumberInput(attrs={"step": "0.01", "min": "0"}),
        }
        error_messages = {"sku": {"unique": "This SKU is already used by another variant."},
                          "barcode": {"unique": "This barcode already belongs to another variant."}}

    def __init__(self, *args, store=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sku"].required = False
        self.fields["barcode"].required = False
        for key in ("cost_price", "selling_price"):                  # blank = same as the first variant (decided in the formset)
            self.fields[key].required = False
            self.fields[key].widget.attrs["placeholder"] = "Same as first"
        for key in ("name", "cost_price", "selling_price", "opening_stock", "barcode", "sku"):
            self.fields[key].widget.attrs.setdefault("aria-label", str(self.fields[key].label))
        self.fields["opening_stock"].widget.attrs["placeholder"] = "0"
        if not self.instance.pk:                                      # a new row starts empty, not at 0, so a forgotten price is noticed
            for key in ("cost_price", "selling_price"):
                self.initial.pop(key, None)
                self.fields[key].initial = None
        qs = ProductAttributeValue.objects.select_related("attribute").order_by("attribute__name", "value")
        if store is not None:
            qs = qs.filter(attribute__store=store)
        self.fields["attribute_values"].queryset = qs
        self.fields["attribute_values"].required = False

    def attribute_groups(self):
        """The attribute values grouped by attribute (Size: S M L / Color: Red Blue) for the variant card. One value per
        attribute per variant, so the card shows each attribute as its own row of choices."""
        bf = self["attribute_values"]
        selected = {str(getattr(v, "pk", v)) for v in (bf.value() or [])}
        groups, order = {}, []
        for val in self.fields["attribute_values"].queryset:
            if val.attribute_id not in groups:
                groups[val.attribute_id] = {"name": val.attribute.name, "options": []}
                order.append(val.attribute_id)
            groups[val.attribute_id]["options"].append({
                "id": f"{bf.auto_id}_{val.pk}", "html_name": bf.html_name, "value": val.pk, "label": val.value, "checked": str(val.pk) in selected})
        return [groups[k] for k in order]

    def clean_attribute_values(self):
        values = self.cleaned_data.get("attribute_values")
        if values:
            seen = {}
            for v in values:
                if v.attribute_id in seen:
                    raise forms.ValidationError(f"Choose only one {v.attribute.name} for this variant (you picked {seen[v.attribute_id]} and {v.value}).")
                seen[v.attribute_id] = v.value
        return values

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            raise forms.ValidationError("Enter a variant name.")
        return name

    def clean_sku(self):
        return (self.cleaned_data.get("sku") or "").strip() or None      # None: skipped by the duplicate-in-formset check

    def clean_barcode(self):
        return (self.cleaned_data.get("barcode") or "").strip() or None


class BaseVariantFormSet(forms.BaseInlineFormSet):
    def __init__(self, *args, store=None, **kwargs):
        self.store = store
        super().__init__(*args, **kwargs)

    def get_form_kwargs(self, index):
        kwargs = super().get_form_kwargs(index)
        kwargs["store"] = self.store
        return kwargs

    def _construct_form(self, i, **kwargs):
        form = super()._construct_form(i, **kwargs)
        if i == 0 and not self.instance.pk:
            form.empty_permitted = False                                   # a new product always needs its first variant
        return form

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        self.live_forms = [f for f in self.forms if f.cleaned_data and not self._should_delete_form(f)]
        if not self.live_forms:
            raise forms.ValidationError("A product needs at least one variant. Keep the \"Default\" row, or add one.")
        # every variant of a product has its own name (Small / Medium / Large)
        seen, clash = {}, False
        for f in self.live_forms:
            key = f.cleaned_data["name"].lower()
            if key in seen:
                f.add_error("name", f"“{f.cleaned_data['name']}” is already used by another variant of this product.")
                clash = True
            seen[key] = True
        if clash:
            raise forms.ValidationError("Each variant needs a different name.")
        # a blank Cost / Selling price means "same as the first variant that has one" (like the product price in a simple form)
        from decimal import Decimal
        for field in ("cost_price", "selling_price"):
            base = next((f.cleaned_data[field] for f in self.live_forms if f.cleaned_data.get(field) is not None), None)
            for f in self.live_forms:
                if f.cleaned_data.get(field) is None:
                    if base is None and field == "selling_price":
                        f.add_error(field, "Enter a selling price.")
                        raise forms.ValidationError("Enter a selling price for the product.")
                    value = base if base is not None else Decimal("0")
                    f.cleaned_data[field] = value
                    setattr(f.instance, field, value)


# a NEW product starts with one blank variant row; an existing product shows only the variants it really has
VariantFormSet = inlineformset_factory(
    Product, ProductVariant, form=VariantForm, formset=BaseVariantFormSet, extra=1, can_delete=True,
)
VariantFormSetEdit = inlineformset_factory(
    Product, ProductVariant, form=VariantForm, formset=BaseVariantFormSet, extra=0, can_delete=True,
)


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["parent", "name", "image", "is_active"]
        widgets = {"parent": forms.Select(attrs={}), "name": forms.TextInput(attrs={})}

    def __init__(self, *args, store=None, **kwargs):
        super().__init__(*args, **kwargs)
        if store is not None:
            self.fields["parent"].queryset = Category.objects.filter(store=store)


class BrandForm(forms.ModelForm):
    class Meta:
        model = Brand
        fields = ["name", "image"]
        widgets = {"name": forms.TextInput(attrs={})}


class UnitForm(forms.ModelForm):
    class Meta:
        model = Unit
        fields = ["name", "short_name", "allow_decimal"]
        widgets = {"name": forms.TextInput(attrs={}), "short_name": forms.TextInput(attrs={})}


class ProductAttributeForm(forms.ModelForm):
    class Meta:
        model = ProductAttribute
        fields = ["name"]
        widgets = {"name": forms.TextInput(attrs={"placeholder": "e.g. Size, Color, Karat"})}


AttributeValueFormSet = inlineformset_factory(
    ProductAttribute,
    ProductAttributeValue,
    fields=["value"],
    extra=1,
    can_delete=True,
    widgets={"value": forms.TextInput(attrs={"placeholder": "e.g. Large"})},
)
