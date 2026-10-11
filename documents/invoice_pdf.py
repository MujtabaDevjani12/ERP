"""Invoice PDF drawn with ReportLab (pure Python: no GTK / Pango / Cairo, so it works on Windows with a plain `pip install`).

Same content as the old WeasyPrint template: shop header, bill-to / sale details, item table, totals, note and footer.
Amounts follow Settings > Currency (symbol, position, decimals, separators).
"""
import io
from decimal import Decimal
from xml.sax.saxutils import escape

from core.templatetags.ui import format_money

INK, MUTED, FAINT, BRAND, LINE = "#111827", "#6b7280", "#9ca3af", "#4f46e5", "#e5e7eb"
STATUS = {"paid": ("PAID", "#065f46", "#d1fae5"), "partial": ("PARTIAL", "#92400e", "#fef3c7"), "due": ("DUE", "#991b1b", "#fee2e2")}


def _t(value):
    return escape(str(value if value not in (None, "") else ""))


def _qty(value):
    text = f"{Decimal(str(value or 0)):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def render_invoice_pdf_bytes(sale) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from configuration.models import SystemConfiguration
    from reports.pdf import _fonts

    from documents.receipt_design import design_for

    cfg = SystemConfiguration.get()
    store = sale.store
    d = design_for(store, cfg)                        # Settings > Receipt: paper, colour, what is printed, wording
    brand = d.accent
    symbol = store.currency_symbol or getattr(cfg, "default_currency_symbol", "") or ""

    def money(v):
        return format_money(v, symbol, decimals=getattr(cfg, "decimals", 2), decimal_sep=getattr(cfg, "decimal_separator", ".") or ".",
                            thousands_sep=getattr(cfg, "thousands_separator", ",") or "", position=getattr(cfg, "currency_position", "before"),
                            show_symbol=getattr(cfg, "display_currency_symbol", True))

    def num(v):
        return format_money(v, "", decimals=2, show_symbol=False)

    reg, bold = _fonts()
    base = ParagraphStyle("b", fontName=reg, fontSize=9.5, leading=13, textColor=colors.HexColor(INK))
    right = ParagraphStyle("r", parent=base, alignment=TA_RIGHT)
    muted = ParagraphStyle("m", parent=base, fontSize=8.5, textColor=colors.HexColor(MUTED))
    muted_r = ParagraphStyle("mr", parent=muted, alignment=TA_RIGHT)
    label = ParagraphStyle("l", parent=base, fontName=bold, fontSize=7.5, leading=10, textColor=colors.HexColor(FAINT))
    label_r = ParagraphStyle("lr", parent=label, alignment=TA_RIGHT)
    th = ParagraphStyle("th", parent=base, fontName=bold, fontSize=7.5, textColor=colors.HexColor(MUTED))
    th_r = ParagraphStyle("thr", parent=th, alignment=TA_RIGHT)
    name = ParagraphStyle("n", parent=base, fontName=bold, fontSize=16, leading=20)
    title = ParagraphStyle("t", parent=base, fontName=bold, fontSize=18, leading=22, textColor=colors.HexColor(brand), alignment=TA_CENTER if d.is_thermal or d.header_align == "center" else TA_RIGHT)
    foot = ParagraphStyle("f", parent=muted, alignment=TA_CENTER)

    buf = io.BytesIO()
    if d.is_thermal:                                  # receipt roll: narrow and long
        page_w = (58 if d.is_compact else 80) * mm
        pagesize, margin = (page_w, 600 * mm), 4 * mm
        base.fontSize, base.leading = 8.5, 11
    else:
        pagesize, margin = A4, 16 * mm
    doc = SimpleDocTemplate(buf, pagesize=pagesize, leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=margin,
                            title=f"{d.title.title()} {sale.reference}", author=store.name)
    width = pagesize[0] - 2 * margin
    story = []

    # header: shop on the left, the title + reference on the right (stacked and centred for the centred style and for rolls)
    contact = " · ".join(x for x in (store.phone, store.email, getattr(cfg, "website", "")) if x)
    left = [Paragraph(_t(store.name), name)]
    if getattr(cfg, "tagline", ""):
        left.append(Paragraph(_t(cfg.tagline), muted))
    if d.show_address and store.address:
        left.append(Paragraph(_t(store.address).replace("\n", "<br/>"), muted))
    if d.show_contact and contact:
        left.append(Paragraph(_t(contact), muted))
    if d.show_tax_number and getattr(cfg, "tax_number", ""):
        left.append(Paragraph("Tax / NTN: " + _t(cfg.tax_number), muted))
    if d.header_note:
        left.append(Paragraph(_t(d.header_note), muted))
    created = sale.created_at.strftime("%d %b %Y, %H:%M")
    ident_style = ParagraphStyle("mc", parent=muted, alignment=TA_CENTER) if (d.is_thermal or d.header_align == "center") else muted_r
    ident = [Paragraph(_t(d.title), title), Paragraph(_t(sale.reference), ident_style), Paragraph(created, ident_style)]
    if d.is_thermal or d.header_align == "center":
        left_c = [Paragraph(p.text, ParagraphStyle("c", parent=p.style, alignment=TA_CENTER)) for p in left]
        head = Table([[left_c], [ident]], colWidths=[width])
    else:
        head = Table([[left, ident]], colWidths=[width * 0.6, width * 0.4])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [head, Spacer(1, 4), HRFlowable(width="100%", thickness=2.2 if d.style == "classic" else 0.8, color=colors.HexColor(brand if d.style != "minimal" else LINE), spaceAfter=10)]

    # bill to / sale details
    cust = sale.customer
    bill = [Paragraph("BILLED TO", label), Paragraph(_t(cust.name if cust else "Walk-in customer"), ParagraphStyle("bn", parent=base, fontName=bold))]
    if cust and cust.phone:
        bill.append(Paragraph(_t(cust.phone), base))
    if cust and getattr(cust, "address", ""):
        bill.append(Paragraph(_t(cust.address), base))
    biller = sale.biller.get_full_name() or sale.biller.username if sale.biller_id else ""
    word, fg, bg = STATUS.get(sale.payment_status, STATUS["due"])
    badge = Table([[Paragraph(word, ParagraphStyle("bd", parent=base, fontName=bold, fontSize=8, textColor=colors.HexColor(fg), alignment=TA_CENTER))]], colWidths=[22 * mm])
    badge.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(bg)), ("ROUNDEDCORNERS", [8, 8, 8, 8]), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    details = [Paragraph("SALE DETAILS", label_r)]
    if d.show_biller:
        details.append(Paragraph(f"Biller: {_t(biller)}", right))
    if d.show_warehouse:
        details.append(Paragraph(f"Warehouse: {_t(sale.warehouse.name)}", right))
    badge.hAlign = "RIGHT"
    meta = Table([[bill, details]], colWidths=[width * 0.5, width * 0.5])
    meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [meta, Spacer(1, 4), badge, Spacer(1, 12)]

    # items (the columns follow "What is printed"; a roll keeps just item / qty / total)
    cols = [("ITEM", 0.38, th), ("QTY", 0.09, th_r), ("UNIT PRICE", 0.15, th_r)]
    if d.show_item_discount and not d.is_thermal:
        cols.append(("DISCOUNT", 0.13, th_r))
    if d.show_item_tax and not d.is_thermal:
        cols.append(("TAX", 0.11, th_r))
    cols.append(("TOTAL", 0.14, th_r))
    if d.is_thermal:
        cols = [("ITEM", 0.5, th), ("QTY", 0.14, th_r), ("TOTAL", 0.36, th_r)]
    scale = 1 / sum(c[1] for c in cols)
    rows = [[Paragraph(c[0], c[2]) for c in cols]]
    for it in sale.items.select_related("variant__product"):
        label_text = it.variant.product.name + (f" — {it.variant.name}" if it.variant.name and it.variant.name != "Default" else "")
        sku = f"<br/><font size='7' color='{FAINT}'>{_t(it.variant.sku)}</font>" if d.show_item_sku and it.variant.sku else ""
        cells = {"ITEM": Paragraph(_t(label_text) + sku, base), "QTY": Paragraph(_qty(it.quantity), right), "UNIT PRICE": Paragraph(num(it.unit_price), right),
                 "DISCOUNT": Paragraph(num(it.discount_amount), right), "TAX": Paragraph(num(it.tax_amount), right), "TOTAL": Paragraph(num(it.line_total), right)}
        rows.append([cells[c[0]] for c in cols])
    items = Table(rows, colWidths=[width * c[1] * scale for c in cols], repeatRows=1)
    head_bg = colors.HexColor(brand).clone(alpha=0.1) if d.style == "modern" else colors.HexColor("#f9fafb")
    items.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), head_bg), ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor(LINE)),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#f3f4f6")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
    story += [items, Spacer(1, 12)]

    # totals
    lines = [("Subtotal", money(sale.subtotal)), ("Discount", "-" + money(sale.discount_amount)), ("Tax", "+" + money(sale.tax_amount))]
    if sale.shipping_amount:
        lines.append(("Shipping", "+" + money(sale.shipping_amount)))
    if sale.rounding_amount:
        lines.append(("Rounding", ("+" if sale.rounding_amount > 0 else "") + money(sale.rounding_amount)))
    grand_at = len(lines)
    lines += [("Total", money(sale.total_amount)), ("Paid", money(sale.paid_amount)), ("Balance due", money(sale.due_amount))]
    trows = [[Paragraph(_t(a), ParagraphStyle("ta", parent=base, fontName=bold if i == grand_at else reg, fontSize=11 if i == grand_at else 9.5)),
              Paragraph(_t(b), ParagraphStyle("tb", parent=right, fontName=bold if i == grand_at else reg, fontSize=11 if i == grand_at else 9.5))] for i, (a, b) in enumerate(lines)]
    totals = Table(trows, colWidths=[width * 0.5, width * 0.5] if d.is_thermal else [width * 0.25, width * 0.22], hAlign="RIGHT")
    totals.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LINEABOVE", (0, grand_at), (-1, grand_at), 1.4, colors.HexColor(INK)),
                                ("TOPPADDING", (0, grand_at), (-1, grand_at), 6)]))
    story.append(totals)

    if d.show_payments:
        paid_rows = [[Paragraph(_t(p.get_method_display()), muted), Paragraph(_t(money(p.amount)), muted_r)] for p in sale.payments.all()]
        if paid_rows:
            pay = Table(paid_rows, colWidths=[width * 0.25, width * 0.22] if not d.is_thermal else [width * 0.5, width * 0.5], hAlign="RIGHT")
            pay.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
            story += [Spacer(1, 6), pay]
    if d.show_notes and sale.sale_note:
        story += [Spacer(1, 10), Paragraph(f"Note: {_t(sale.sale_note)}", muted)]
    if d.terms_and_conditions:
        story += [Spacer(1, 10), Paragraph("<b>Terms &amp; conditions</b><br/>" + _t(d.terms_and_conditions).replace("\n", "<br/>"), muted)]
    thanks = _t(d.footer_text) if d.footer_text else f"Thank you for shopping with {_t(store.name)}!"
    story += [Spacer(1, 22), HRFlowable(width="100%", thickness=0.5, color=colors.HexColor(LINE), spaceAfter=8),
              Paragraph(thanks + "<br/>This is a computer-generated document.", foot)]
    doc.build(story)
    return buf.getvalue()
