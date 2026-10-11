from django import template

from documents.models import InvoiceTemplate

register = template.Library()


@register.simple_tag
def receipt_template(store):
    """The branch's default receipt wording (Settings > Receipt), or None. Used by sales/invoice_print.html."""
    if store is None:
        return None
    return InvoiceTemplate.objects.filter(store=store).order_by("-is_default", "pk").first()
