from django import template

from .. import exchange

register = template.Library()


@register.inclusion_tag("reports/_excel_actions.html", takes_context=True)
def excel_actions(context, key):
    """Export / Import buttons for a list page: {% excel_actions "products" %}. Shows only what the signed-in user may do."""
    request, res = context.get("request"), exchange.get(key)
    user = getattr(request, "user", None)
    if res is None or user is None or not user.is_authenticated:
        return {"show": False}
    return {"show": res.can_export(user) or res.can_import(user), "key": key, "label": res.plural, "export": res.can_export(user), "import": res.can_import(user)}
