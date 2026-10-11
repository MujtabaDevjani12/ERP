from django import forms

from .models import Currency, POSConfiguration, SystemConfiguration


class POSConfigurationForm(forms.ModelForm):
    class Meta:
        model = POSConfiguration
        exclude = ["store", "created_at", "updated_at"]
        widgets = {
            "product_button_color": forms.TextInput(attrs={"type": "color"}),
        }


class SystemConfigurationForm(forms.ModelForm):
    class Meta:
        model = SystemConfiguration
        exclude = ["created_at", "updated_at"]
        widgets = {
            "primary_color": forms.TextInput(attrs={"type": "color"}),
            "secondary_color": forms.TextInput(attrs={"type": "color"}),
        }


class CurrencyForm(forms.ModelForm):
    class Meta:
        model = Currency
        fields = ["code", "name", "symbol", "rate", "decimals", "is_active"]
        labels = {"code": "Currency code", "name": "Name", "symbol": "Symbol", "rate": "Exchange rate", "decimals": "Decimal places", "is_active": "Active"}
        widgets = {
            "code": forms.TextInput(attrs={"placeholder": "USD", "maxlength": 6, "autocomplete": "off", "style": "text-transform:uppercase"}),
            "name": forms.TextInput(attrs={"placeholder": "US Dollar"}),
            "symbol": forms.TextInput(attrs={"placeholder": "$"}),
            "rate": forms.NumberInput(attrs={"step": "any", "min": "0", "placeholder": "280"}),
            "decimals": forms.NumberInput(attrs={"min": "0", "max": "4"}),
        }

    def __init__(self, *args, store=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store
        if store is not None:
            self.fields["rate"].help_text = f"How many {store.currency_code} equal ONE unit of this currency. Example: 280 if 1 USD = 280 {store.currency_code}."

    def clean_code(self):
        code = (self.cleaned_data.get("code") or "").strip().upper()
        if not code:
            raise forms.ValidationError("Enter the currency code.")
        if self.store is not None:
            if code == (self.store.currency_code or "").upper():
                raise forms.ValidationError(f"{code} is already your main currency. Add only the other currencies here.")
            if Currency.objects.filter(store=self.store, code=code).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError(f"{code} is already in the list. Edit it instead.")
        return code

    def clean_rate(self):
        rate = self.cleaned_data.get("rate")
        if rate is None or rate <= 0:
            raise forms.ValidationError("The exchange rate must be more than zero.")
        return rate

    def clean_decimals(self):
        d = self.cleaned_data.get("decimals")
        if d is None or d > 4:
            raise forms.ValidationError("Use 0 to 4 decimal places.")
        return d


# ====================================================================================== Settings hub forms
# One small form per section of Settings (see hub.py). Each only touches the fields its panel shows, so saving one panel can
# never blank a setting that lives in another.
import re
import string

from django.contrib.auth import get_user_model

from documents.models import EmailTemplate

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _hex(value, label):
    # these values are written into <style>/<script> by base.html, so only a strict #rrggbb is accepted
    value = (value or "").strip()
    if not HEX_RE.match(value):
        raise forms.ValidationError(f"{label} must be a colour like #4f46e5.")
    return value.lower()


def timezone_choices(current=""):
    try:
        import zoneinfo
        names = sorted(zoneinfo.available_timezones())
    except Exception:  # noqa: BLE001
        names = []
    if not names:
        names = ["Asia/Karachi", "Asia/Dubai", "Asia/Riyadh", "Europe/London", "America/New_York", "UTC"]
    if current and current not in names:
        names.append(current)
    return [(n, n) for n in names]


class _SystemSectionForm(forms.ModelForm):
    """Base for every panel stored on the SystemConfiguration singleton."""
    class Meta:
        model = SystemConfiguration
        fields: list = []

    def __init__(self, *args, store=None, **kwargs):
        self.store = store
        super().__init__(*args, **kwargs)


class BusinessForm(_SystemSectionForm):
    phone = forms.CharField(max_length=30, required=False, widget=forms.TextInput(attrs={"placeholder": "+92 300 1234567", "type": "tel"}))
    email = forms.EmailField(required=False, widget=forms.EmailInput(attrs={"placeholder": "info@yourshop.com"}))
    address = forms.CharField(max_length=255, required=False, widget=forms.Textarea(attrs={"rows": 2, "placeholder": "Shop address printed on receipts"}))

    class Meta:
        model = SystemConfiguration
        fields = ["site_name", "tagline", "phone", "email", "website", "tax_number", "address", "default_timezone", "rows_per_page", "quantity_decimals"]
        labels = {"site_name": "Business name", "tagline": "Tagline", "default_timezone": "Time zone", "rows_per_page": "Default rows per page", "quantity_decimals": "Quantity decimals"}
        widgets = {
            "site_name": forms.TextInput(attrs={"placeholder": "Your business name"}),
            "tagline": forms.TextInput(attrs={"placeholder": "Retail & wholesale"}),
            "website": forms.TextInput(attrs={"placeholder": "www.yourshop.com"}),
            "tax_number": forms.TextInput(attrs={"placeholder": "1234567-8"}),
            "rows_per_page": forms.NumberInput(attrs={"min": 5, "max": 200}),
            "quantity_decimals": forms.NumberInput(attrs={"min": 0, "max": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["site_name"].required = True
        self.fields["default_timezone"] = forms.ChoiceField(label="Time zone", choices=timezone_choices(self.instance.default_timezone))
        self.order_fields(self.Meta.fields)
        if self.store is not None and not self.is_bound:
            self.initial.update({"phone": self.store.phone, "email": self.store.email, "address": self.store.address})
        self.fields["address"].help_text = "Shown on invoices and receipts of this branch."
        for name in ("tagline", "tax_number"):
            self.fields[name].help_text = ""                 # keeps the two-column rows aligned; the labels say it all

    def clean_rows_per_page(self):
        v = self.cleaned_data["rows_per_page"]
        if not 5 <= v <= 200:
            raise forms.ValidationError("Use a number between 5 and 200.")
        return v

    def clean_quantity_decimals(self):
        v = self.cleaned_data["quantity_decimals"]
        if v > 4:
            raise forms.ValidationError("Use 0 to 4 decimals.")
        return v

    def save(self, commit=True):
        obj = super().save(commit=commit)
        if self.store is not None:
            self.store.phone = self.cleaned_data.get("phone", "")
            self.store.email = self.cleaned_data.get("email", "")
            self.store.address = self.cleaned_data.get("address", "")
            self.store.timezone = obj.default_timezone
            self.store.save(update_fields=["phone", "email", "address", "timezone", "updated_at"])
        return obj


class BrandingForm(_SystemSectionForm):
    class Meta:
        model = SystemConfiguration
        fields = ["site_logo", "primary_color"]
        labels = {"site_logo": "Logo", "primary_color": "Brand colour"}
        widgets = {"primary_color": forms.TextInput(attrs={"type": "color"})}

    def clean_primary_color(self):
        return _hex(self.cleaned_data["primary_color"], "Brand colour")

    def clean_site_logo(self):
        f = self.cleaned_data.get("site_logo")
        if f and hasattr(f, "size") and f.size > 2 * 1024 * 1024:
            raise forms.ValidationError("The logo must be smaller than 2 MB.")
        return f


def _contrast_with_white(hex_value):
    """WCAG contrast ratio of a #rrggbb colour against white (4.5 or more is comfortable body text)."""
    r, g, b = (int(hex_value[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 1.05 / (0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2] + 0.05)


class MenuFontForm(_SystemSectionForm):
    class Meta:
        model = SystemConfiguration
        fields = ["secondary_color", "font_family", "font_size", "text_color"]
        labels = {"secondary_color": "Menu (sidebar) colour", "font_family": "Font", "font_size": "Text size", "text_color": "Text colour"}
        help_texts = {"font_size": "Makes everything slightly smaller or larger.",
                      "text_color": "Main text colour as #rrggbb, or empty for the default. It must be dark enough to read on white."}
        widgets = {"secondary_color": forms.TextInput(attrs={"type": "color"}), "text_color": forms.TextInput(attrs={"placeholder": "#111827", "maxlength": 7})}

    def clean_secondary_color(self):
        return _hex(self.cleaned_data["secondary_color"], "Menu colour")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["font_size"].required = False                     # an old form post without it keeps the current size

    def clean_font_size(self):
        return self.cleaned_data.get("font_size") or self.instance.font_size or "normal"

    def clean_text_color(self):
        value = (self.cleaned_data.get("text_color") or "").strip()
        if not value:
            return ""
        value = _hex(value, "Text colour")
        if _contrast_with_white(value) < 4.5:
            raise forms.ValidationError("This colour is too light to read on a white page. Pick a darker one.")
        return value


class CurrencySettingsForm(_SystemSectionForm):
    class Meta:
        model = SystemConfiguration
        fields = ["default_currency_code", "default_currency_symbol", "currency_position", "decimals", "decimal_separator", "thousands_separator", "display_currency_symbol"]
        labels = {"currency_position": "Symbol position", "default_currency_code": "Currency code", "default_currency_symbol": "Currency symbol", "decimals": "Decimals for money",
                  "decimal_separator": "Decimal separator", "thousands_separator": "Thousands separator", "display_currency_symbol": "Show the currency symbol next to amounts"}
        widgets = {
            "default_currency_code": forms.TextInput(attrs={"placeholder": "PKR", "style": "text-transform:uppercase"}),
            "default_currency_symbol": forms.TextInput(attrs={"placeholder": "Rs"}),
            "decimals": forms.NumberInput(attrs={"min": 0, "max": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["currency_position"].required = False

    def clean_currency_position(self):
        return self.cleaned_data.get("currency_position") or self.instance.currency_position or "before"

    def clean_default_currency_code(self):
        v = (self.cleaned_data["default_currency_code"] or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{3,6}", v):
            raise forms.ValidationError("Use the letters of the currency code, e.g. PKR or USD.")
        return v

    def clean_decimals(self):
        v = self.cleaned_data["decimals"]
        if v > 4:
            raise forms.ValidationError("Use 0 to 4 decimals.")
        return v

    def clean(self):
        data = super().clean()
        if data.get("decimal_separator") and data.get("decimal_separator") == data.get("thousands_separator"):
            self.add_error("thousands_separator", "The thousands separator must be different from the decimal separator.")
        return data

    def save(self, commit=True):
        obj = super().save(commit=commit)
        if self.store is not None:
            # the store's own currency is what receipts, reports and the POS read, so it follows this setting
            self.store.currency_code = obj.default_currency_code
            self.store.currency_symbol = obj.default_currency_symbol
            self.store.save(update_fields=["currency_code", "currency_symbol", "updated_at"])
        return obj


class StockForm(_SystemSectionForm):
    class Meta:
        model = SystemConfiguration
        fields = ["update_cost_with_purchase", "allow_over_selling", "enable_product_variants", "enable_racks", "enable_product_expiry",
                  "remove_expired_from_stock", "edit_lock_days"]
        labels = {"update_cost_with_purchase": "Update cost price from purchases", "allow_over_selling": "Allow selling more than the stock on hand",
                  "enable_product_variants": "Product variants (size, colour…)", "enable_racks": "Racks / shelf locations",
                  "enable_product_expiry": "Product expiry dates", "remove_expired_from_stock": "Take expired products out of stock automatically",
                  "edit_lock_days": "Lock sales & purchases after (days)"}
        widgets = {"edit_lock_days": forms.NumberInput(attrs={"min": 0})}


class TaxDiscountForm(_SystemSectionForm):
    class Meta:
        model = SystemConfiguration
        fields = ["order_tax_inclusive", "allow_product_level_discount"]
        labels = {"order_tax_inclusive": "Prices already include tax", "allow_product_level_discount": "Allow a discount on each item (not only on the whole bill)"}


class RewardForm(_SystemSectionForm):
    class Meta:
        model = SystemConfiguration
        fields = ["award_points_per_amount", "award_points_value"]
        labels = {"award_points_per_amount": "Spend this much…", "award_points_value": "…to earn this many points"}
        widgets = {"award_points_per_amount": forms.NumberInput(attrs={"min": 1, "step": "any"}), "award_points_value": forms.NumberInput(attrs={"min": 0})}

    def clean_award_points_per_amount(self):
        v = self.cleaned_data["award_points_per_amount"]
        if v is None or v <= 0:
            raise forms.ValidationError("Enter an amount greater than zero.")
        return v


class DefaultsForm(_SystemSectionForm):
    """System defaults + the POS defaults of this branch, in one panel (they are the same idea to the owner)."""
    pos_default_warehouse = forms.ModelChoiceField(queryset=None, required=False, label="POS warehouse", empty_label="Branch default warehouse")
    pos_default_biller = forms.ModelChoiceField(queryset=None, required=False, label="POS biller", empty_label="Whoever is signed in")
    pos_default_customer = forms.ModelChoiceField(queryset=None, required=False, label="POS customer", empty_label="Walk-in customer")
    pos_default_category = forms.ModelChoiceField(queryset=None, required=False, label="POS category", empty_label="All categories")

    class Meta:
        model = SystemConfiguration
        fields = ["default_warehouse", "default_language", "date_format"]
        labels = {"default_warehouse": "Default warehouse", "default_language": "Language", "date_format": "Date format"}

    def __init__(self, *args, pos_config=None, **kwargs):
        super().__init__(*args, **kwargs)
        from catalog.models import Category
        from customers.models import Customer
        from tenants.models import Warehouse
        store = self.store
        User = get_user_model()
        self.pos_config = pos_config
        self.fields["default_warehouse"].queryset = Warehouse.objects.filter(store=store) if store else Warehouse.objects.none()
        self.fields["default_warehouse"].empty_label = "Branch default warehouse"
        self.fields["pos_default_warehouse"].queryset = Warehouse.objects.filter(store=store) if store else Warehouse.objects.none()
        self.fields["pos_default_biller"].queryset = User.objects.filter(store=store, is_biller=True) if store else User.objects.none()
        self.fields["pos_default_customer"].queryset = Customer.objects.filter(store=store) if store else Customer.objects.none()
        self.fields["pos_default_category"].queryset = Category.objects.filter(store=store) if store else Category.objects.none()
        if pos_config is not None and not self.is_bound:
            self.initial.update({
                "pos_default_warehouse": pos_config.default_warehouse_id, "pos_default_biller": pos_config.default_biller_id,
                "pos_default_customer": pos_config.default_customer_id, "pos_default_category": pos_config.default_category_id,
            })

    def save(self, commit=True):
        obj = super().save(commit=commit)
        pc = self.pos_config
        if pc is not None:
            pc.default_warehouse = self.cleaned_data.get("pos_default_warehouse")
            pc.default_biller = self.cleaned_data.get("pos_default_biller")
            pc.default_customer = self.cleaned_data.get("pos_default_customer")
            pc.default_category = self.cleaned_data.get("pos_default_category")
            pc.save()
        return obj


class _POSSectionForm(forms.ModelForm):
    class Meta:
        model = POSConfiguration
        fields: list = []

    def __init__(self, *args, store=None, **kwargs):
        self.store = store
        super().__init__(*args, **kwargs)


class POSBehaviourForm(_POSSectionForm):
    class Meta:
        model = POSConfiguration
        fields = ["products_per_page", "item_order", "after_sale_page", "rounding", "product_button_color", "pos_access_pin",
                  "show_clock", "show_tooltips", "on_screen_keyboard", "require_customer"]
        labels = {"products_per_page": "Products per page", "item_order": "Product order", "after_sale_page": "After a sale", "rounding": "Round the total",
                  "product_button_color": "Product tile colour", "pos_access_pin": "POS PIN", "show_clock": "Show the clock",
                  "show_tooltips": "Show tooltips", "on_screen_keyboard": "On-screen keyboard (touch screens)",
                  "require_customer": "Ask for a customer before adding items"}
        widgets = {"product_button_color": forms.TextInput(attrs={"type": "color"}), "pos_access_pin": forms.TextInput(attrs={"inputmode": "numeric", "maxlength": 6, "autocomplete": "off", "placeholder": "Leave empty for no PIN"}),
                   "products_per_page": forms.NumberInput(attrs={"min": 6, "max": 200})}

    def clean_product_button_color(self):
        return _hex(self.cleaned_data["product_button_color"], "Product tile colour")

    def clean_pos_access_pin(self):
        v = (self.cleaned_data.get("pos_access_pin") or "").strip()
        if v and not v.isdigit():
            raise forms.ValidationError("The PIN can contain digits only.")
        return v

    def clean_products_per_page(self):
        v = self.cleaned_data["products_per_page"]
        if not 6 <= v <= 200:
            raise forms.ValidationError("Use a number between 6 and 200.")
        return v


class ReceiptForm(_POSSectionForm):
    """Receipt / invoice of this branch: POS options + the default InvoiceTemplate (paper, look, what is printed, wording)."""
    # the template (documents.InvoiceTemplate) fields the designer edits - built from the model so labels/choices stay in one place
    DESIGN_LABELS = {
        "paper": "Paper", "style": "Style", "header_align": "Heading alignment", "title_text": "Document title",
        "font_style": "Font", "font_size": "Text size", "header_note": "Line under the shop details",
        "show_logo": "Logo", "show_address": "Shop address", "show_contact": "Phone, email and website", "show_tax_number": "Tax / NTN number",
        "show_biller": "Cashier name", "show_warehouse": "Warehouse", "show_item_sku": "Item codes (SKU)", "show_item_discount": "Item discounts",
        "show_item_tax": "Item tax", "show_payments": "Payment methods used", "show_notes": "Sale note",
        "footer_text": "Footer message", "terms_and_conditions": "Terms & conditions",
    }
    DESIGN_PLACEHOLDERS = {"title_text": "INVOICE", "header_note": "e.g. Open daily 9am – 10pm",
                           "footer_text": "Thank you for shopping with us!",
                           "terms_and_conditions": "e.g. Goods once sold can be exchanged within 7 days with this receipt."}
    BOOLEAN_DESIGN = ["show_logo", "show_address", "show_contact", "show_tax_number", "show_biller", "show_warehouse",
                      "show_item_sku", "show_item_discount", "show_item_tax", "show_payments", "show_notes"]
    CHOICE_DESIGN = ["paper", "style", "header_align", "font_style", "font_size"]
    TEXT_DESIGN = ["title_text", "header_note", "footer_text", "terms_and_conditions"]
    use_brand_color = forms.BooleanField(required=False, label="Use my brand colour")
    accent_color = forms.CharField(required=False, label="Accent colour", widget=forms.TextInput(attrs={"type": "color"}))

    class Meta:
        model = POSConfiguration
        fields = ["auto_print_receipt", "show_customer_details_on_receipt", "printing_mode", "receipt_custom_field1_title", "receipt_custom_field1_value",
                  "receipt_custom_field2_title", "receipt_custom_field2_value"]
        labels = {"auto_print_receipt": "Print automatically when a sale is completed",
                  "show_customer_details_on_receipt": "Print the customer's details", "printing_mode": "Printing method",
                  "receipt_custom_field1_title": "Extra line 1 — label", "receipt_custom_field1_value": "Extra line 1 — text",
                  "receipt_custom_field2_title": "Extra line 2 — label", "receipt_custom_field2_value": "Extra line 2 — text"}

    def __init__(self, *args, invoice_template=None, **kwargs):
        super().__init__(*args, **kwargs)
        from documents.models import InvoiceTemplate
        self.invoice_template = invoice_template
        for name in self.CHOICE_DESIGN + self.BOOLEAN_DESIGN + self.TEXT_DESIGN:
            model_field = InvoiceTemplate._meta.get_field(name)
            field = model_field.formfield(label=self.DESIGN_LABELS[name], required=False, help_text="")
            if name in self.BOOLEAN_DESIGN:
                field = forms.BooleanField(required=False, label=self.DESIGN_LABELS[name])
            elif name in self.CHOICE_DESIGN:
                field = forms.ChoiceField(required=False, label=self.DESIGN_LABELS[name], choices=model_field.choices)
            elif name == "terms_and_conditions":
                field.widget = forms.Textarea(attrs={"rows": 3})
            if name in self.DESIGN_PLACEHOLDERS:
                field.widget.attrs["placeholder"] = self.DESIGN_PLACEHOLDERS[name]
            self.fields[name] = field
        self.fields["auto_print_receipt"].help_text = "The print dialog opens by itself right after payment, without leaving the POS."
        if not self.is_bound:
            from documents.receipt_design import resolve
            values = resolve(invoice_template)                    # saved design, or the defaults for a branch that never set one
            for name in self.CHOICE_DESIGN + self.BOOLEAN_DESIGN + self.TEXT_DESIGN:
                self.initial[name] = getattr(values, name)
            saved = (getattr(invoice_template, "accent_color", "") or "")
            self.initial["use_brand_color"] = not saved
            self.initial["accent_color"] = saved or "#4f46e5"

    def clean_accent_color(self):
        value = (self.cleaned_data.get("accent_color") or "").strip()
        if value and not re.match(r"^#[0-9a-fA-F]{6}$", value):
            raise forms.ValidationError("Pick a colour from the colour box.")
        return value.lower()

    def clean_title_text(self):
        return (self.cleaned_data.get("title_text") or "").strip() or "INVOICE"

    def save(self, commit=True):
        obj = super().save(commit=commit)
        from documents.models import InvoiceTemplate
        tpl = self.invoice_template or InvoiceTemplate(store=self.store, name="Default", is_default=True)
        for name in self.CHOICE_DESIGN + self.BOOLEAN_DESIGN + self.TEXT_DESIGN:
            value = self.cleaned_data.get(name, "" if name in self.TEXT_DESIGN else False)
            if name in self.CHOICE_DESIGN and not value:
                continue                                  # a choice left out of the request keeps what the template already has
            setattr(tpl, name, value)
        tpl.accent_color = "" if self.cleaned_data.get("use_brand_color") else self.cleaned_data.get("accent_color", "")
        tpl.save()
        return obj


class ScannerForm(_SystemSectionForm):
    class Meta:
        model = SystemConfiguration
        fields = ["auto_detect_barcode_scan", "barcode_scan_min_chars", "barcode_renderer", "barcode_separator"]
        labels = {"auto_detect_barcode_scan": "Detect barcode scanners automatically", "barcode_scan_min_chars": "Shortest barcode (characters)",
                  "barcode_renderer": "Barcode type for printed labels", "barcode_separator": "Separator inside generated barcodes"}
        widgets = {"barcode_scan_min_chars": forms.NumberInput(attrs={"min": 1, "max": 30})}


class EmailDeliveryForm(_SystemSectionForm):
    class Meta:
        model = SystemConfiguration
        fields = ["email_from_name", "email_from_address", "email_reply_to"]
        labels = {"email_from_name": "Sender name", "email_from_address": "Sender address", "email_reply_to": "Reply-to address"}
        widgets = {"email_from_name": forms.TextInput(attrs={"placeholder": "Your business name"}),
                   "email_from_address": forms.EmailInput(attrs={"placeholder": "billing@yourshop.com"}),
                   "email_reply_to": forms.EmailInput(attrs={"placeholder": "support@yourshop.com"})}


WHATSAPP_FIELDS = {"customer_name", "store_name", "reference", "total"}


class WhatsAppForm(_SystemSectionForm):
    class Meta:
        model = SystemConfiguration
        fields = ["whatsapp_country_code", "whatsapp_message"]
        labels = {"whatsapp_country_code": "Default country code", "whatsapp_message": "Invoice message"}
        widgets = {"whatsapp_country_code": forms.TextInput(attrs={"inputmode": "numeric", "maxlength": 5, "placeholder": "92"}),
                   "whatsapp_message": forms.Textarea(attrs={"rows": 4})}

    def clean_whatsapp_country_code(self):
        v = (self.cleaned_data.get("whatsapp_country_code") or "").strip().lstrip("+")
        if not v.isdigit():
            raise forms.ValidationError("Digits only, without the + sign — e.g. 92.")
        return v

    def clean_whatsapp_message(self):
        v = self.cleaned_data.get("whatsapp_message") or ""
        try:
            names = {f[1] for f in string.Formatter().parse(v) if f[1]}
        except ValueError:
            raise forms.ValidationError("Check the { } brackets in the message — each one must be a complete placeholder like {reference}.")
        bad = sorted(names - WHATSAPP_FIELDS)
        if bad:
            raise forms.ValidationError("Unknown placeholder: " + ", ".join("{" + b + "}" for b in bad) + ". Allowed: {customer_name} {store_name} {reference} {total}.")
        return v


class EmailTemplateForm(forms.ModelForm):
    class Meta:
        model = EmailTemplate
        fields = ["name", "subject", "body", "is_default"]
        labels = {"name": "Template name", "subject": "Subject", "body": "Message", "is_default": "Use this template for invoice emails"}
        widgets = {"body": forms.Textarea(attrs={"rows": 8})}

    def __init__(self, *args, store=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.store = store

    def clean(self):
        data = super().clean()
        from django.template import Context, Template, TemplateSyntaxError
        for f in ("subject", "body"):
            try:
                Template(data.get(f) or "").render(Context({}))
            except TemplateSyntaxError as exc:
                self.add_error(f, f"There is a mistake in the placeholders: {exc}")
        return data

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.store = self.store
        obj.save()
        if obj.is_default:
            type(obj).objects.filter(store=self.store).exclude(pk=obj.pk).update(is_default=False)
        return obj
