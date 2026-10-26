"""
Settings hub — ONE screen for every setting, laid out as a section list on the left and the selected section's panel on the right.

Each section is a small form that saves only its own fields (see forms.py), so changing the receipt wording can never blank the
brand colour. System-wide sections live on the SystemConfiguration singleton; POS sections live on the current branch's
POSConfiguration. Lists that already have their own full screens (payment methods, devices, taxes…) are shown here as a summary
with a "Manage" button rather than duplicated.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.mail import EmailMessage
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse

from . import forms as F
from .mailing import sender_identity, smtp_status, whatsapp_status
from .models import NumberingPrefix, POSConfiguration, SystemConfiguration

SYS = "configuration.change_systemconfiguration"
POS = "configuration.change_posconfiguration"

# slug, label, icon, one-line description, kind, permission, form class (+ layout: [(group title, [fields], columns)])
SECTIONS = [
    dict(slug="business", label="Business", icon="building", kind="form", perm=SYS, scope="system", form=F.BusinessForm,
         desc="Who you are — printed on receipts and invoices.",
         layout=[("Business details", ["site_name", "tagline", "phone", "email", "website", "tax_number"], 2), ("Address", ["address"], 1),
                 ("Regional", ["default_timezone", "rows_per_page", "quantity_decimals"], 2)]),
    dict(slug="branding", label="Logo & colours", icon="palette", kind="form", perm=SYS, scope="system", form=F.BrandingForm, multipart=True, extra="logo",
         desc="Your logo and brand colour — used on every screen, receipt and the sign-in page.",
         layout=[("Brand", ["site_logo", "primary_color"], 2)]),
    dict(slug="menu-fonts", label="Menu & fonts", icon="layers", kind="form", perm=SYS, scope="system", form=F.MenuFontForm,
         desc="The sidebar colour, typeface, text size and text colour of the whole application.",
         layout=[("Appearance", ["secondary_color", "font_family"], 2), ("Text", ["font_size", "text_color"], 2)]),
    dict(slug="currency", label="Currency", icon="currency", kind="form", perm=SYS, scope="system", form=F.CurrencySettingsForm, extra="currency",
         desc="Your main currency and how amounts are written.",
         layout=[("Main currency", ["default_currency_code", "default_currency_symbol"], 2), ("Number format", ["decimals", "decimal_separator", "thousands_separator"], 2),
                 ("Display", ["currency_position", "display_currency_symbol"], 2)]),
    dict(slug="stock", label="Stock & costing", icon="archive", kind="form", perm=SYS, scope="system", form=F.StockForm,
         desc="How stock and purchase costs behave.",
         layout=[("Costing & selling", ["update_cost_with_purchase", "allow_over_selling"], 1), ("Product options", ["enable_product_variants", "enable_racks", "enable_product_expiry", "remove_expired_from_stock"], 1),
                 ("Locking", ["edit_lock_days"], 2)]),
    dict(slug="tax", label="Tax & discounts", icon="percent", kind="form", perm=SYS, scope="system", form=F.TaxDiscountForm, extra="tax",
         desc="How tax and discounts are applied to a bill.",
         layout=[("Bill rules", ["order_tax_inclusive", "allow_product_level_discount"], 1)]),
    dict(slug="defaults", label="Defaults", icon="sliders", kind="form", perm=SYS, scope="system", form=F.DefaultsForm, needs_store=True,
         desc="What is pre-selected when someone starts a new sale or document.",
         layout=[("System", ["default_warehouse", "default_language", "date_format"], 2),
                 ("Point of sale (this branch)", ["pos_default_warehouse", "pos_default_biller", "pos_default_customer", "pos_default_category"], 2)]),
    dict(slug="numbering", label="Numbering", icon="hash", kind="numbering", perm="configuration.change_numberingprefix",
         desc="The prefix and length of every document number, e.g. SALE-000123."),
    dict(slug="rewards", label="Reward points", icon="gift", kind="form", perm=SYS, scope="system", form=F.RewardForm, extra="rewards",
         desc="How customers earn loyalty points.",
         layout=[("Earning rule", ["award_points_per_amount", "award_points_value"], 2)]),
    dict(slug="pos-behaviour", label="POS behaviour", icon="register", kind="form", perm=POS, scope="pos", form=F.POSBehaviourForm, needs_store=True,
         desc="How the billing screen behaves at this branch.",
         layout=[("Product grid", ["products_per_page", "item_order", "product_button_color"], 2), ("Checkout", ["after_sale_page", "rounding", "pos_access_pin"], 2), ("Customer", ["require_customer"], 1),
                 ("Screen", ["show_clock", "show_tooltips", "on_screen_keyboard"], 1)]),
    dict(slug="receipt", label="Receipt", icon="receipt", kind="form", perm=POS, scope="pos", form=F.ReceiptForm, needs_store=True, extra="receipt",
         desc="Design the customer's receipt / invoice and choose when it prints.",
         layout=[("Paper & look", ["paper", "style", "header_align", "font_style", "font_size"], 2),
                 ("Colour", ["use_brand_color", "accent_color"], 2),
                 ("Heading", ["title_text", "header_note"], 2),
                 ("What is printed", ["show_logo", "show_address", "show_contact", "show_tax_number", "show_biller", "show_warehouse",
                                      "show_item_sku", "show_item_discount", "show_item_tax", "show_payments", "show_notes",
                                      "show_customer_details_on_receipt"], 2),
                 ("Wording", ["footer_text", "terms_and_conditions"], 1),
                 ("Extra lines", ["receipt_custom_field1_title", "receipt_custom_field1_value", "receipt_custom_field2_title", "receipt_custom_field2_value"], 2),
                 ("Printing", ["auto_print_receipt", "printing_mode"], 2)]),
    dict(slug="payments", label="Payment methods", icon="credit-card", kind="payments", perm=None, desc="Cash, card, wallets — which are accepted at the till."),
    dict(slug="printers", label="Printers", icon="printer", kind="devices", device_type="printer", perm=None, desc="Receipt printers connected to this branch."),
    dict(slug="scanners", label="Barcode scanners", icon="scan", kind="form", perm=SYS, scope="system", form=F.ScannerForm, extra="scanners",
         desc="Scanner detection and barcode labels.",
         layout=[("Scanning", ["auto_detect_barcode_scan", "barcode_scan_min_chars"], 1), ("Labels", ["barcode_renderer", "barcode_separator"], 2)]),
    dict(slug="email", label="Email delivery", icon="mail", kind="form", perm=SYS, scope="system", form=F.EmailDeliveryForm, extra="email",
         desc="Who invoice and password-reset emails come from.",
         layout=[("Sender", ["email_from_name", "email_from_address", "email_reply_to"], 1)]),
    dict(slug="email-templates", label="Email templates", icon="doc", kind="email_templates", perm="documents.change_emailtemplate",
         desc="The wording of the invoice email sent to customers."),
    dict(slug="whatsapp", label="WhatsApp", icon="chat", kind="form", perm=SYS, scope="system", form=F.WhatsAppForm, extra="whatsapp",
         desc="Send invoices to customers on WhatsApp.",
         layout=[("Message", ["whatsapp_country_code", "whatsapp_message"], 1)]),
    dict(slug="backup", label="Data backup", icon="database", kind="backup", perm=SYS,
         desc="Download all of this company's data, or restore it from a backup file."),
    dict(slug="shortcuts", label="Keyboard shortcuts", icon="keyboard", kind="shortcuts", perm=None, desc="Faster billing without the mouse."),
]
BY_SLUG = {s["slug"]: s for s in SECTIONS}

_SHORTCUTS = [
    ("Billing screen", [("F2", "Focus the product search (Ctrl + K also works)"), ("F4", "Choose a customer"), ("F6", "Order discount & tax"),
                        ("F7", "Hold the current sale"), ("F8", "Open payment"), ("F9", "Held sales"), ("Ctrl + Enter", "Pay / complete / start a new sale"),
                        ("Ctrl + Shift + X", "Clear the cart"), ("?", "Show the shortcut help"), ("Esc", "Close the open window or clear the search")]),
    ("Everywhere", [("Ctrl + K", "Open the quick search / command palette")]),
]
# keys split into caps for display: "Ctrl + Enter" -> ["Ctrl", "Enter"]
SHORTCUT_GROUPS = [(title, [([p.strip() for p in k.split(" + ")], text) for k, text in rows]) for title, rows in _SHORTCUTS]


def _can(user, perm):
    return perm is None or user.has_perm(perm)


def _visible(user):
    return [s for s in SECTIONS if _can(user, s["perm"])]


def _section_url(slug):
    return reverse("configuration:hub_section", args=[slug])


@login_required
def hub_index(request):
    first = next(iter(_visible(request.user)), None)
    if first is None:
        raise PermissionDenied
    return redirect(_section_url(first["slug"]))


def _pos_config(store):
    return POSConfiguration.objects.get_or_create(store=store)[0] if store is not None else None


def _build_form(request, sec, data=None, files=None):
    store = request.store
    cls = sec["form"]
    kwargs = {}
    if sec["scope"] == "system":
        instance = SystemConfiguration.get()
        kwargs["store"] = store
        if cls is F.DefaultsForm:
            kwargs["pos_config"] = _pos_config(store)
    else:
        instance = _pos_config(store)
        kwargs["store"] = store
        if cls is F.ReceiptForm:
            from documents.models import InvoiceTemplate
            kwargs["invoice_template"] = InvoiceTemplate.objects.filter(store=store).order_by("-is_default", "pk").first()
    return cls(data, files, instance=instance, **kwargs) if data is not None else cls(instance=instance, **kwargs)


def _sample_sale(store, request):
    """A made-up sale for the receipt designer preview, so the preview never depends on (or exposes) real sales."""
    from datetime import datetime
    from decimal import Decimal as D
    from types import SimpleNamespace as NS

    class _All(list):
        def all(self):
            return self

    def item(name, variant, sku, qty, price, disc, tax):
        return NS(variant=NS(product=NS(name=name), name=variant, sku=sku), quantity=D(qty), unit_price=D(price),
                  discount_amount=D(disc), tax_amount=D(tax), line_total=D(qty) * D(price) - D(disc) + D(tax))
    items = _All([item("Basmati rice 5 kg", "Default", "RICE-5K", "2", "1450.00", "50.00", "0.00"),
                  item("Cooking oil 3 L", "Default", "OIL-3L", "1", "2180.00", "0.00", "0.00"),
                  item("Green tea", "Box of 25", "TEA-G25", "3", "395.00", "0.00", "0.00")])
    total = sum((i.line_total for i in items), D("0"))
    payments = _All([NS(amount=D("5000.00"), get_method_display="Cash"), NS(amount=total - D("5000.00"), get_method_display="Card")])
    return NS(
        store=store, reference="INV-000123", created_at=datetime.now(), customer=NS(name="Sample Customer", phone="0300 1234567", address="Main Boulevard, Lahore"),
        biller=NS(get_full_name=request.user.get_full_name() or request.user.get_username(), username=request.user.get_username()),
        warehouse=NS(name="Main warehouse"), items=items, payments=payments, subtotal=total + D("50.00"), discount_amount=D("50.00"),
        tax_amount=D("0.00"), shipping_amount=D("0.00"), rounding_amount=D("0.00"), total_amount=total, paid_amount=total, due_amount=D("0.00"),
        payment_status="paid", sale_note="Please keep this receipt for exchanges.",
    )


def _preview_overrides(params):
    """Design values typed in the form (not saved yet), cleaned so the preview can never be broken by a bad query string."""
    from documents.receipt_design import DESIGN_FIELDS
    from documents.models import InvoiceTemplate
    out = {}
    for name in DESIGN_FIELDS:
        if name not in params:
            continue
        field = InvoiceTemplate._meta.get_field(name)
        raw = params.get(name, "")
        if field.get_internal_type() == "BooleanField":
            out[name] = raw in ("1", "true", "on")
        elif field.choices:
            if raw in dict(field.choices):
                out[name] = raw
        else:
            out[name] = raw[: field.max_length or 2000]
    if params.get("use_brand_color") in ("1", "true", "on"):
        out["accent_color"] = ""
    return out


@login_required
def receipt_preview(request):
    """Settings > Receipt live preview: the real print page, filled with a sample sale and the design currently in the form."""
    if not _can(request.user, POS):
        raise PermissionDenied
    store = request.store
    if store is None:
        raise Http404
    from documents.receipt_design import design_for
    cfg = SystemConfiguration.get()
    d = design_for(store, cfg, _preview_overrides(request.GET))
    return render(request, "sales/invoice_print.html", {"sale": _sample_sale(store, request), "d": d, "preview": True})


def _groups(form, sec):
    out = []
    for title, names, cols in sec["layout"]:
        out.append({"title": title, "cols": cols, "fields": [form[n] for n in names if n in form.fields]})
    return out


def _extra_context(request, sec, store):
    key = sec.get("extra")
    ctx = {}
    if key == "email":
        ctx["smtp"] = smtp_status()
        ctx["test_to"] = request.user.email
    elif key == "whatsapp":
        ctx["wa"] = whatsapp_status()
    elif key == "scanners":
        from devices.models import Device
        ctx["devices"] = Device.objects.filter(store=store, type="scanner") if store else []
    elif key in ("currency", "tax"):
        pass
    elif key == "logo":
        ctx["cfg"] = SystemConfiguration.get()
    return ctx


def _send_test_email(request):
    to = (request.POST.get("test_to") or request.user.email or "").strip()
    if not to or "@" not in to:
        messages.error(request, "Type the email address that should receive the test message.")
        return
    from_email, reply_to = sender_identity()
    try:
        EmailMessage(
            subject="Test email from " + SystemConfiguration.get().site_name,
            body="This is a test message. If you can read it, your email settings work.",
            from_email=from_email, to=[to], reply_to=reply_to,
        ).send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001 — shown to the admin so they can fix the mail settings
        messages.error(request, f"The test email could not be sent: {exc}")
        return
    if smtp_status()["live"]:
        messages.success(request, f"Test email sent to {to}. Check the inbox (and spam folder).")
    else:
        messages.warning(request, "No mail server is configured, so nothing was really delivered — the message was only printed in the server console. Add EMAIL_HOST in .env to send real emails.")


def _numbering(request, sec, ctx):
    existing = set(NumberingPrefix.objects.values_list("document_type", flat=True))
    for dtype, default_prefix in NumberingPrefix.DEFAULT_PREFIXES.items():
        if dtype not in existing:
            NumberingPrefix.objects.create(document_type=dtype, prefix=default_prefix)
    if request.method == "POST":
        errors = {}
        rows = list(NumberingPrefix.objects.all())
        for row in rows:
            prefix = (request.POST.get(f"prefix_{row.pk}") or "").strip().upper()
            padding = (request.POST.get(f"padding_{row.pk}") or "").strip()
            if not prefix or len(prefix) > 15 or not all(c.isalnum() or c in "-_" for c in prefix):
                errors[row.pk] = "Use letters, digits, - or _ (up to 15 characters)."
            elif not padding.isdigit() or not 1 <= int(padding) <= 12:
                errors[row.pk] = "Length must be between 1 and 12."
            else:
                row.prefix, row.padding = prefix, int(padding)
        if errors:
            for r in rows:
                r.error = errors.get(r.pk)
            ctx["rows"] = rows
            messages.error(request, "Some prefixes need fixing — see the highlighted rows.")
            return None
        for row in rows:
            row.save(update_fields=["prefix", "padding", "updated_at"])
        messages.success(request, "Numbering saved. New documents use the new prefix; existing numbers are not changed.")
        return redirect(_section_url(sec["slug"]))
    ctx["rows"] = NumberingPrefix.objects.all()
    return None


def _email_templates(request, sec, ctx, store):
    from documents.models import EmailTemplate
    if store is None:
        return None
    qs = EmailTemplate.objects.filter(store=store)
    edit_pk, new = request.GET.get("edit"), request.GET.get("new")
    if request.method == "POST" and request.POST.get("action") == "delete":
        if not request.user.has_perm("documents.delete_emailtemplate"):
            raise PermissionDenied
        row = get_object_or_404(qs, pk=request.POST.get("pk"))
        row.delete()
        messages.success(request, "Template deleted.")
        return redirect(_section_url(sec["slug"]))
    if edit_pk or new or (request.method == "POST"):
        instance = get_object_or_404(qs, pk=edit_pk or request.POST.get("pk")) if (edit_pk or request.POST.get("pk")) else None
        need = "documents.change_emailtemplate" if instance else "documents.add_emailtemplate"
        if not request.user.has_perm(need):
            raise PermissionDenied
        form = F.EmailTemplateForm(request.POST or None, instance=instance, store=store)
        if request.method == "POST" and form.is_valid():
            form.save()
            messages.success(request, "Template saved.")
            return redirect(_section_url(sec["slug"]))
        ctx.update(form=form, editing=instance, groups=[{"title": "Template", "cols": 1, "fields": [form[n] for n in ("name", "subject", "body", "is_default")]}])
    ctx["templates_list"] = qs
    return None


@login_required
def hub_section(request, section="business"):
    sec = BY_SLUG.get(section)
    if sec is None:
        raise Http404
    if not _can(request.user, sec["perm"]):
        raise PermissionDenied
    store = request.store
    ctx = {"nav_sections": [{**s, "url": _section_url(s["slug"]), "active": s["slug"] == sec["slug"]} for s in _visible(request.user)],
           "section": sec, "store": store, "needs_store": sec.get("needs_store") and store is None}

    if ctx["needs_store"]:
        return render(request, "configuration/hub.html", ctx)

    kind = sec["kind"]
    if kind == "form":
        if request.method == "POST":
            if sec.get("extra") == "email" and request.POST.get("action") == "test_email":
                _send_test_email(request)
                return redirect(_section_url(sec["slug"]))
            form = _build_form(request, sec, request.POST, request.FILES)
            if form.is_valid():
                form.save()
                messages.success(request, f"{sec['label']} saved.")
                return redirect(_section_url(sec["slug"]))
            messages.error(request, "Some fields need attention — see the messages in red below.")
        else:
            form = _build_form(request, sec)
        ctx.update(form=form, groups=_groups(form, sec))
    elif kind == "numbering":
        resp = _numbering(request, sec, ctx)
        if resp is not None:
            return resp
    elif kind == "payments":
        from payments.models import PaymentMethod
        if store is not None:
            PaymentMethod.ensure_defaults(store)
            ctx["methods"] = PaymentMethod.objects.filter(store=store)
    elif kind == "devices":
        from devices.models import Device
        ctx["devices"] = Device.objects.filter(store=store, type=sec["device_type"]) if store else []
    elif kind == "email_templates":
        resp = _email_templates(request, sec, ctx, store)
        if resp is not None:
            return resp
    elif kind == "shortcuts":
        ctx["shortcut_groups"] = SHORTCUT_GROUPS
    elif kind == "backup":
        from core import backup
        company = getattr(request, "company", None)
        ctx["company"] = company
        ctx["saved_copies"] = backup.saved_copies("company", company_id=company.pk) if company else []

    ctx.update(_extra_context(request, sec, store))
    ctx["can_edit"] = _can(request.user, sec["perm"])
    return render(request, "configuration/hub.html", ctx)
