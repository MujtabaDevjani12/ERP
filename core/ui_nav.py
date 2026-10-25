"""
The ONE sidebar for the whole product (original POS pages + the ERP screens).

`build_nav(request)` returns sections -> entries -> items. Legacy POS links are declared here; ERP registers come from
erp_ui.registry.build_menu() (already filtered by each user's permissions) and are slotted into the matching group.
Anything that cannot be reversed or that the user may not see is dropped, so a missing module never breaks the page.
"""
from fnmatch import fnmatch

from django.urls import NoReverseMatch, reverse

from .ui_icons import icon_path

# (section title, [group keys])
SECTIONS = [
    ("Workspace", ["dashboard", "pos"]),
    ("Operations", ["sales", "catalog", "inventory", "purchasing", "production", "quality"]),
    ("Finance & people", ["accounting", "hr", "payroll"]),
    ("Insights", ["reports", "approvals"]),
    ("Control", ["masters", "admin", "settings", "billing"]),
]

# key -> (label, icon). Single-link entries are declared in SINGLES.
GROUPS = {
    "sales": ("Sales & customers", "receipt"),
    "catalog": ("Catalog & pricing", "cube"),
    "inventory": ("Inventory", "archive"),
    "purchasing": ("Purchasing", "cart"),
    "production": ("Production", "cog"),
    "quality": ("Quality control", "beaker"),
    "accounting": ("Accounting", "calculator"),
    "hr": ("HR", "id"),
    "payroll": ("Payroll", "cash"),
    "reports": ("Reports", "chart"),
    "approvals": ("Approvals & inbox", "check"),
    "masters": ("Master data", "database"),
    "admin": ("Administration", "shield"),
    "settings": ("Settings", "sliders"),
}

SINGLES = {
    "dashboard": ("Dashboard", "home", "dashboard:home", ["dashboard:home"], ""),
    "pos": ("POS terminal", "register", "dashboard:pos", ["dashboard:pos"], "F2"),
    # one link to the Settings hub; the hub's own left-hand list holds every section (configuration/hub.py)
    "settings": ("Settings", "sliders", "configuration:hub", ["configuration:*"], ""),
    # SaaS: a company's own subscription page (shown only to people who may manage billing; decided in _single_visible)
    "billing": ("Billing & plans", "credit-card", "billing:home", ["billing:home", "billing:change_plan", "billing:invoice", "billing:locked"], ""),
}

