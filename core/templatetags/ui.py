"""
Template helpers for the shared UI kit.

    {% load ui %}
    {% icon "search" "w-4 h-4" %}                                    one icon library (core/ui_icons.py)
    {% ui "badge" tone="success" label="Paid" %}                      component without a body
    {% component "card" title="Orders" %}...{% endcomponent %}        component with a body (the "slot")
    {{ order.status|status_badge }}                                   status pill with automatic colour
    {% sparkline values %}                                            tiny trend line for KPI cards
    {% qs page=3 %}                                                   current query string with one param changed

Components live in templates/components/<name>.html and receive the keyword arguments plus `body`.
"""
from decimal import Decimal

from django import template
from django.template.loader import get_template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from core.ui_icons import icon_path

register = template.Library()


# --------------------------------------------------------------------------- icons
@register.simple_tag
def icon(name, css="w-4 h-4", stroke="1.8"):
    return format_html(
        '<svg class="{}" fill="none" stroke="currentColor" stroke-width="{}" viewBox="0 0 24 24" aria-hidden="true">'
        '<path stroke-linecap="round" stroke-linejoin="round" d="{}"/></svg>',
        css, stroke, icon_path(name),
    )


@register.filter
def icon_d(name):
    """Raw path data, for templates that already draw their own <svg>."""
    return icon_path(name)


# --------------------------------------------------------------------------- components
def _render_component(context, name, values, body):
    ctx = context.flatten()
    ctx.update(values)
    ctx["body"] = mark_safe(body)
    return get_template("components/%s.html" % name).render(ctx)


@register.simple_tag(takes_context=True)
def ui(context, name, **kwargs):
    """A component that has no body: {% ui "empty_state" title="Nothing here" %}"""
    return mark_safe(_render_component(context, name, kwargs, ""))


class ComponentNode(template.Node):
    def __init__(self, name, kwargs, nodelist):
        self.name, self.kwargs, self.nodelist = name, kwargs, nodelist

    def render(self, context):
        name = self.name.resolve(context)
        values = {k: v.resolve(context) for k, v in self.kwargs.items()}
        body = self.nodelist.render(context)
        return mark_safe(_render_component(context, name, values, body))


@register.tag("component")
def do_component(parser, token):
    bits = token.split_contents()[1:]
    if not bits:
        raise template.TemplateSyntaxError("component needs a name: {% component \"card\" title=\"...\" %}")
    name = parser.compile_filter(bits[0])
    kwargs = {}
    for bit in bits[1:]:
        key, sep, value = bit.partition("=")
        if not sep or not key.isidentifier():
            raise template.TemplateSyntaxError("component arguments must look like key=value, got %r" % bit)
        kwargs[key] = parser.compile_filter(value)
    nodelist = parser.parse(("endcomponent",))
    parser.delete_first_token()
    return ComponentNode(name, kwargs, nodelist)


# --------------------------------------------------------------------------- status badges
_SUCCESS = {"active", "approved", "paid", "posted", "completed", "complete", "received", "delivered", "passed", "pass",
            "confirmed", "fulfilled", "reconciled", "in_stock", "yes", "true", "enabled", "ok", "accepted", "done",
            "settled", "balanced", "present", "verified", "healthy", "success", "valid", "online"}
_WARNING = {"pending", "partial", "partially_paid", "partially_received", "partially_delivered", "submitted", "in_progress",
            "on_hold", "hold", "waiting", "low", "low_stock", "review", "in_review", "pending_approval", "expiring",
            "warning", "partial_paid", "awaiting", "paused", "returned"}
_DANGER = {"rejected", "cancelled", "canceled", "failed", "fail", "void", "voided", "blocked", "overdue", "out_of_stock",
           "error", "expired", "declined", "critical", "unpaid", "reversed", "no", "false", "offline"}
_INFO = {"open", "new", "scheduled", "planned", "released", "issued", "sent", "running", "processing", "info", "started",
         "in_transit", "assigned"}


def tone_for(value):
    key = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    if key in _SUCCESS:
        return "success"
    if key in _WARNING:
        return "warning"
    if key in _DANGER:
        return "danger"
    if key in _INFO:
        return "info"
    return "neutral"


@register.simple_tag
def badge(value, label=None, tone=None):
    """{% badge obj.status label=obj.get_status_display %} -> coloured pill."""
    text = label if label not in (None, "") else str(value).replace("_", " ").capitalize()
    return format_html('<span class="ui-badge ui-badge-{}">{}</span>', tone or tone_for(value), text)


@register.filter
def status_badge(value):
    if value in (None, ""):
        return mark_safe('<span class="text-slate-400">&mdash;</span>')
    if value is True:
        return badge("yes", label="Yes")
    if value is False:
        return badge("no", label="No")
    return badge(value)


@register.filter
def status_tone(value):
    return tone_for(value)


