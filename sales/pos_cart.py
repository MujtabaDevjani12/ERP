"""Server-side POS cart (one per open register session = per counter).

The browser keeps a backup copy too, but this is the copy that survives a refresh, a closed tab or a crashed browser.
It stores only the cart (lines, customer, discount, notes) - never any payment data - and is removed when the sale is
completed, the cart is cleared, or the register is closed. A counter can only ever see its own cart because every view
resolves the cart from the register session of the logged-in browser."""
import json
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from configuration.models import SystemConfiguration
from register.views import get_active_session
from .models import PosCart
from .pos_stock import OUT_OF_STOCK, STOCK_CHANGED, stock_map

MAX_LINES = 200
ZERO = Decimal("0")


def _num(value, default="0", *, minimum=None, places=3):
    try:
        n = Decimal(str(value if value not in (None, "") else default))
    except (InvalidOperation, ValueError):
        n = Decimal(default)
    if not n.is_finite():
        n = Decimal(default)
    if minimum is not None and n < minimum:
        n = minimum
    return str(n.quantize(Decimal(1).scaleb(-places)))


def clean_cart(raw):
    """Whitelist + type-check whatever the browser sent. Anything that is not a cart field (e.g. payment data) is dropped."""
    raw = raw if isinstance(raw, dict) else {}
    items = []
    for row in (raw.get("items") or [])[:MAX_LINES]:
        if not isinstance(row, dict):
            continue
        try:
            variant_id = int(row.get("variant_id"))
        except (TypeError, ValueError):
            continue
        qty = Decimal(_num(row.get("quantity"), "1", minimum=ZERO))
        if qty <= 0:
            continue
        items.append({
            "variant_id": variant_id,
            "name": str(row.get("name", ""))[:200],
            "sku": str(row.get("sku", ""))[:60],
            "unit": str(row.get("unit", ""))[:20],
            "image": (str(row["image"])[:300] if row.get("image") else None),
            "unit_price": _num(row.get("unit_price"), "0", minimum=ZERO, places=2),
            "list_price": _num(row.get("list_price", row.get("unit_price")), "0", minimum=ZERO, places=2),
            "discount_amount": _num(row.get("discount_amount"), "0", minimum=ZERO, places=2),
            "quantity": str(qty),
            "stock": _num(row.get("stock"), "0", places=3),
        })
    customer = raw.get("customer")
    if isinstance(customer, dict) and customer.get("id"):
        customer = {"id": customer.get("id"), "name": str(customer.get("name", ""))[:150], "phone": str(customer.get("phone", ""))[:30]}
    else:
        customer = None
    disc = raw.get("order_discount") if isinstance(raw.get("order_discount"), dict) else {}
    order_discount = {"type": "percent" if disc.get("type") == "percent" else "amount",
                      "value": _num(disc.get("value"), "0", minimum=ZERO, places=2)}
    return {
        "items": items, "customer": customer, "order_discount": order_discount, "shipping": _num(raw.get("shipping"), "0", minimum=ZERO, places=2),
        "sale_note": str(raw.get("sale_note", ""))[:2000], "staff_note": str(raw.get("staff_note", ""))[:2000],
    }


def revalidate(cart, warehouse):
    """Drops lines that went out of stock and trims quantities to what is there now. Returns (cart, notices)."""
    notices = []
    if not cart["items"]:
        return cart, notices
    allow_over = SystemConfiguration.get().allow_over_selling
    stocks = stock_map(warehouse, [r["variant_id"] for r in cart["items"]])
    from catalog.models import ProductVariant
    active = set(ProductVariant.objects.filter(
        pk__in=stocks, is_active=True, product__is_active=True).values_list("pk", flat=True))
    kept = []
    for row in cart["items"]:
        label = row["name"] or f"Item #{row['variant_id']}"
        available = stocks.get(row["variant_id"], ZERO)
        if row["variant_id"] not in active:
            notices.append(f"{label} is no longer available and was removed from the cart.")
            continue
        if available <= 0:
            notices.append(f"{label}: {OUT_OF_STOCK} It was removed from the cart.")
            continue
        qty = Decimal(row["quantity"])
        if qty > available and not allow_over:
            notices.append(f"{label}: only {available.normalize():f} left. Quantity was reduced.")
            row["quantity"] = str(available)
        row["stock"] = str(available)
        kept.append(row)
    cart["items"] = kept
    return cart, notices


def _session_or_error(request):
    session = get_active_session(request)
    if session is None:
        return None, JsonResponse({"ok": False, "error": "No register is open."}, status=409)
    return session, None


