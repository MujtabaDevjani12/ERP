"""Excel import / export of the lists a shop keeps: products, customers, suppliers, categories, brands, expenses, purchases
(and export-only: sales, stock, staff).

* Every file is a plain table - the first sheet, one header row. The headers of an **export** are the same as the headers an **import**
  understands, so an exported file can be edited and imported back (rows are matched by SKU / name / reference and updated).
* Import never writes blindly: each row is checked with the same rules the screens use, rows are saved one by one inside a savepoint,
  bad rows are reported with their row number and the others still import.
* "Check only" (dry run) runs the whole import and rolls everything back, so a file can be checked before it changes anything.
* Everything is limited to the current branch (store).
"""
import logging
import re
from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DecimalField, ExpressionWrapper, F, Q, Sum

from .xlsx import ExcelError, norm_header, number, read_table, text, when, yes_no

log = logging.getLogger("pos.exchange")
EXPORT_CAP = 20000
MONEY = DecimalField(max_digits=16, decimal_places=2)


# ------------------------------------------------------------------ plumbing
@dataclass
class Col:
    key: str
    label: str
    required: bool = False
    help: str = ""
    example: str = ""


def _msg(exc) -> str:
    if isinstance(exc, ValidationError):
        if hasattr(exc, "message_dict"):
            return "; ".join(f"{k.replace('_', ' ').capitalize()}: {' '.join(v)}" for k, v in exc.message_dict.items())
        return " ".join(exc.messages)
    return str(exc)


class Ctx:
    def __init__(self, user, store, dry_run=False):
        self.user, self.store, self.dry_run = user, store, dry_run
        self.created = self.updated = self.skipped = self.failed = 0
        self.errors, self.warnings, self.made, self.cache = [], [], {}, {}

    def fail(self, row, message):
        self.failed += 1
        if len(self.errors) < 200:
            self.errors.append({"row": row, "message": message})

    def warn(self, row, message):
        if len(self.warnings) < 100:
            self.warnings.append({"row": row, "message": message})

    def rollback_row(self, snapshot):
        """A failed row's savepoint is gone, so whatever it created (categories, brands...) is gone too - forget the cached copies."""
        self.made = snapshot
        self.cache.clear()

    def made_one(self, what):
        self.made[what] = self.made.get(what, 0) + 1

    def result(self):
        return {"dry_run": self.dry_run, "created": self.created, "updated": self.updated, "skipped": self.skipped, "failed": self.failed,
                "errors": self.errors, "warnings": self.warnings, "made": sorted(self.made.items()), "errors_truncated": self.failed > len(self.errors)}


class Resource:
    key = label = plural = note = ""
    description = ""
    icon = "table"
    cols: list = []
    extra_aliases: dict = {}
    export_only = False
    view_perm = add_perm = change_perm = None

    # ---- permissions
    def can_export(self, user):
        return bool(self.view_perm and user.has_perm(self.view_perm))

    def can_import(self, user):
        return not self.export_only and bool(self.add_perm and user.has_perm(self.add_perm))

    def can_update(self, user):
        return bool(self.change_perm and user.has_perm(self.change_perm))

    def required_headers(self):
        return [c.key for c in self.cols if c.required]

    def missing_headers(self, headers):
        """Labels of the required columns the file does not have."""
        return [c.label for c in self.cols if c.key in self.required_headers() and c.key not in headers]

    # ---- layout
    def aliases(self):
        a = {norm_header(c.label): c.key for c in self.cols}
        a.update({norm_header(c.key): c.key for c in self.cols})
        a.update({norm_header(k): v for k, v in self.extra_aliases.items()})
        return a

    def export_headers(self, user):
        return [c.label for c in self.cols]

    def export_rows(self, ctx_user, store, params):  # pragma: no cover
        return []

    def export_sheets(self, user, store, params):
        return [{"title": self.plural or self.label, "headers": self.export_headers(user), "rows": list(self.export_rows(user, store, params))}]

    def template_sheets(self):
        return [
            {"title": self.plural or self.label, "headers": [c.label for c in self.cols], "rows": []},
            {"title": "How to fill", "heading": f"{self.label} import",
             "notes": ([self.note] if self.note else []) + [
                 "Fill the first sheet (one row per line). Keep the header row. Extra columns are ignored.",
                 "Columns marked Yes are required. Leave a cell empty to keep the existing value when updating.",
                 "Tip: use “Check only” first - it shows what would happen without saving anything."],
             "headers": ["Column", "Required", "What to enter", "Example"],
             "rows": [[c.label, "Yes" if c.required else "", c.help, c.example] for c in self.cols]},
        ]

    # ---- import
    def process(self, ctx: Ctx, rows):
        for rownum, row in rows:
            self.run_one(ctx, rownum, lambda: self.import_row(ctx, row, rownum))

    def run_one(self, ctx, rownum, fn):
        snap = dict(ctx.made)
        try:
            with transaction.atomic():
                outcome = fn()
        except (ValidationError, ValueError, ArithmeticError) as exc:
            ctx.rollback_row(snap)
            ctx.fail(rownum, _msg(exc))
            return
        except Exception as exc:  # noqa: BLE001 - a bad row must not stop the others
            log.exception("import row %s failed", rownum)
            ctx.rollback_row(snap)
            ctx.fail(rownum, f"Could not import this row ({exc.__class__.__name__}).")
            return
        setattr(ctx, outcome, getattr(ctx, outcome) + 1)

    def import_row(self, ctx, r, rownum) -> str:  # pragma: no cover
        raise NotImplementedError


