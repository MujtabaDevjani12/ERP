"""Server-side pricing of a POS cart - the ONE place that turns lines + discounts + tax rates into money.
Used by the checkout (to write the sale) and by the quote endpoint (so the totals the cashier sees on screen are
exactly the totals the sale will be saved with, to the cent)."""
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

from catalog.models import ProductVariant

TWO = Decimal("0.01")
HUNDRED = Decimal("100")


class CartError(Exception):
    """A cart/checkout problem the cashier can fix. `code` lets the POS screen react (out_of_stock / stock_changed ...)."""

    def __init__(self, message, code="invalid", **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


def money(value) -> Decimal:
    return Decimal(value).quantize(TWO, rounding=ROUND_HALF_UP)


def to_decimal(value, what, *, minimum=None, positive=False):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise CartError(f"{what} is not a valid number.")
    if not number.is_finite():
        raise CartError(f"{what} is not a valid number.")
    if positive and number <= 0:
        raise CartError(f"{what} must be greater than zero.")
    if minimum is not None and number < minimum:
        raise CartError(f"{what} cannot be below {minimum}.")
    return number


def rounding_mode(store):
    """The branch's 'Rounding' choice from Settings > POS behaviour (none / nearest_1 / nearest_5 / nearest_10 / up / down)."""
    from configuration.models import POSConfiguration
    conf = POSConfiguration.objects.filter(store=store).only("rounding").first()
    return conf.rounding if conf else "none"


def round_total(total, mode):
    """-> (rounded total, the adjustment that was made). 'up'/'down' go to the next whole unit."""
    if mode in ("nearest_1", "nearest_5", "nearest_10"):
        step = Decimal(mode.split("_")[1])
        rounded = (total / step).quantize(Decimal(1), rounding=ROUND_HALF_UP) * step
    elif mode == "up":
        rounded = total.quantize(Decimal(1), rounding=ROUND_CEILING)
    elif mode == "down":
        rounded = total.quantize(Decimal(1), rounding=ROUND_FLOOR)
    else:
        return total, Decimal("0.00")
    rounded = money(rounded)
    return rounded, rounded - total


def price_cart(store, items, order_discount=None, shipping=0):
    """Validates every line and prices the cart.
    items: [{variant_id, quantity, unit_price?, discount_amount?}], order_discount: {type: 'amount'|'percent', value}.
    shipping: a delivery charge added on top (never discounted or taxed). The total is then rounded as the branch configured.
    Returns {rows, subtotal, discount, tax, shipping, rounding, total, overrides}; each row has variant, quantity, unit_price, discount
    (line + share of the order discount), subtotal (before discount), tax and line_total."""
    rows, overrides = [], []
    for row in items:
        if not isinstance(row, dict) or "variant_id" not in row:
            raise CartError("Cart contains an invalid item.")
        try:
            variant = ProductVariant.objects.select_related("product__tax").get(
                pk=row["variant_id"], product__store=store, is_active=True, product__is_active=True)
        except (ProductVariant.DoesNotExist, ValueError, TypeError):
            raise CartError("A product in the cart is no longer available. Please review your cart.", "stock_changed")
        quantity = to_decimal(row.get("quantity"), f"Quantity of '{variant}'", positive=True)
        unit_price = money(to_decimal(row.get("unit_price", variant.current_price), f"Price of '{variant}'", minimum=Decimal("0")))
        discount = money(to_decimal(row.get("discount_amount", 0), f"Discount on '{variant}'", minimum=Decimal("0")))
        line_subtotal = money(quantity * unit_price)
        if discount > line_subtotal:
            raise CartError(f"Discount on '{variant}' is more than the line total.")
        if unit_price != variant.current_price:
            overrides.append(f"{variant.sku}: {variant.current_price} -> {unit_price}")
        rows.append({"variant": variant, "quantity": quantity, "unit_price": unit_price, "discount": discount, "subtotal": line_subtotal})

    # order-level discount is spread over the lines so tax and reports stay line-accurate
    od = order_discount if isinstance(order_discount, dict) else {}
    try:
        od_value = Decimal(str(od.get("value") or 0))
    except InvalidOperation:
        raise CartError("Discount is not a valid number.")
    if od and od_value > 0:
        base = sum((r["subtotal"] - r["discount"] for r in rows), Decimal("0"))
        value = to_decimal(od_value, "Discount", minimum=Decimal("0"))
        amount = money(base * min(value, HUNDRED) / HUNDRED) if od.get("type") == "percent" else money(value)
        if amount > base:
            raise CartError("The discount is more than the bill total.")
        left = amount
        for i, r in enumerate(rows):
            room = r["subtotal"] - r["discount"]
            share = left if i == len(rows) - 1 else min(money(amount * room / base) if base else Decimal("0"), left)
            share = min(share, room)
            r["discount"] += share
            left -= share

    for r in rows:
        rate = r["variant"].product.tax.rate if r["variant"].product.tax else Decimal("0")
        r["tax"] = money((r["subtotal"] - r["discount"]) * rate / HUNDRED)
        r["line_total"] = r["subtotal"] - r["discount"] + r["tax"]
    subtotal = sum((r["subtotal"] for r in rows), Decimal("0"))
    discount = sum((r["discount"] for r in rows), Decimal("0"))
    tax = sum((r["tax"] for r in rows), Decimal("0"))
    shipping = money(to_decimal(shipping or 0, "Shipping charge", minimum=Decimal("0")))
    if shipping > Decimal("10000000"):
        raise CartError("The shipping charge is too large.")
    total, rounding = round_total(subtotal - discount + tax + shipping, rounding_mode(store))
    return {"rows": rows, "subtotal": subtotal, "discount": discount, "tax": tax, "shipping": shipping, "rounding": rounding, "total": total, "overrides": overrides}