# group -> [(label, url name, [match patterns on view_name; "!x" excludes], staff only)]
LEGACY = {
    "sales": [
        ("All sales", "sales:sale_list", ["sales:sale_*", "sales:invoice_*"], False),
        ("Deliveries", "sales:delivery_list", ["sales:delivery_*"], False),
        ("Customer feedback", "sales:feedback_list", ["sales:feedback_*"], False),
        ("Customers", "customers:customer_list", ["customers:customer_*"], False),
        ("Billers", "accounts:biller_list", ["accounts:biller_*"], True),
        ("Gift cards", "customers:giftcard_list", ["customers:giftcard_*"], False),
        ("Registers & shifts", "register:session_list", ["register:session_*", "register:open", "register:close"], False),
        ("Counters", "register:counter_list", ["register:counter_*"], False),
        ("Payment methods", "payments:method_list", ["payments:method_*"], False),
        ("Document log", "documents:document_log_list", ["documents:*"], False),
    ],
    "catalog": [
        ("Products", "catalog:product_list", ["catalog:product_*", "catalog:api_*", "catalog:variant_*"], False),
        ("Print barcodes", "catalog:barcode_print", ["catalog:barcode_*"], False),
        ("Categories", "catalog:category_list", ["catalog:category_*"], False),
        ("Brands", "catalog:brand_list", ["catalog:brand_*"], False),
        ("Units", "catalog:unit_list", ["catalog:unit_*"], False),
        ("Attributes", "catalog:attribute_list", ["catalog:attribute_*"], False),
        ("Taxes", "pricing:tax_list", ["pricing:tax_*"], False),
        ("Discounts", "pricing:discount_list", ["pricing:discount_*"], False),
        ("Customer price groups", "pricing:group_list", ["pricing:group_*"], False),
    ],
    "inventory": [
        ("Stock levels", "inventory:stock_list", ["inventory:stock_*"], False),
        ("Quick scan adjust", "inventory:quantity_adjustment", ["inventory:quantity_adjustment"], False),
        ("Adjustment history", "inventory:adjustment_list", ["inventory:adjustment_*"], False),
        ("Stock transfers", "inventory:transfer_list", ["inventory:transfer_*"], False),
        ("Warehouses", "inventory:warehouse_list", ["inventory:warehouse_*"], False),
        ("Racks & locations", "inventory:rack_list", ["inventory:rack_*"], False),
    ],
    "purchasing": [
        ("Suppliers", "suppliers:supplier_list", ["suppliers:supplier_*"], False),
        ("Purchase orders", "suppliers:purchase_list", ["suppliers:*", "!suppliers:supplier_*"], False),
    ],
    "accounting": [
        ("Expenses", "expenses:expense_list", ["expenses:*", "!expenses:category_*"], False),
        ("Expense categories", "expenses:category_list", ["expenses:category_*"], False),
    ],
    "reports": [
        ("All reports", "reports:pack_index", ["reports:pack_*"], False),
        ("Import & export", "reports:data_hub", ["reports:data_*"], False),
        ("Sales & stock reports", "reports:dashboard", ["reports:*", "!reports:pack_*", "!reports:data_*"], False),
        ("ERP dashboard & reports", "reports_erp:dashboard", ["reports_erp:*"], False),
    ],
    "approvals": [
        ("Notifications", "notifications:inbox", ["notifications:*"], False),
    ],
    "admin": [
        ("Staff users", "accounts:staff_user_list", ["accounts:*user*"], True),
        ("Roles & permissions", "accounts:role_list", ["accounts:*role*"], True),
        ("Printers & devices", "devices:device_list", ["devices:*"], False),
    ],
    "settings": [
        ("POS configuration", "configuration:pos_settings", ["configuration:pos_settings"], False),
        ("System & branding", "configuration:system_settings", ["configuration:system_settings"], False),
        ("Currencies", "configuration:currency_list", ["configuration:currency_*"], False),
        ("Number prefixes", "configuration:numbering_prefixes", ["configuration:numbering_prefixes"], False),
    ],
}

# url name -> permission needed to SEE the link (a str, or a tuple = any of). A link that is not listed is shown to everyone
# signed in. Super Admins hold every permission, so they always see everything.
LEGACY_PERMS = {
    "sales:sale_list": "sales.view_sale",
    "sales:delivery_list": "sales.view_delivery",
    "sales:feedback_list": "sales.view_salefeedback",
    "customers:customer_list": "customers.view_customer",
    "accounts:biller_list": "accounts.view_user",
    "customers:giftcard_list": "customers.view_giftcard",
    "register:session_list": "register.view_registersession",
    "register:counter_list": "register.view_register",
    "payments:method_list": "payments.view_paymentmethod",
    "documents:document_log_list": "sales.view_sale",
    "catalog:product_list": "catalog.view_product",
    "catalog:barcode_print": "catalog.view_product",
    "catalog:category_list": "catalog.view_category",
    "catalog:brand_list": "catalog.view_brand",
    "catalog:unit_list": "catalog.view_unit",
    "catalog:attribute_list": "catalog.view_productattribute",
    "pricing:tax_list": "pricing.view_tax",
    "pricing:discount_list": "pricing.view_discount",
    "pricing:group_list": "pricing.view_customergroup",
    "inventory:stock_list": "inventory.view_stockitem",
    "inventory:quantity_adjustment": "inventory.add_stockadjustment",
    "inventory:adjustment_list": "inventory.view_stockadjustment",
    "inventory:transfer_list": "inventory.view_stocktransfer",
    "inventory:warehouse_list": "inventory.view_warehouse",
    "inventory:rack_list": "inventory.view_rack",
    "suppliers:supplier_list": "suppliers.view_supplier",
    "suppliers:purchase_list": "suppliers.view_purchaseorder",
    "expenses:expense_list": "expenses.view_expense",
    "expenses:category_list": "expenses.view_expensecategory",
    "reports:pack_index": ("sales.view_sale", "payments.view_payment", "inventory.view_stockitem", "expenses.view_expense", "customers.view_customer"),
    "reports:data_hub": ("catalog.view_product", "customers.view_customer", "suppliers.view_supplier"),
    "reports:dashboard": "sales.view_sale",
    "devices:device_list": "devices.view_device",
    "accounts:staff_user_list": "accounts.view_user",
    "accounts:role_list": "auth.view_group",
    "configuration:pos_settings": "configuration.change_posconfiguration",
    "configuration:system_settings": "configuration.change_systemconfiguration",
    "configuration:currency_list": "configuration.view_currency",
    "configuration:numbering_prefixes": "configuration.change_numberingprefix",
}


