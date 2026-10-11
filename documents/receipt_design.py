"""Resolves how a receipt / invoice should look: the branch's saved design (Settings > Receipt) with safe defaults.

One object, used by the print page (sales/invoice_print.html), the Settings live preview and the PDF invoice, so all three agree.
"""
import re
from types import SimpleNamespace

from .models import InvoiceTemplate

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# the fields the Settings designer edits (all live on InvoiceTemplate)
DESIGN_FIELDS = [
    "paper", "style", "header_align", "title_text", "accent_color", "font_style", "font_size",
    "show_logo", "show_address", "show_contact", "show_tax_number", "show_biller", "show_warehouse",
    "show_item_sku", "show_item_discount", "show_item_tax", "show_payments", "show_notes",
    "header_note", "footer_text", "terms_and_conditions",
]
FONT_STACKS = {
    "sans": "'Segoe UI', Roboto, Arial, sans-serif",
    "serif": "Georgia, 'Times New Roman', serif",
    "mono": "'Courier New', Consolas, monospace",
}
FONT_PX = {"small": 11.5, "normal": 13, "large": 14.5}
PAPER = {  # css width in px for the preview, @page size
    "a4": {"width": 794, "page": "A4", "margin": "16mm 14mm"},
    "thermal80": {"width": 302, "page": "80mm auto", "margin": "3mm"},
    "thermal58": {"width": 219, "page": "58mm auto", "margin": "2mm"},
}


def _defaults():
    out = {}
    for name in DESIGN_FIELDS:
        field = InvoiceTemplate._meta.get_field(name)
        out[name] = field.get_default() if field.has_default() else ""
    return out


def resolve(template=None, system_settings=None, overrides=None):
    """A plain object with every design value filled in (template -> overrides -> defaults), plus the derived css values."""
    values = _defaults()
    if template is not None:
        for name in DESIGN_FIELDS:
            values[name] = getattr(template, name)
    if overrides:
        values.update({k: v for k, v in overrides.items() if k in values})
    brand = getattr(system_settings, "primary_color", "") or "#4f46e5"
    accent = values["accent_color"] if HEX_RE.match(values.get("accent_color") or "") else brand
    thermal = values["paper"] in ("thermal80", "thermal58")
    paper = PAPER.get(values["paper"], PAPER["a4"])
    d = SimpleNamespace(**values)
    d.accent = accent
    d.is_thermal = thermal
    d.is_compact = values["paper"] == "thermal58"
    d.width_px = paper["width"]
    d.page_size, d.page_margin = paper["page"], paper["margin"]
    d.font_stack = FONT_STACKS.get(values["font_style"], FONT_STACKS["sans"])
    base = FONT_PX.get(values["font_size"], 13)
    d.font_px = base - 1.5 if thermal else base
    d.title = (values.get("title_text") or "").strip() or "INVOICE"
    return d


def design_for(store, system_settings=None, overrides=None):
    template = None
    if store is not None:
        template = InvoiceTemplate.objects.filter(store=store).order_by("-is_default", "pk").first()
    return resolve(template, system_settings, overrides)