def _clean_save(obj, **kw):
    obj.full_clean(**kw)
    obj.save()
    return obj


# ------------------------------------------------------------------ shared lookups (all scoped to the branch)
def _warehouse(ctx, value, required=True):
    from tenants.models import Warehouse
    v = text(value)
    qs = Warehouse.objects.filter(store=ctx.store)
    if v:
        wh = qs.filter(Q(code__iexact=v) | Q(name__iexact=v)).first()
        if wh is None:
            raise ValueError(f"Warehouse “{v}” does not exist.")
        return wh
    wh = qs.filter(is_default=True).first() or qs.order_by("pk").first()
    if wh is None and required:
        raise ValueError("Add a warehouse first (Inventory → Warehouses).")
    return wh


def _category(ctx, name, parent=None):
    from catalog.models import Category
    name = text(name)
    if not name:
        return None
    key = ("cat", name.lower(), parent.pk if parent else None)
    if key in ctx.cache:
        return ctx.cache[key]
    obj = Category.objects.filter(store=ctx.store, name__iexact=name, parent=parent).first()
    if obj is None:
        obj = _clean_save(Category(store=ctx.store, name=name[:100], parent=parent), exclude=["image"])
        ctx.made_one("categories")
    ctx.cache[key] = obj
    return obj


def _brand(ctx, name):
    from catalog.models import Brand
    name = text(name)
    if not name:
        return None
    key = ("brand", name.lower())
    if key not in ctx.cache:
        obj = Brand.objects.filter(store=ctx.store, name__iexact=name).first()
        if obj is None:
            obj = _clean_save(Brand(store=ctx.store, name=name[:100]), exclude=["image"])
            ctx.made_one("brands")
        ctx.cache[key] = obj
    return ctx.cache[key]


def _unit(ctx, value):
    from catalog.models import Unit
    v = text(value)
    if v:
        key = ("unit", v.lower())
        if key not in ctx.cache:
            obj = Unit.objects.filter(store=ctx.store).filter(Q(name__iexact=v) | Q(short_name__iexact=v)).first()
            if obj is None:
                obj = _clean_save(Unit(store=ctx.store, name=v[:50], short_name=v[:10]))
                ctx.made_one("units")
            ctx.cache[key] = obj
        return ctx.cache[key]
    obj = Unit.objects.filter(store=ctx.store).order_by("pk").first()
    if obj is None:
        obj = _clean_save(Unit(store=ctx.store, name="Piece", short_name="pc"))
        ctx.made_one("units")
    return obj


def _tax(ctx, value):
    from pricing.models import Tax
    v = text(value)
    if not v:
        return None
    obj = Tax.objects.filter(store=ctx.store, name__iexact=v).first()
    if obj is None:
        raise ValueError(f"Tax “{v}” does not exist. Add it under Catalog → Taxes first.")
    return obj