def _allowed(user, url_name):
    need = LEGACY_PERMS.get(url_name)
    if need is None:
        return None                                            # not mapped
    if isinstance(need, str):
        need = (need,)
    return any(user.has_perm(p) for p in need)


# ERP module app label -> sidebar group
ERP_GROUP = {
    "distribution": "sales", "pos_erp": "sales",
    "stock": "inventory",
    "procurement": "purchasing",
    "production": "production",
    "quality": "quality",
    "accounting": "accounting",
    "hr": "hr",
    "payroll": "payroll",
    "approvals": "approvals", "notifications": "approvals",
    "masters": "masters",
    "rbac": "admin", "audit": "admin", "company": "admin", "tenants": "admin", "numbering": "admin", "attachments": "admin",
}


def _url(name):
    try:
        return reverse(name)
    except NoReverseMatch:
        return None


def _matches(view_name, patterns):
    if not view_name:
        return False
    positive = [p for p in patterns if not p.startswith("!")]
    negative = [p[1:] for p in patterns if p.startswith("!")]
    return any(fnmatch(view_name, p) for p in positive) and not any(fnmatch(view_name, p) for p in negative)


def _erp_modules(request):
    try:
        from erp_ui.registry import build_menu
    except ImportError:
        return []
    try:
        return build_menu(request)
    except Exception:                                           # noqa: BLE001 - the menu must never break a page
        return []


def _plan_filter(request):
    """app label -> True when the company's plan includes it. The platform owner and companies without a subscription see everything."""
    try:
        from billing import access
        from billing.modules import module_for_app
    except ImportError:
        return lambda app: True
    if access.is_platform_owner(request.user):
        return lambda app: True
    if getattr(request, "subscription_state", None) in ("pending", "expired", "suspended"):
        return lambda app: app == "billing"               # locked company: the menu shrinks to Billing
    mods = access.allowed_modules(getattr(request, "company", None))
    if mods is None:
        return lambda app: True
    return lambda app: module_for_app(app) is None or module_for_app(app) in mods


def plan_allows(request, *apps):
    """True when the company's plan includes every app label given (always true for the owner / unsubscribed companies)."""
    ok = _plan_filter(request)
    return all(ok(a) for a in apps)


def _app_of(url_name):
    return "sales" if url_name == "dashboard:pos" else url_name.split(":")[0]


def _single_visible(key, request):
    """The Billing link only shows to people who may manage their company's billing (the owner console is a separate site)."""
    if key != "billing":
        return True
    try:
        from billing import access
    except ImportError:
        return False
    user = request.user
    if access.is_platform_owner(user):
        return False
    return access.managed(getattr(request, "company", None)) and user.has_perm("billing.view_subscription")


