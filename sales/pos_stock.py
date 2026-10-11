"""Stock helpers for the POS screen. Stock lives per variant per warehouse (inventory.StockItem); the POS sells from the
warehouse of the counter that is open, so that is the quantity every POS check uses."""
from decimal import Decimal

from django.db.models import Exists, F, OuterRef, Q, Subquery
from django.db.models.functions import Coalesce

from inventory.models import StockItem

ZERO = Decimal("0")
UNLIMITED = Decimal("999999")          # shown for services / digital items, which have no shelf to run out of
OUT_OF_STOCK = "Product is currently out of stock."
STOCK_CHANGED = "Stock changed. Please review your cart."


def annotate_pos_stock(variants, warehouse):
    """Adds `.pos_stock` (Decimal quantity in the counter's warehouse, 0 when there is no stock row) to a ProductVariant queryset."""
    qty = StockItem.objects.filter(variant=OuterRef("pk"), warehouse=warehouse).values("quantity")[:1]
    return variants.annotate(pos_stock=Subquery(qty))


def only_in_stock(variants, warehouse):
    """Variants that can be sold from this warehouse: standard goods with stock above zero; services and digital items always;
    combos when every component is on the shelf in the quantity the combo needs."""
    from catalog.models import ComboItem, Product
    have = Coalesce(Subquery(StockItem.objects.filter(variant=OuterRef("component_id"), warehouse=warehouse).values("quantity")[:1]), ZERO)
    short = ComboItem.objects.annotate(have=have).filter(combo=OuterRef("product_id"), have__lt=F("quantity"))
    has_parts = ComboItem.objects.filter(combo=OuterRef("product_id"))
    in_stock = Exists(StockItem.objects.filter(variant=OuterRef("pk"), warehouse=warehouse, quantity__gt=0))
    return variants.filter(
        Q(product__item_type__in=[Product.ITEM_SERVICE, Product.ITEM_DIGITAL])
        | (Q(product__item_type=Product.ITEM_STANDARD) & in_stock)
        | (Q(product__item_type=Product.ITEM_COMBO) & Exists(has_parts) & ~Exists(short)))


def stock_map(warehouse, variant_ids):
    """{variant_id: quantity that can be sold} for the given variants in this warehouse (missing rows count as 0).
    Services / digital items are UNLIMITED; a combo is how many whole bundles its components allow."""
    from catalog.models import ComboItem, Product, ProductVariant
    out = {int(v): ZERO for v in variant_ids}
    for vid, qty in StockItem.objects.filter(warehouse=warehouse, variant_id__in=list(out)).values_list("variant_id", "quantity"):
        out[vid] = qty
    special = dict(ProductVariant.objects.filter(pk__in=list(out)).exclude(product__item_type=Product.ITEM_STANDARD)
                   .values_list("pk", "product__item_type"))
    if not special:
        return out
    combos = [vid for vid, kind in special.items() if kind == Product.ITEM_COMBO]
    parts = {}
    if combos:
        for it in ComboItem.objects.filter(combo__variants__in=combos).values("combo__variants__id", "component_id", "quantity"):
            parts.setdefault(it["combo__variants__id"], []).append((it["component_id"], it["quantity"]))
        comp_stock = {}
        ids = {c for rows in parts.values() for c, _ in rows}
        for cid, qty in StockItem.objects.filter(warehouse=warehouse, variant_id__in=ids).values_list("variant_id", "quantity"):
            comp_stock[cid] = qty
    for vid, kind in special.items():
        if kind == Product.ITEM_COMBO:
            rows = parts.get(vid)
            out[vid] = min((comp_stock.get(c, ZERO) / q for c, q in rows), default=ZERO) if rows else ZERO
            out[vid] = max(out[vid].quantize(Decimal("0.001"), rounding="ROUND_DOWN"), ZERO)
        else:
            out[vid] = UNLIMITED
    return out


def stock_effects(variant, quantity):
    """What leaves (or returns to) the shelf when `quantity` of `variant` is sold: [(variant, quantity), ...].
    Nothing for a service / digital item; the components for a combo; the item itself otherwise."""
    product = variant.product
    if product.item_type in (product.ITEM_SERVICE, product.ITEM_DIGITAL):
        return []
    if product.item_type == product.ITEM_COMBO:
        return [(ci.component, quantity * ci.quantity) for ci in product.combo_items.select_related("component__product")]
    return [(variant, quantity)]
