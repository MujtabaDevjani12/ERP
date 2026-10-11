from django import template

from documents.models import InvoiceTemplate

register = template.Library()


@register.simple_tag
def receipt_template(store):
    """The branch's default receipt wording (Settings > Receipt), or None. Used by sales/invoice_print.html."""
    if store is None:
        return None
    return InvoiceTemplate.objects.filter(store=store).order_by("-is_default", "pk").first()


@register.simple_tag(takes_context=True)
def receipt_design(context, store):
    """{% receipt_design sale.store as d %} - every design value of the receipt, with defaults filled in (documents.receipt_design)."""
    from documents.receipt_design import design_for
    return design_for(store, context.get("system_settings"))