def build_nav(request):
    user = request.user
    plan_ok = _plan_filter(request)
    match = getattr(request, "resolver_match", None)
    view_name = match.view_name if match else ""
    modules = _erp_modules(request)
    erp_by_group = {}
    for m in modules:
        if not plan_ok(m["app"]):
            continue
        erp_by_group.setdefault(ERP_GROUP.get(m["app"], "admin"), []).append(m)

    sections = []
    for section_title, keys in SECTIONS:
        entries = []
        for key in keys:
            if key in SINGLES:
                label, icon, url_name, patterns, tip = SINGLES[key]
                if not _single_visible(key, request) or not plan_ok(_app_of(url_name)):
                    continue
                url = _url(url_name)
                if url:
                    entries.append({"type": "link", "key": key, "label": label, "icon": icon_path(icon), "url": url,
                                    "active": _matches(view_name, patterns), "hint": tip})
                continue
            label, icon = GROUPS[key]
            items = []
            legacy_count = 0
            for item_label, url_name, patterns, staff_only in LEGACY.get(key, []):
                if not plan_ok(_app_of(url_name)):
                    continue
                allowed = _allowed(user, url_name)
                if allowed is False:
                    continue
                if allowed is None and staff_only and not user.is_staff:
                    continue
                url = _url(url_name)
                if not url:
                    continue
                items.append({"type": "link", "label": item_label, "url": url, "active": _matches(view_name, patterns)})
                legacy_count += 1
            erp = erp_by_group.get(key, [])
            for m in erp:
                if legacy_count or len(erp) > 1:
                    items.append({"type": "label", "label": m["title"] if len(erp) > 1 else "ERP registers"})
                for it in m["items"]:
                    items.append({"type": "link", "label": it["label"], "url": it["url"], "active": it["active"]})
            links = [i for i in items if i["type"] == "link"]
            if not links:
                continue
            entries.append({"type": "group", "key": key, "label": label, "icon": icon_path(icon), "items": items,
                            "active": any(i["active"] for i in links), "count": len(links)})
        if entries:
            sections.append({"title": section_title, "entries": entries})
    return sections


def build_commands(nav, request):
    """Entries for the Ctrl+K palette: every page the user can open, plus a few quick actions."""
    commands = []
    for section in nav:
        for e in section["entries"]:
            if e["type"] == "link":
                commands.append({"kind": "nav", "title": e["label"], "url": e["url"], "hint": section["title"], "keywords": ""})
            else:
                for it in e["items"]:
                    if it["type"] == "link":
                        commands.append({"kind": "nav", "title": it["label"], "url": it["url"], "hint": e["label"], "keywords": e["label"]})
    pos = _url("dashboard:pos")
    if pos and request.user.has_perm("sales.add_sale") and plan_allows(request, "sales"):
        commands.append({"kind": "action", "title": "Open POS terminal", "url": pos, "hint": "F2", "keywords": "sale billing checkout"})
    for title, name, kw, perm in (("Add product", "catalog:product_add", "new item sku", "catalog.add_product"),
                                  ("Add customer", "customers:customer_add", "new client", "customers.add_customer"),
                                  ("Add supplier", "suppliers:supplier_add", "new vendor", "suppliers.add_supplier"),
                                  ("New purchase order", "suppliers:purchase_add", "buy stock", "suppliers.add_purchaseorder")):
        url = _url(name)
        if url and request.user.has_perm(perm) and plan_allows(request, _app_of(name)):
            commands.append({"kind": "action", "title": title, "url": url, "hint": "Create", "keywords": kw})
    commands.append({"kind": "action", "title": "Toggle dark mode", "url": "", "action": "theme", "hint": "Appearance", "keywords": "light theme night"})
    commands.append({"kind": "action", "title": "Keyboard shortcuts", "url": "", "action": "shortcuts", "hint": "?", "keywords": "help keys"})
    return commands