@login_required
@permission_required("sales.add_sale", raise_exception=True)
@require_GET
def cart_get(request):
    session, err = _session_or_error(request)
    if err:
        return err
    stored = PosCart.objects.filter(register_session=session).first()
    if stored is None or not stored.data:
        return JsonResponse({"ok": True, "cart": None, "notices": [], "version": 0})
    cart, notices = revalidate(clean_cart(stored.data), session.register.warehouse)
    if notices:                                             # keep the stored copy honest too
        PosCart.objects.filter(pk=stored.pk).update(data=cart)
    return JsonResponse({"ok": True, "cart": cart, "notices": notices, "version": stored.version})


@login_required
@permission_required("sales.add_sale", raise_exception=True)
@require_POST
def cart_save(request):
    session, err = _session_or_error(request)
    if err:
        return err
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"ok": False, "error": "Bad request body."}, status=400)
    cart = clean_cart(payload.get("cart") if isinstance(payload, dict) else None)
    with transaction.atomic():
        if not cart["items"] and not cart["customer"] and not cart["sale_note"] and not cart["staff_note"] and Decimal(cart["shipping"]) <= 0:
            PosCart.objects.filter(register_session=session).delete()
            return JsonResponse({"ok": True, "version": 0})
        stored, _ = PosCart.objects.select_for_update().get_or_create(register_session=session)
        stored.data = cart
        stored.version += 1
        stored.save(update_fields=["data", "version", "updated_at"])
    return JsonResponse({"ok": True, "version": stored.version})


@login_required
@permission_required("sales.add_sale", raise_exception=True)
@require_POST
def cart_clear(request):
    session, err = _session_or_error(request)
    if err:
        return err
    PosCart.objects.filter(register_session=session).delete()
    return JsonResponse({"ok": True})


@login_required
@permission_required("sales.add_sale", raise_exception=True)
@require_POST
def stock_check(request):
    """Cheap pre-flight before the payment window opens: {items:[{variant_id, quantity}]} ->
    {ok, problems:[{variant_id, available, message}]}. The real, locked check still runs again inside checkout."""
    session, err = _session_or_error(request)
    if err:
        return err
    try:
        payload = json.loads(request.body or b"{}")
        rows = payload.get("items") or []
        wanted = {}
        for r in rows:
            wanted[int(r["variant_id"])] = wanted.get(int(r["variant_id"]), ZERO) + Decimal(str(r["quantity"]))
    except (ValueError, TypeError, KeyError, InvalidOperation, AttributeError):
        return JsonResponse({"ok": False, "error": "Bad request body."}, status=400)
    allow_over = SystemConfiguration.get().allow_over_selling
    stocks = stock_map(session.register.warehouse, wanted)
    problems = []
    for vid, qty in wanted.items():
        available = stocks.get(vid, ZERO)
        if available <= 0:
            problems.append({"variant_id": vid, "available": str(available), "message": OUT_OF_STOCK})
        elif qty > available and not allow_over:
            problems.append({"variant_id": vid, "available": str(available), "message": STOCK_CHANGED})
    return JsonResponse({"ok": not problems, "problems": problems,
                         "message": (STOCK_CHANGED if problems else "")})


@login_required
@permission_required("sales.add_sale", raise_exception=True)
@require_POST
def quote(request):
    """Prices the cart exactly like the checkout will: {items, order_discount} -> subtotal / discount / tax / total.
    The POS screen shows these numbers, so what the cashier sees is what the sale is saved with."""
    from .pricing import CartError, price_cart
    try:
        payload = json.loads(request.body or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "error": "Bad request body."}, status=400)
    if not isinstance(payload, dict) or not isinstance(payload.get("items") or [], list):
        return JsonResponse({"ok": False, "error": "Bad request body."}, status=400)
    try:
        priced = price_cart(request.store, payload.get("items") or [], payload.get("order_discount"), payload.get("shipping"))
    except CartError as exc:
        return JsonResponse({"ok": False, "error": str(exc), "code": exc.code}, status=400)
    return JsonResponse({
        "ok": True, "subtotal": str(priced["subtotal"]), "discount": str(priced["discount"]),
        "tax": str(priced["tax"]), "shipping": str(priced["shipping"]), "rounding": str(priced["rounding"]), "total": str(priced["total"]),
        "lines": [{"variant_id": r["variant"].id, "discount": str(r["discount"]), "tax": str(r["tax"]), "total": str(r["line_total"])}
                  for r in priced["rows"]],
    })
