from django import template
from django.utils.html import linebreaks
from django.utils.safestring import mark_safe

from core.richtext import plain_text, sanitize_html

register = template.Library()


@register.filter
def rich(value):
    """A product description: formatted text from the editor, or an old plain-text one with line breaks. Always sanitised."""
    value = value or ""
    if "<" not in value:
        return linebreaks(value)
    return mark_safe(sanitize_html(value))


@register.filter
def plain(value, limit=None):
    return plain_text(value, int(limit) if limit else None)
