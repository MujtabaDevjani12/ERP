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

    cfg = SystemConfiguration.get()
    store = sale.store
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
    title = ParagraphStyle("t", parent=base, fontName=bold, fontSize=18, leading=22, textColor=colors.HexColor(BRAND), alignment=TA_RIGHT)
    foot = ParagraphStyle("f", parent=muted, alignment=TA_CENTER)

    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
                            title=f"Invoice {sale.reference}", author=store.name)
    width = A4[0] - 32 * mm
    story = []

    # header: shop on the left, "INVOICE" + reference on the right
    contact = " · ".join(x for x in (store.phone, store.email) if x)
    left = [Paragraph(_t(store.name), name)]
    if store.address:
        left.append(Paragraph(_t(store.address).replace("\n", "<br/>"), muted))
    if contact:
        left.append(Paragraph(_t(contact), muted))
    created = sale.created_at.strftime("%d %b %Y, %H:%M")
    head = Table([[left, [Paragraph("INVOICE", title), Paragraph(_t(sale.reference), muted_r), Paragraph(created, muted_r)]]], colWidths=[width * 0.6, width * 0.4])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [head, Spacer(1, 4), HRFlowable(width="100%", thickness=2.2, color=colors.HexColor(BRAND), spaceAfter=10)]

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
    details = [Paragraph("SALE DETAILS", label_r), Paragraph(f"Biller: {_t(biller)}", right), Paragraph(f"Warehouse: {_t(sale.warehouse.name)}", right)]
    badge.hAlign = "RIGHT"
    meta = Table([[bill, details]], colWidths=[width * 0.5, width * 0.5])
    meta.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [meta, Spacer(1, 4), badge, Spacer(1, 12)]

    # items
    rows = [[Paragraph("ITEM", th), Paragraph("QTY", th_r), Paragraph("UNIT PRICE", th_r), Paragraph("DISCOUNT", th_r), Paragraph("TAX", th_r), Paragraph("TOTAL", th_r)]]
    for it in sale.items.select_related("variant__product"):
        label_text = it.variant.product.name + (f" — {it.variant.name}" if it.variant.name and it.variant.name != "Default" else "")
        rows.append([Paragraph(_t(label_text), base), Paragraph(_qty(it.quantity), right), Paragraph(num(it.unit_price), right),
                     Paragraph(num(it.discount_amount), right), Paragraph(num(it.tax_amount), right), Paragraph(num(it.line_total), right)])
    items = Table(rows, colWidths=[width * 0.38, width * 0.09, width * 0.15, width * 0.13, width * 0.11, width * 0.14], repeatRows=1)
    items.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f9fafb")), ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor(LINE)),
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
    totals = Table(trows, colWidths=[width * 0.25, width * 0.22], hAlign="RIGHT")
    totals.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LINEABOVE", (0, grand_at), (-1, grand_at), 1.4, colors.HexColor(INK)),
                                ("TOPPADDING", (0, grand_at), (-1, grand_at), 6)]))
    story.append(totals)

    if sale.sale_note:
        story += [Spacer(1, 10), Paragraph(f"Note: {_t(sale.sale_note)}", muted)]
    story += [Spacer(1, 22), HRFlowable(width="100%", thickness=0.5, color=colors.HexColor(LINE), spaceAfter=8),
              Paragraph(f"Thank you for shopping with {_t(store.name)}!<br/>This is a computer-generated invoice.", foot)]
    doc.build(story)
    return buf.getvalue()