# =================================================================================================== products
class Products(Resource):
    key, label, plural, icon = "products", "Product", "Products", "cube"
    description = "Products with their variants, prices and opening stock."
    view_perm, add_perm, change_perm = "catalog.view_product", "catalog.add_product", "catalog.change_product"
    cols = [
        Col("sku", "SKU", False, "Product code. An existing SKU updates that product (a variant's SKU or barcode also finds its product). Empty = new product with an automatic SKU.", "TEA-001"),
        Col("name", "Name", True, "Product name.", "Tea 250g"),
        Col("category", "Category", False, "Created automatically when it does not exist yet.", "Grocery"),
        Col("brand", "Brand", False, "Created automatically when it does not exist yet.", "Lipton"),
        Col("unit", "Unit", False, "Unit name or short name (Piece, kg...). Created when missing; empty = your first unit.", "pc"),
        Col("cost", "Cost price", False, "Purchase cost per unit.", "45"),
        Col("price", "Selling price", False, "Selling price per unit. Required for a NEW product.", "60"),
        Col("tax", "Tax", False, "Name of an existing tax (Catalog → Taxes).", "GST"),
        Col("barcode", "Barcode", False, "Barcode of a single product. Empty = automatic.", "8901234567890"),
        Col("alert_quantity", "Low-stock alert", False, "Warn when stock falls to this quantity.", "10"),
        Col("description", "Description", False, "Short description.", ""),
        Col("is_active", "Active", False, "Yes (default) or No.", "Yes"),
        Col("variants", "Variants", False, "name:price:cost entries separated by ; (price and cost optional). A product with variants has no barcode/opening stock here.", "Small:55; Large:75:50"),
        Col("opening_stock", "Opening stock", False, "Quantity on hand, for NEW single products only.", "24"),
        Col("warehouse", "Warehouse", False, "Warehouse name or code for the opening stock (default warehouse if empty).", "Main"),
    ]
    extra_aliases = {"code": "sku", "product": "name", "product_name": "name", "cost_price": "cost", "unit_cost": "cost", "selling_price": "price", "sale_price": "price",
                     "alert": "alert_quantity", "low_stock_alert": "alert_quantity", "qty": "opening_stock", "quantity": "opening_stock", "details": "description"}
    note = "One row per product. Products are matched by SKU - existing ones are updated, new ones created. Variants are added or updated by name; variants not listed are kept."

    def missing_headers(self, headers):
        # a file that only updates existing products needs SKU but not Name; a file of new products needs Name but not SKU
        return [] if ("sku" in headers or "name" in headers) else ["SKU or Name"]

    def export_headers(self, user):
        return [c.label for c in self.cols if c.key not in ("opening_stock", "warehouse")] + ["Stock (info)"]

    def export_rows(self, user, store, params):
        from catalog.models import Product
        from inventory.models import StockItem
        qs = Product.objects.filter(store=store).select_related("category", "brand", "unit", "tax").prefetch_related("variants").order_by("name")
        if str(params.get("category", "")).isdigit():
            qs = qs.filter(category_id=int(params["category"]))
        if params.get("active") in ("0", "1"):
            qs = qs.filter(is_active=params["active"] == "1")
        stock = {r["variant__product_id"]: r["t"] for r in StockItem.objects.filter(warehouse__store=store).values("variant__product_id").annotate(t=Sum("quantity"))}
        for p in qs[:EXPORT_CAP]:
            variants = list(p.variants.all())
            multi = p.product_type == "variant" or len(variants) > 1
            first = variants[0] if variants else None
            text_variants = "; ".join(f"{v.name}:{v.selling_price:f}:{v.cost_price:f}" for v in variants) if multi else ""
            yield [p.sku, p.name, p.category.name if p.category_id else "", p.brand.name if p.brand_id else "", p.unit.name, first.cost_price if first else "",
                   first.selling_price if first else "", p.tax.name if p.tax_id else "", (first.barcode if first and not multi else ""), p.alert_quantity, p.description,
                   p.is_active, text_variants, stock.get(p.pk, 0)]

    # ---- import
    def _variants(self, raw):
        out, seen = [], set()
        for part in re.split(r"[;|\n]", text(raw)):
            part = part.strip()
            if not part:
                continue
            bits = [b.strip() for b in part.split(":")]
            name = bits[0]
            if not name or len(name) > 120:
                raise ValueError(f"Variant name “{name[:30]}” is empty or too long.")
            if name.lower() in seen:
                raise ValueError(f"Variant “{name}” is listed twice.")
            seen.add(name.lower())
            price = number(bits[1], f"Price of variant {name}", minimum=0) if len(bits) > 1 and bits[1] else None
            cost = number(bits[2], f"Cost of variant {name}", minimum=0) if len(bits) > 2 and bits[2] else None
            out.append({"name": name, "price": price, "cost": cost})
        return out

    def _find(self, store, code):
        from catalog.models import Product, ProductVariant
        if not code:
            return None
        p = Product.objects.filter(store=store, sku__iexact=code).first()
        if p:
            return p
        v = ProductVariant.objects.filter(product__store=store).filter(Q(sku__iexact=code) | Q(barcode=code)).select_related("product").first()
        return v.product if v else None

    def _new_sku(self, store, name):
        from catalog.models import Product
        from django.utils.text import slugify
        base = (slugify(name).upper().replace("-", "")[:12] or "ITEM")
        sku, n = base, 1
        while Product.objects.filter(store=store, sku__iexact=sku).exists():
            n += 1
            sku = f"{base}{n}"
        return sku

    def import_row(self, ctx, r, rownum):
        from catalog.models import Product, ProductVariant
        from catalog.views import _variant_sku
        from inventory.models import StockItem

        code, name = text(r.get("sku")), text(r.get("name"))
        product = self._find(ctx.store, code)
        if product is None and not ctx.user.has_perm(self.add_perm):
            raise ValueError("You are not allowed to add products.")
        if product is not None and not ctx.user.has_perm(self.change_perm):
            raise ValueError(f"Product {code} exists and you are not allowed to edit products.")
        if product is None and not name:
            raise ValueError("Name is required.")
        cost = number(r.get("cost"), "Cost price", minimum=Decimal("0"))
        price = number(r.get("price"), "Selling price", minimum=Decimal("0"))
        alert = number(r.get("alert_quantity"), "Low-stock alert", minimum=Decimal("0"))
        active = yes_no(r.get("is_active"))
        barcode = text(r.get("barcode"))
        variants = self._variants(r.get("variants")) if text(r.get("variants")) else []
        category = _category(ctx, r.get("category"))
        brand = _brand(ctx, r.get("brand"))
        tax = _tax(ctx, r.get("tax"))
        qty = number(r.get("opening_stock"), "Opening stock", minimum=Decimal("0"))
        if variants and barcode:
            raise ValueError("A product with variants cannot have a single barcode. Leave Barcode empty.")
        if barcode:
            clash = ProductVariant.objects.filter(barcode=barcode)
            if product is not None:
                clash = clash.exclude(product=product)
            if clash.exists():
                raise ValueError(f"Barcode {barcode} is already used by another product.")

        if product is None:
            if price is None and not any(v["price"] is not None for v in variants):
                raise ValueError("Selling price is required for a new product.")
            if qty and variants:
                raise ValueError("Opening stock cannot be combined with variants. Add the variants, then receive stock with a purchase.")
            sku = code[:60] if code else self._new_sku(ctx.store, name)
            product = Product(store=ctx.store, name=name[:180], sku=sku, unit=_unit(ctx, r.get("unit")), category=category, brand=brand, tax=tax,
                              description=text(r.get("description")), alert_quantity=alert or Decimal("0"), is_active=True if active is None else active,
                              product_type="variant" if variants else "single")
            product.full_clean(exclude=["image"])
            product._skip_default_variant = True
            product.save()
            default_price = price if price is not None else next(v["price"] for v in variants if v["price"] is not None)
            default_cost = cost if cost is not None else Decimal("0")
            made = []
            for v in (variants or [{"name": "Default", "price": None, "cost": None}]):
                variant = ProductVariant(product=product, name=v["name"][:120], selling_price=v["price"] if v["price"] is not None else default_price,
                                         cost_price=v["cost"] if v["cost"] is not None else default_cost, is_active=product.is_active,
                                         barcode=barcode if (barcode and not variants) else "")
                variant.sku = _variant_sku(product, variant.name)
                variant.full_clean(exclude=["image", "barcode"])
                variant.save()
                made.append(variant)
            if qty and not variants:
                wh = _warehouse(ctx, r.get("warehouse"))
                item, _ = StockItem.objects.get_or_create(variant=made[0], warehouse=wh)
                item.adjust(qty, reference_type="OPENING_STOCK", reference_no=product.sku, user=ctx.user, remarks="Opening stock (Excel import)", unit_cost=made[0].cost_price)
            return "created"

        # ---- existing product: only the cells that were filled in are changed
        if name:
            product.name = name[:180]
        if category is not None:
            product.category = category
        if brand is not None:
            product.brand = brand
        if tax is not None:
            product.tax = tax
        if text(r.get("unit")):
            product.unit = _unit(ctx, r.get("unit"))
        if text(r.get("description")):
            product.description = text(r.get("description"))
        if alert is not None:
            product.alert_quantity = alert
        if active is not None:
            product.is_active = active
        product.full_clean(exclude=["image"])
        product.save()
        existing = list(product.variants.all())
        if variants:
            if product.product_type != "variant" and len(existing) <= 1:
                raise ValueError(f"Product {product.sku} is a single product. Open it in Products and switch on “This product has variants” first.")
            by_name = {v.name.lower(): v for v in existing}
            for v in variants:
                row = by_name.get(v["name"].lower())
                if row is None:
                    row = ProductVariant(product=product, name=v["name"][:120], selling_price=v["price"] if v["price"] is not None else (price or existing[0].selling_price),
                                         cost_price=v["cost"] if v["cost"] is not None else (cost if cost is not None else existing[0].cost_price), is_active=product.is_active)
                    row.sku = _variant_sku(product, row.name)
                else:
                    if v["price"] is not None:
                        row.selling_price = v["price"]
                    if v["cost"] is not None:
                        row.cost_price = v["cost"]
                row.full_clean(exclude=["image", "barcode"])
                row.save()
        else:
            if len(existing) == 1:
                row, changed = existing[0], False
                if price is not None:
                    row.selling_price, changed = price, True
                if cost is not None:
                    row.cost_price, changed = cost, True
                if barcode:
                    row.barcode, changed = barcode, True
                if active is not None:
                    row.is_active, changed = active, True
                if changed:
                    row.full_clean(exclude=["image"])
                    row.save()
            if len(existing) > 1 and (price is not None or cost is not None or barcode):
                ctx.warn(rownum, f"{product.sku} has several variants - price, cost and barcode were not changed. List the variants in the Variants column to update their prices.")
        if qty:
            ctx.warn(rownum, f"Opening stock ignored for existing product {product.sku}. Use Inventory → Quick scan adjust to change stock.")
        return "updated"