# --------------------------------------------------------------------------- misc helpers
@register.simple_tag
def sparkline(values, height=28):
    """Small inline trend chart from a list of numbers (anything non-numeric is ignored)."""
    nums = []
    for v in values or []:
        try:
            nums.append(float(v) if not isinstance(v, Decimal) else float(v))
        except (TypeError, ValueError):
            continue
    if len(nums) < 2:
        return ""
    lo, hi = min(nums), max(nums)
    span = (hi - lo) or 1.0
    h = float(height)
    pad = 2.0
    step = 100.0 / (len(nums) - 1)
    pts = [(i * step, pad + (h - 2 * pad) * (1 - (n - lo) / span)) for i, n in enumerate(nums)]
    line = "M" + " L".join("%.2f %.2f" % p for p in pts)
    area = line + " L100 %.2f L0 %.2f Z" % (h, h)
    return format_html(
        '<svg class="spark" viewBox="0 0 100 {}" preserveAspectRatio="none" aria-hidden="true">'
        '<path class="area" d="{}"/><path class="line" d="{}" vector-effect="non-scaling-stroke"/></svg>',
        int(h), area, line,
    )


@register.simple_tag(takes_context=True)
def qs(context, **changes):
    """Current GET parameters with some changed (value None removes the parameter): href="{% qs page=2 %}" """
    request = context.get("request")
    params = request.GET.copy() if request is not None else {}
    if hasattr(params, "setlist"):
        for key, value in changes.items():
            if value is None or value == "":
                params.pop(key, None)
            else:
                params[key] = value
        return "?" + params.urlencode() if params else "?"
    return "?"


@register.filter
def getitem(mapping, key):
    try:
        return mapping.get(key)
    except AttributeError:
        try:
            return mapping[key]
        except (KeyError, IndexError, TypeError):
            return None


@register.filter
def initials(name):
    parts = [p for p in str(name or "").replace("_", " ").replace(".", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


# --------------------------------------------------------------------------- top bar section tabs
# Presentation only: picks a handful of the sidebar's groups (already permission-filtered by core.ui_nav.build_nav)
# and links each tab to the first page inside it. Nothing here decides who may see what.
NAV_TABS = (
    ("dashboard", "Dashboard"), ("sales", "Sales"), ("catalog", "Catalog"),
    ("inventory", "Inventory"), ("purchasing", "Purchasing"), ("reports", "Reports"),
)


@register.filter
def nav_tabs(nav):
    wanted = dict(NAV_TABS)
    found = {}
    try:
        for section in nav or []:
            for e in section.get("entries", []):
                key = e.get("key")
                if key not in wanted:
                    continue
                if e.get("type") == "link":
                    url = e.get("url")
                else:
                    url = next((i["url"] for i in e.get("items", []) if i.get("type") == "link" and i.get("url")), "")
                if url:
                    found[key] = {"key": key, "label": wanted[key], "url": url, "active": bool(e.get("active"))}
    except Exception:                                           # noqa: BLE001 - the tabs must never break a page
        return []
    return [found[k] for k, _ in NAV_TABS if k in found]


# --------------------------------------------------------------------------- money
def format_money(value, symbol="", *, decimals=2, decimal_sep=".", thousands_sep=",", position="before", show_symbol=True):
    """1234.5 -> 'Rs 1,234.50' (or '1,234.50 Rs'), following the Settings > Currency options."""
    from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
    try:
        number = Decimal(str(value if value not in (None, "") else 0))
    except (InvalidOperation, ValueError):
        return str(value)
    decimals = max(0, min(int(decimals), 4))
    quant = Decimal(1).scaleb(-decimals)
    number = number.quantize(quant, rounding=ROUND_HALF_UP)
    negative = number < 0
    whole, _, frac = f"{abs(number):.{decimals}f}".partition(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    text = thousands_sep.join(groups) + (decimal_sep + frac if decimals else "")
    if show_symbol and symbol:
        text = f"{text} {symbol}" if position == "after" else f"{symbol} {text}"
    return f"-{text}" if negative else text


@register.simple_tag(takes_context=True)
def money(context, value, symbol=None, decimals=None):
    """{% money sale.total_amount %} - an amount written the way Settings > Currency says (symbol, position, decimals, separators).
    `symbol` defaults to the current store's; `decimals` defaults to the setting (use 0 for whole-number dashboards)."""
    cfg = context.get("system_settings")
    if symbol is None:
        store = context.get("current_store")
        symbol = getattr(store, "currency_symbol", "") or getattr(cfg, "default_currency_symbol", "") or ""
    return format_html("{}", format_money(
        value, symbol,
        decimals=decimals if decimals is not None else getattr(cfg, "decimals", 2),
        decimal_sep=getattr(cfg, "decimal_separator", ".") or ".", thousands_sep=getattr(cfg, "thousands_separator", ",") or "",
        position=getattr(cfg, "currency_position", "before"), show_symbol=getattr(cfg, "display_currency_symbol", True)))
