"""PDF version of any report-pack report: shop header, period, summary figures and the table.

Uses ReportLab (pure Python: `pip install reportlab`), so it works on Windows without extra system libraries.
Wide reports are printed landscape; the header row repeats on every page; long text wraps instead of overflowing.
"""
import io
import os
import re
from datetime import date, datetime
from decimal import Decimal
from xml.sax.saxutils import escape

from django.utils import timezone

from .xlsx import ExcelError

_FONTS = {}
_CANDIDATES = [  # (regular, bold) - first pair that exists wins; otherwise the built-in Helvetica
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    (r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
    (r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
]


def _reportlab():
    try:
        import reportlab  # noqa: F401
        return reportlab
    except ImportError:  # pragma: no cover - depends on the environment
        raise ExcelError("PDF support is not installed on the server. Run: pip install reportlab")


def _fonts():
    """Register a Unicode font once (so names with accents print correctly); fall back to Helvetica."""
    if _FONTS:
        return _FONTS["regular"], _FONTS["bold"]
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    regular, bold = "Helvetica", "Helvetica-Bold"
    for reg, bld in _CANDIDATES:
        if os.path.exists(reg) and os.path.exists(bld):
            try:
                pdfmetrics.registerFont(TTFont("RptSans", reg))
                pdfmetrics.registerFont(TTFont("RptSans-Bold", bld))
                regular, bold = "RptSans", "RptSans-Bold"
                break
            except Exception:  # noqa: BLE001 - a broken font file must not break the report
                continue
    _FONTS.update(regular=regular, bold=bold)
    return regular, bold


def _t(value):
    return escape(str(value if value is not None else ""))


def _money(v, symbol):
    v = Decimal(str(v or 0))
    return f"{'-' if v < 0 else ''}{symbol}{abs(v):,.2f}"


def _cell(kind, value, symbol):
    if value is None or value == "":
        return "—"
    if kind == "money":
        return _money(value, symbol)
    if kind == "num":
        s = f"{Decimal(str(value)):,.3f}".rstrip("0").rstrip(".")
        return s or "0"
    if kind == "datetime" and isinstance(value, datetime):
        return (timezone.localtime(value) if timezone.is_aware(value) else value).strftime("%d %b %Y %H:%M")
    if kind in ("date", "datetime") and isinstance(value, (date, datetime)):
        return value.strftime("%d %b %Y")
    return str(value)


def _kpi_text(kind, value, symbol):
    if kind == "money":
        return _money(value, symbol)
    if kind == "num":
        return _cell("num", value, symbol)
    if kind == "int":
        return f"{int(value):,}"
    return str(value)


def render_report_pdf(entry_title, data, store, date_from, date_to, user) -> bytes:
    _reportlab()
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    regular, bold = _fonts()
    symbol = (getattr(store, "currency_symbol", "") or "") + (" " if len(getattr(store, "currency_symbol", "") or "") > 1 else "")
    brand = colors.HexColor("#4f46e5")
    ink, muted, line = colors.HexColor("#111827"), colors.HexColor("#6b7280"), colors.HexColor("#e5e7eb")
    cols = data["cols"]
    page = landscape(A4) if len(cols) > 6 else A4

    def st(name, size=9, font=regular, color=ink, align=0):
        return ParagraphStyle(name, fontName=font, fontSize=size, leading=size * 1.3, textColor=color, alignment=align)

    body, small = st("b"), st("s", 8, color=muted)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=page, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=16 * mm,
                            title=entry_title, author=store.name, subject=entry_title)
    width = page[0] - 24 * mm
    story = []

    period = "As of today" if data.get("no_dates") else f"Period: {date_from:%d %b %Y} – {date_to:%d %b %Y}"
    printed = f"Printed {timezone.localtime():%d %b %Y %H:%M} by {user.get_username()}"
    head = [[Paragraph(f'<font name="{bold}" size="16" color="#4f46e5">{_t(store.name)}</font>', body),
             Paragraph(f'<font name="{bold}" size="13">{_t(entry_title)}</font>', st("t", 13, bold, align=TA_RIGHT))],
            [Paragraph(_t(" · ".join(x for x in (getattr(store, "phone", ""), getattr(store, "email", "")) if x)), small),
             Paragraph(_t(f"{period} · {printed}"), st("p", 8, color=muted, align=TA_RIGHT))]]
    ht = Table(head, colWidths=[width * 0.4, width * 0.6])
    ht.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("LINEBELOW", (0, 1), (-1, 1), 1.2, brand), ("BOTTOMPADDING", (0, 1), (-1, 1), 5),
                            ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    story += [ht, Spacer(1, 5 * mm)]

    kpis = data.get("kpis") or []
    if kpis:
        per_row = 4 if page == A4 or len(kpis) <= 4 else 6
        cells = [[Paragraph(_t(label), small), Paragraph(f'<font name="{bold}" size="11">{_t(_kpi_text(kind, value, symbol))}</font>', body)] for label, kind, value in kpis]
        rows = []
        for i in range(0, len(cells), per_row):
            chunk = cells[i:i + per_row]
            chunk += [[Paragraph("", small), Paragraph("", body)]] * (per_row - len(chunk))
            rows.append([c[0] for c in chunk])
            rows.append([c[1] for c in chunk])
        t = Table(rows, colWidths=[width / per_row] * per_row)
        style = [("LEFTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
        for i in range(0, len(rows), 2):
            style += [("BACKGROUND", (0, i), (-1, i + 1), colors.HexColor("#f9fafb")), ("BOX", (0, i), (-1, i + 1), 0.4, line),
                      ("TOPPADDING", (0, i), (-1, i), 5), ("BOTTOMPADDING", (0, i + 1), (-1, i + 1), 6)]
        t.setStyle(TableStyle(style))
        story += [t, Spacer(1, 6 * mm)]

    cap = 1500  # thousands of rows are not readable on paper - Excel holds the full list
    rows = data["rows"]
    if not rows:
        story.append(Paragraph("No data for this period.", small))
    else:
        numeric = ("money", "num")
        weights = [1.0 if k in numeric else 1.6 for _key, _label, k in cols]
        weights[0] = max(weights[0], 1.7)
        cw = [width * w / sum(weights) for w in weights]
        fs = 8 if len(cols) <= 8 else 7
        hs = ParagraphStyle("h", fontName=bold, fontSize=fs, leading=fs * 1.25, textColor=colors.white)
        table = [[Paragraph(_t(label), ParagraphStyle("hh", parent=hs, alignment=TA_RIGHT if kind in numeric else 0)) for _k, label, kind in cols]]
        for r in rows[:cap]:
            table.append([Paragraph(_t(_cell(kind, r.get(key), symbol)), ParagraphStyle("c", fontName=regular, fontSize=fs, leading=fs * 1.3, textColor=ink,
                                                                                    alignment=TA_RIGHT if kind in numeric else 0)) for key, _l, kind in cols])
        t = Table(table, colWidths=cw, repeatRows=1)
        t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), ink), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                               ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]), ("LINEBELOW", (0, 0), (-1, -1), 0.25, line),
                               ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
        story.append(t)
        if len(rows) > cap:
            story += [Spacer(1, 2 * mm), Paragraph(f"Showing the first {cap} of {len(rows)} rows. Export to Excel for the full list.", small)]
    if data.get("notes"):
        story += [Spacer(1, 3 * mm), Paragraph(_t(data["notes"]), small)]

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont(regular, 7.5)
        canvas.setFillColor(muted)
        canvas.drawString(12 * mm, 8 * mm, f"{store.name} · {entry_title}"[:120])
        canvas.drawRightString(page[0] - 12 * mm, 8 * mm, f"Page {doc_.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def safe_filename(*parts):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", "-".join(str(p) for p in parts if p)).strip("-") or "report"