# =================================================================================================== customers / suppliers
class Customers(Resource):
    key, label, plural, icon = "customers", "Customer", "Customers", "users"
    description = "Customers with phone, address and price group."
    view_perm, add_perm, change_perm = "customers.view_customer", "customers.add_customer", "customers.change_customer"
    cols = [
        Col("name", "Name", True, "Customer name.", "Ali Khan"),
        Col("phone", "Phone", False, "Used to find an existing customer when updating.", "0300-1234567"),
        Col("email", "Email", False, "", "ali@example.com"),
        Col("cnic", "CNIC", False, "Format 35201-1234567-1.", "35201-1234567-1"),
        Col("address", "Address", False, "", "Lahore"),
        Col("group", "Price group", False, "Name of an existing customer price group.", "Wholesale"),
        Col("is_active", "Active", False, "Yes (default) or No.", "Yes"),
    ]
    extra_aliases = {"customer": "name", "customer_name": "name", "mobile": "phone", "cell": "phone", "customer_group": "group"}
    note = "Customers are matched by phone (or by email, or by exact name). Existing ones are updated."

    def export_rows(self, user, store, params):
        from customers.models import Customer
        for c in Customer.objects.filter(store=store).select_related("group").order_by("name")[:EXPORT_CAP]:
            yield [c.name, c.phone, c.email, c.cnic, c.address, c.group.name if c.group_id else "", c.is_active]

    def _find(self, ctx, name, phone, email):
        from customers.models import Customer
        qs = Customer.objects.filter(store=ctx.store)
        return ((phone and qs.filter(phone=phone).first()) or (email and qs.filter(email__iexact=email).first()) or qs.filter(name__iexact=name).first())

    def import_row(self, ctx, r, rownum):
        from customers.models import Customer
        from pricing.models import CustomerGroup
        name, phone, email = text(r.get("name")), text(r.get("phone")), text(r.get("email"))
        if not name:
            raise ValueError("Name is required.")
        obj = self._find(ctx, name, phone, email)
        if obj is None and not ctx.user.has_perm(self.add_perm):
            raise ValueError("You are not allowed to add customers.")
        if obj is not None and not ctx.user.has_perm(self.change_perm):
            raise ValueError(f"{obj.name} already exists and you are not allowed to edit customers.")
        new = obj is None
        obj = obj or Customer(store=ctx.store)
        obj.name = name[:150]
        for attr in ("phone", "email", "cnic", "address"):
            v = text(r.get(attr))
            if v:
                setattr(obj, attr, v)
        g = text(r.get("group"))
        if g:
            grp = CustomerGroup.objects.filter(store=ctx.store, name__iexact=g).first()
            if grp is None:
                raise ValueError(f"Price group “{g}” does not exist.")
            obj.group = grp
        active = yes_no(r.get("is_active"))
        if active is not None:
            obj.is_active = active
        _clean_save(obj)
        return "created" if new else "updated"


