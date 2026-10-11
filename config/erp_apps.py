"""
The ERP apps added on top of the original POS, in dependency order.

settings.py asks this module which of them exist on disk, so a half-copied install (or one where you deliberately
deleted a module you do not need) still starts. Nothing here touches the original POS apps.
"""
import importlib.util

# (app, url prefix or None, url module)
ERP_APPS = [
    "rbac",
    "approvals",
    "accounting",
    "masters",
    "stock",
    "quality",
    "hr",
    "procurement",
    "production",
    "distribution",
    "pos_erp",
    "payroll",
    "notifications",
    "reports_erp",
    "erp_ui",
    "api",
]

ERP_URLS = [
    ("erp/", "reports_erp.urls"),          # dashboard + report pages
    ("erp/", "erp_ui.urls"),               # ERP screens: /erp/m/<module>/<register>/ (list, add, edit, actions)
    ("notifications/", "notifications.urls"),
    ("api/v1/", "api.urls"),
]

# Hard requirements between ERP apps (an app is skipped when something it needs is missing).
REQUIRES = {
    "approvals": ["rbac"],
    "accounting": ["approvals"],
    "masters": ["accounting"],
    "stock": ["masters", "accounting"],
    "quality": ["stock"],
    "hr": ["accounting", "approvals"],
    "procurement": ["stock", "quality", "masters"],
    "production": ["stock", "quality", "hr"],
    "distribution": ["stock", "masters"],
    "pos_erp": ["stock", "accounting"],
    "payroll": ["hr", "accounting"],
    "reports_erp": ["accounting"],
}


def _exists(name):
    return importlib.util.find_spec(name) is not None


def installed_erp_apps():
    chosen = []
    for app in ERP_APPS:
        if not _exists(app):
            continue
        if any(dep not in chosen for dep in REQUIRES.get(app, [])):
            continue
        chosen.append(app)
    if _exists("rest_framework"):
        chosen.append("rest_framework")        # optional: only for the browsable API pages
    return chosen


def erp_urlpatterns():
    from django.urls import include, path
    patterns = []
    for prefix, module in ERP_URLS:
        if _exists(module.split(".")[0]) and module.split(".")[0] in installed_erp_apps():
            patterns.append(path(prefix, include(module)))
    return patterns


def context_processors():
    """Kept for compatibility: the shell's context processor is now registered directly in settings.py."""
    return []
