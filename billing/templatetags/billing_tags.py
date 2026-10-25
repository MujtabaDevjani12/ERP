from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


@register.filter
def price(value, currency="PKR"):
    """1500 -> 'PKR 1,500' (whole numbers lose the decimals, cents are kept otherwise)."""
    try:
        d = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return ""
    text = f"{d:,.0f}" if d == d.to_integral_value() else f"{d:,.2f}"
    return f"{currency} {text}"


@register.filter
def state_tone(state):
    return {"pending": "warning", "trial": "info", "active": "success", "grace": "warning", "expired": "danger", "suspended": "danger"}.get(state, "neutral")


@register.filter
def state_label(state):
    return {"pending": "Awaiting payment", "trial": "Free demo", "active": "Active", "grace": "Payment due", "expired": "Expired", "suspended": "Suspended"}.get(state, state)


@register.filter
def get_item(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        return None