class Suppliers(Resource):
    key, label, plural, icon = "suppliers", "Supplier", "Suppliers", "truck"
    description = "Suppliers with contact details."
    view_perm, add_perm, change_perm = "suppliers.view_supplier", "suppliers.add_supplier", "suppliers.change_supplier"
    cols = [
        Col("name", "Name", True, "Supplier / company name. Used to find an existing supplier when updating.", "Acme Traders"),
        Col("contact_person", "Contact person", False, "", "Bilal"),
        Col("phone", "Phone", False, "", "0300-1234567"),
        Col("email", "Email", False, "", "sales@acme.example"),
        Col("address", "Address", False, "", "Karachi"),
        Col("company", "Company", False, "Registered / trade name, when it differs from the supplier name.", "Acme Traders (Pvt) Ltd"),
        Col("tax_no", "NTN / tax no", False, "", "1234567-8"),
        Col("city", "City", False, "", "Karachi"),
        Col("country", "Country", False, "", "Pakistan"),
        Col("payment_term_days", "Payment terms (days)", False, "Days allowed to pay after delivery; 0 = pay on delivery.", "30"),
        Col("is_active", "Active", False, "Yes (default) or No.", "Yes"),
    ]
    extra_aliases = {"supplier": "name", "supplier_name": "name", "contact": "contact_person", "mobile": "phone", "ntn": "tax_no",
                     "terms": "payment_term_days", "payment_terms": "payment_term_days"}
    note = "Suppliers are matched by name. Existing ones are updated."

    def export_rows(self, user, store, params):
        from suppliers.models import Supplier
        for s in Supplier.objects.filter(store=store).order_by("name")[:EXPORT_CAP]:
            yield [s.name, s.contact_person, s.phone, s.email, s.address, s.company, s.tax_no, s.city, s.country, s.payment_term_days, s.is_active]

    def import_row(self, ctx, r, rownum):
        from suppliers.models import Supplier
        name = text(r.get("name"))
        if not name:
            raise ValueError("Name is required.")
        obj = Supplier.objects.filter(store=ctx.store, name__iexact=name).first()
        if obj is None and not ctx.user.has_perm(self.add_perm):
            raise ValueError("You are not allowed to add suppliers.")
        if obj is not None and not ctx.user.has_perm(self.change_perm):
            raise ValueError(f"{obj.name} already exists and you are not allowed to edit suppliers.")
        new = obj is None
        obj = obj or Supplier(store=ctx.store, name=name[:150])
        for attr in ("contact_person", "phone", "email", "address", "company", "tax_no", "city", "country"):
            v = text(r.get(attr))
            if v:
                setattr(obj, attr, v)
        term = number(r.get("payment_term_days"), "Payment terms", minimum=Decimal("0"))
        if term is not None:
            obj.payment_term_days = min(int(term), 365)
        active = yes_no(r.get("is_active"))
        if active is not None:
            obj.is_active = active
        _clean_save(obj)
        return "created" if new else "updated"


