"""
Which models show up in the ERP screens, how they are grouped in the sidebar, and who may see what.

The screens are generated from the ModelAdmin classes the ERP apps already register, so every form, inline table,
search, filter, permission rule and workflow action (Submit / Approve / Receive / Pay ...) stays defined in ONE place
(each app's admin.py). The Django admin site itself is not used for display any more; it only supplies the rules.
"""
from django.contrib import admin
from django.urls import reverse
from django.utils.text import capfirst

# (app label, sidebar title, icon). Order = order in the sidebar. Apps that are not installed are skipped.
MODULES = [
    ("masters", "Master Data", "database"),
    ("procurement", "Procurement", "cart"),
    ("stock", "Inventory & Warehouse", "archive"),
    ("quality", "Quality Control", "beaker"),
    ("production", "Production", "cog"),
    ("distribution", "Sales & Distribution", "truck"),
    ("pos_erp", "POS Control", "register"),
    ("accounting", "Accounting", "calculator"),
    ("hr", "HR", "id"),
    ("payroll", "Payroll", "cash"),
    ("approvals", "Approvals", "check"),
    ("rbac", "Roles & Security", "shield"),
    ("audit", "Audit Trail", "doc"),
    ("notifications", "Notifications", "bell"),
    ("company", "Company & Branches", "building"),
    ("tenants", "Stores", "building"),
    ("numbering", "Document Numbers", "doc"),
    ("attachments", "Attachments", "doc"),
]
MODULE_APPS = {app for app, _, _ in MODULES}
MODULE_TITLES = {app: title for app, title, _ in MODULES}

# Heroicons (outline, 24x24) path data.
ICONS = {
    "database": "M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4",
    "cart": "M3 3h2l.4 2M7 13h10l4-8H5.4M7 13L5.4 5M7 13l-2.293 2.293c-.63.63-.184 1.707.707 1.707H17m-10 0a2 2 0 100 4 2 2 0 000-4zm10 0a2 2 0 100 4 2 2 0 000-4z",
    "archive": "M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4",
    "beaker": "M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z",
    "cog": "M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z",
    "truck": "M13 16V6a1 1 0 00-1-1H4a1 1 0 00-1 1v10a1 1 0 001 1h1m8-1a1 1 0 01-1 1H9m4-1V8a1 1 0 011-1h2.586a1 1 0 01.707.293l3.414 3.414a1 1 0 01.293.707V16a1 1 0 01-1 1h-1m-6-1a1 1 0 001 1h1M5 17a2 2 0 104 0m-4 0a2 2 0 114 0m6 0a2 2 0 104 0m-4 0a2 2 0 114 0",
    "register": "M9 7h6m0 10v-3m-3 3v-3m-3 3v-3m9-4H3V6a2 2 0 012-2h14a2 2 0 012 2v7z",
    "calculator": "M9 7h6m0 10v-3m-3 3h.01M9 17h.01M9 14h.01M12 14h.01M15 11h.01M12 11h.01M9 11h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z",
    "id": "M10 6H5a2 2 0 00-2 2v9a2 2 0 002 2h14a2 2 0 002-2V8a2 2 0 00-2-2h-5m-4 0V5a2 2 0 114 0v1m-4 0a2 2 0 104 0m-5 8a2 2 0 100-4 2 2 0 000 4zm0 0c1.306 0 2.417.835 2.83 2M9 14a3.001 3.001 0 00-2.83 2M15 11h3m-3 4h2",
    "cash": "M17 9V7a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2m2 4h10a2 2 0 002-2v-6a2 2 0 00-2-2H9a2 2 0 00-2 2v6a2 2 0 002 2zm7-5a2 2 0 11-4 0 2 2 0 014 0z",
    "check": "M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4",
    "shield": "M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z",
    "doc": "M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z",
    "bell": "M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9",
    "building": "M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4",
    "chart": "M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2",
    "home": "M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6",
}


def icon_path(key):
    return ICONS.get(key, ICONS["doc"])


def model_registry():
    """[(model, model_admin)] for every ERP model that has an admin definition, in registration order."""
    return [(m, ma) for m, ma in admin.site._registry.items() if m._meta.app_label in MODULE_APPS]


def get_model_admin(app_label, model_name):
    """(model, model_admin) or (None, None)."""
    if app_label not in MODULE_APPS:
        return None, None
    for model, ma in admin.site._registry.items():
        if model._meta.app_label == app_label and model._meta.model_name == model_name:
            return model, ma
    return None, None


def list_url(model):
    return reverse("erp_ui:list", args=[model._meta.app_label, model._meta.model_name])


def add_url(model):
    return reverse("erp_ui:add", args=[model._meta.app_label, model._meta.model_name])


def edit_url(model, pk):
    return reverse("erp_ui:edit", args=[model._meta.app_label, model._meta.model_name, pk])


def plural_label(model):
    return capfirst(str(model._meta.verbose_name_plural))


def build_menu(request):
    """Sidebar tree for the current user: only modules/models they are allowed to view."""
    match = getattr(request, "resolver_match", None)
    kwargs = match.kwargs if match else {}
    active_app = kwargs.get("app_label")
    active_model = kwargs.get("model_name")
    by_app = {}
    for model, ma in model_registry():
        try:
            if not ma.has_view_permission(request):
                continue
        except Exception:
            continue
        by_app.setdefault(model._meta.app_label, []).append({
            "label": plural_label(model),
            "url": list_url(model),
            "can_add": bool(ma.has_add_permission(request)),
            "add_url": add_url(model),
            "active": model._meta.app_label == active_app and model._meta.model_name == active_model,
        })
    modules = []
    for app, title, icon in MODULES:
        items = by_app.get(app)
        if not items:
            continue
        modules.append({"app": app, "title": title, "icon": icon_path(icon), "items": items,
                        "active": app == active_app, "url": reverse("erp_ui:module", args=[app])})
    return modules