# =================================================================================================== categories / brands
class Categories(Resource):
    key, label, plural, icon = "categories", "Category", "Categories", "tag"
    description = "Product categories (with an optional parent)."
    view_perm, add_perm, change_perm = "catalog.view_category", "catalog.add_category", "catalog.change_category"
    cols = [
        Col("name", "Name", True, "Category name.", "Soft drinks"),
        Col("parent", "Parent category", False, "Created automatically when it does not exist yet.", "Drinks"),
        Col("is_active", "Active", False, "Yes (default) or No.", "Yes"),
    ]
    extra_aliases = {"category": "name", "parent_category": "parent"}
    note = "A category is found by name and parent. Existing ones are updated (Active)."

    def export_rows(self, user, store, params):
        from catalog.models import Category
        for c in Category.objects.filter(store=store).select_related("parent").order_by("name")[:EXPORT_CAP]:
            yield [c.name, c.parent.name if c.parent_id else "", c.is_active]

    def import_row(self, ctx, r, rownum):
        from catalog.models import Category
        name = text(r.get("name"))
        if not name:
            raise ValueError("Name is required.")
        parent = _category(ctx, r.get("parent"))
        obj = Category.objects.filter(store=ctx.store, name__iexact=name, parent=parent).first()
        active = yes_no(r.get("is_active"))
        if obj is None:
            if not ctx.user.has_perm(self.add_perm):
                raise ValueError("You are not allowed to add categories.")
            _clean_save(Category(store=ctx.store, name=name[:100], parent=parent, is_active=True if active is None else active), exclude=["image"])
            return "created"
        if active is None or active == obj.is_active:
            return "skipped"
        if not ctx.user.has_perm(self.change_perm):
            raise ValueError(f"{obj.name} already exists and you are not allowed to edit categories.")
        obj.is_active = active
        obj.save(update_fields=["is_active", "updated_at"])
        return "updated"


class Brands(Resource):
    key, label, plural, icon = "brands", "Brand", "Brands", "tag"
    description = "Product brands."
    view_perm, add_perm, change_perm = "catalog.view_brand", "catalog.add_brand", None
    cols = [Col("name", "Name", True, "Brand name. Existing brands are skipped.", "Lipton")]
    extra_aliases = {"brand": "name", "brand_name": "name"}

    def export_rows(self, user, store, params):
        from catalog.models import Brand
        for b in Brand.objects.filter(store=store).order_by("name")[:EXPORT_CAP]:
            yield [b.name]

    def import_row(self, ctx, r, rownum):
        from catalog.models import Brand
        name = text(r.get("name"))
        if not name:
            raise ValueError("Name is required.")
        if Brand.objects.filter(store=ctx.store, name__iexact=name).exists():
            return "skipped"
        _clean_save(Brand(store=ctx.store, name=name[:100]), exclude=["image"])
        return "created"


# =================================================================================================== expenses
class Expenses(Resource):
    key, label, plural, icon = "expenses", "Expense", "Expenses", "cash"
    description = "Expenses by date, category and payment method."
    view_perm, add_perm, change_perm = "expenses.view_expense", "expenses.add_expense", None
    cols = [
        Col("date", "Date", True, "YYYY-MM-DD.", "2026-10-01"),
        Col("category", "Category", True, "Created automatically when it does not exist yet.", "Rent"),
        Col("amount", "Amount", True, "Amount paid.", "25000"),
        Col("method", "Method", False, "cash (default), card, bank_transfer...", "cash"),
        Col("paid_to", "Paid to", False, "", "Landlord"),
        Col("note", "Note", False, "", "October rent"),
        Col("reference", "Reference", False, "Leave empty for an automatic number. A reference that already exists is skipped.", ""),
    ]
    extra_aliases = {"expense_date": "date", "payee": "paid_to", "payment_method": "method", "description": "note", "expense_category": "category"}
    note = "Each row is a new expense. A row whose reference already exists is skipped, so importing the same file twice is safe."

    def export_headers(self, user):
        return [c.label for c in self.cols[:6]] + ["Reference", "Entered by"]

    def export_rows(self, user, store, params):
        from expenses.models import Expense
        qs = Expense.objects.filter(store=store).select_related("category", "created_by").order_by("-expense_date", "-id")
        if params.get("from"):
            qs = qs.filter(expense_date__gte=params["from"])
        if params.get("to"):
            qs = qs.filter(expense_date__lte=params["to"])
        for e in qs[:EXPORT_CAP]:
            yield [e.expense_date, e.category.name, e.amount, e.method, e.paid_to, e.note, e.reference, (e.created_by.get_full_name() or e.created_by.username) if e.created_by_id else ""]

    def import_row(self, ctx, r, rownum):
        from configuration.models import NumberingPrefix
        from expenses.models import Expense, ExpenseCategory
        from payments.models import METHOD_CHOICES
        d = when(r.get("date"), "Date")
        if d is None:
            raise ValueError("Date is required.")
        amount = number(r.get("amount"), "Amount", minimum=Decimal("0.01"))
        if amount is None:
            raise ValueError("Amount is required.")
        cat_name = text(r.get("category"))
        if not cat_name:
            raise ValueError("Category is required.")
        method = text(r.get("method")).lower().replace(" ", "_") or "cash"
        if method not in dict(METHOD_CHOICES):
            raise ValueError(f"Method “{method}” must be one of: {', '.join(dict(METHOD_CHOICES))}.")
        ref = text(r.get("reference"))
        if ref and Expense.objects.filter(reference__iexact=ref).exists():
            return "skipped"
        key = ("expcat", cat_name.lower())
        if key not in ctx.cache:
            cat = ExpenseCategory.objects.filter(store=ctx.store, name__iexact=cat_name).first()
            if cat is None:
                cat = _clean_save(ExpenseCategory(store=ctx.store, name=cat_name[:100]))
                ctx.made_one("expense categories")
            ctx.cache[key] = cat
        obj = Expense(store=ctx.store, category=ctx.cache[key], amount=amount, method=method, paid_to=text(r.get("paid_to"))[:150], expense_date=d.date(),
                      note=text(r.get("note")), reference=ref[:30] or NumberingPrefix.next_reference(NumberingPrefix.EXPENSE), created_by=ctx.user)
        obj.full_clean()
        obj.save()
        return "created"


# =================================================================================================== purchases
class Purchases(Resource):
    key, label, plural, icon = "purchases", "Purchase", "Purchases", "truck"
    description = "Purchase orders, one row per product line."
    view_perm, add_perm, change_perm = "suppliers.view_purchaseorder", "suppliers.add_purchaseorder", None
    cols = [
        Col("reference", "Reference", False, "Rows with the same reference form ONE purchase. Empty = a new purchase per row. A reference that already exists is skipped.", "PO-0001"),
        Col("date", "Date", True, "YYYY-MM-DD.", "2026-10-01"),
        Col("supplier", "Supplier", True, "Created automatically when it does not exist yet.", "Acme Traders"),
        Col("warehouse", "Warehouse", False, "Warehouse name or code (default warehouse if empty).", "Main"),
        Col("status", "Status", False, "received (default - adds the goods to stock), ordered or draft.", "received"),
        Col("sku", "Product SKU", True, "SKU or barcode of an existing product variant.", "TEA-001-DEF"),
        Col("quantity", "Quantity", True, "Quantity bought.", "24"),
        Col("cost", "Unit cost", True, "Cost per unit.", "45"),
        Col("note", "Note", False, "Note for the whole purchase (first row of the reference is used).", ""),
    ]
    extra_aliases = {"purchase_date": "date", "order_date": "date", "product": "sku", "code": "sku", "variant_sku": "sku", "qty": "quantity", "cost_price": "cost", "unit_price": "cost",
                     "supplier_name": "supplier", "ref": "reference"}
    note = "Purchases are created unpaid - record the payments on the purchase page. Purchases marked received add their goods to stock."

    def export_headers(self, user):
        return [c.label for c in self.cols] + ["Total (info)"]

    def export_rows(self, user, store, params):
        from suppliers.models import PurchaseOrder
        qs = PurchaseOrder.objects.filter(warehouse__store=store).select_related("supplier", "warehouse").prefetch_related("items__variant").order_by("-order_date", "-id")
        if params.get("from"):
            qs = qs.filter(order_date__gte=params["from"])
        if params.get("to"):
            qs = qs.filter(order_date__lte=params["to"])
        n = 0
        for po in qs[:5000]:
            for it in po.items.all():
                n += 1
                if n > EXPORT_CAP:
                    return
                yield [po.reference, po.order_date, po.supplier.name, po.warehouse.name, po.status, it.variant.sku, it.quantity, it.cost_price, po.note, it.quantity * it.cost_price]

    # grouped import: all the lines of one reference become one purchase
    def process(self, ctx, rows):
        groups, order = {}, []
        for rownum, row in rows:
            ref = text(row.get("reference"))
            gk = ref.lower() if ref else f"__row{rownum}"
            if gk not in groups:
                groups[gk] = []
                order.append(gk)
            groups[gk].append((rownum, row))
        for gk in order:
            lines = groups[gk]
            self.run_one(ctx, lines[0][0], lambda lines=lines: self.import_group(ctx, lines))

    def import_group(self, ctx, lines):
        from configuration.models import NumberingPrefix
        from suppliers.models import PurchaseOrder, PurchaseOrderItem, Supplier
        from suppliers.views import _receive_stock
        from catalog.models import ProductVariant

        first_no, first = lines[0]
        ref = text(first.get("reference"))
        if ref and PurchaseOrder.objects.filter(reference__iexact=ref).exists():
            return "skipped"
        d = when(first.get("date"), "Date")
        if d is None:
            raise ValueError("Date is required.")
        sup_name = text(first.get("supplier"))
        if not sup_name:
            raise ValueError("Supplier is required.")
        status = text(first.get("status")).lower() or "received"
        if status not in ("received", "ordered", "draft"):
            raise ValueError(f"Status “{status}” must be received, ordered or draft.")
        supplier = Supplier.objects.filter(store=ctx.store, name__iexact=sup_name).first()
        if supplier is None:
            supplier = _clean_save(Supplier(store=ctx.store, name=sup_name[:150]))
            ctx.made_one("suppliers")
        wh = _warehouse(ctx, first.get("warehouse"))
        items, seen = [], set()
        for rownum, row in lines:
            code = text(row.get("sku"))
            if not code:
                raise ValueError(f"Row {rownum}: Product SKU is required.")
            variant = ProductVariant.objects.filter(product__store=ctx.store).filter(Q(sku__iexact=code) | Q(barcode=code) | Q(product__sku__iexact=code)).order_by("pk").first()
            if variant is None:
                raise ValueError(f"Row {rownum}: product “{code}” does not exist. Import the products first.")
            qty = number(row.get("quantity"), "Quantity", minimum=Decimal("0.001"))
            cost = number(row.get("cost"), "Unit cost", minimum=Decimal("0"))
            if qty is None or cost is None:
                raise ValueError(f"Row {rownum}: Quantity and Unit cost are required.")
            if variant.pk in seen:
                raise ValueError(f"Row {rownum}: {code} appears twice in the same purchase. Combine the quantities.")
            seen.add(variant.pk)
            items.append((variant, qty, cost))
        po = PurchaseOrder(reference=ref[:30] or NumberingPrefix.next_reference(NumberingPrefix.PURCHASE), supplier=supplier, warehouse=wh, status=status,
                           order_date=d.date(), note=text(first.get("note")), created_by=ctx.user)
        po.full_clean()
        po.save()
        for variant, qty, cost in items:
            PurchaseOrderItem.objects.create(purchase_order=po, variant=variant, quantity=qty, cost_price=cost)
        if status == "received":
            _receive_stock(po, user=ctx.user)
        return "created"


# =================================================================================================== export only
def _range(qs, params, field):
    if params.get("from"):
        qs = qs.filter(**{f"{field}__date__gte": params["from"]})
    if params.get("to"):
        qs = qs.filter(**{f"{field}__date__lte": params["to"]})
    return qs


class Sales(Resource):
    key, label, plural, icon = "sales", "Sale", "Sales", "receipt"
    description = "Every sale with totals, paid and due. Export only."
    export_only = True
    view_perm = "sales.view_sale"
    cols = [Col(k, k) for k in ("Reference", "Date", "Customer", "Biller", "Status", "Payment status", "Subtotal", "Discount", "Tax", "Shipping", "Total", "Paid", "Due", "Register")]

    def export_rows(self, user, store, params):
        from sales.models import Sale
        qs = _range(Sale.objects.filter(store=store).select_related("customer", "biller", "register_session__register").prefetch_related("payments"), params, "created_at").order_by("-created_at")
        for s in qs[:EXPORT_CAP]:
            paid = s.paid_amount
            yield [s.reference, s.created_at, s.customer.name if s.customer_id else "Walk-in", s.biller.get_full_name() or s.biller.username, s.get_status_display(), s.payment_status,
                   s.subtotal, s.discount_amount, s.tax_amount, s.shipping_amount, s.total_amount, paid, s.total_amount - paid,
                   s.register_session.register.name if s.register_session_id else ""]


class Stock(Resource):
    key, label, plural, icon = "stock", "Stock", "Stock", "archive"
    description = "Quantity on hand per product and warehouse, with value. Export only."
    export_only = True
    view_perm = "inventory.view_stockitem"
    cols = [Col(k, k) for k in ("SKU", "Product", "Variant", "Barcode", "Category", "Brand", "Warehouse", "On hand", "Low-stock alert", "Unit cost", "Value at cost", "Selling price")]

    def export_rows(self, user, store, params):
        from inventory.models import StockItem
        qs = StockItem.objects.filter(warehouse__store=store).select_related("variant__product__category", "variant__product__brand", "warehouse").order_by("variant__product__name", "warehouse__name")
        if str(params.get("warehouse", "")).isdigit():
            qs = qs.filter(warehouse_id=int(params["warehouse"]))
        for s in qs[:EXPORT_CAP]:
            v, p = s.variant, s.variant.product
            yield [v.sku, p.name, v.name, v.barcode, p.category.name if p.category_id else "", p.brand.name if p.brand_id else "", s.warehouse.name, s.quantity, p.alert_quantity,
                   v.cost_price, s.quantity * v.cost_price, v.selling_price]


class StaffUsers(Resource):
    key, label, plural, icon = "staff", "Staff user", "Staff", "user"
    description = "Staff accounts with roles and branch. Export only - passwords are never exported."
    export_only = True
    view_perm = "accounts.view_user"
    cols = [Col(k, k) for k in ("Username", "Name", "Email", "Phone", "Roles", "Biller", "Active", "Last login")]

    def export_rows(self, user, store, params):
        from django.contrib.auth import get_user_model
        for u in get_user_model().objects.filter(Q(store=store) | Q(store__isnull=True)).prefetch_related("groups").order_by("username")[:EXPORT_CAP]:
            yield [u.username, u.get_full_name(), u.email, u.phone, ", ".join(g.name for g in u.groups.all()) or ("Owner" if u.is_superuser else ""), u.is_biller, u.is_active, u.last_login]


REGISTRY = {r.key: r for r in (Products(), Customers(), Suppliers(), Categories(), Brands(), Expenses(), Purchases(), Sales(), Stock(), StaffUsers())}


def get(key):
    return REGISTRY.get(key)


def run_import(resource, user, store, upload, dry_run=False):
    headers, rows = read_table(upload, resource.aliases())
    missing = resource.missing_headers(headers)
    if missing:
        raise ExcelError(f"The file is missing the column(s): {', '.join(missing)}. Download the template to see the layout.")
    if not rows:
        raise ExcelError("The file has a header row but no data rows.")
    ctx = Ctx(user, store, dry_run)
    with transaction.atomic():
        resource.process(ctx, rows)
        if dry_run:
            transaction.set_rollback(True)
    return ctx.result()
