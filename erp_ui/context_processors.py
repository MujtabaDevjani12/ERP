"""On ERP pages, swap the POS sidebar for the ERP sidebar (base.html includes `nav_template`)."""

NAV_APPS = {"erp_ui", "reports_erp", "notifications"}


def erp_nav(request):
    user = getattr(request, "user", None)
    match = getattr(request, "resolver_match", None)
    if user is None or not user.is_authenticated or match is None:
        return {}
    if match.app_name not in NAV_APPS and not (set(match.namespaces) & NAV_APPS):
        return {}
    from .registry import build_menu
    return {"nav_template": "erp_ui/nav.html", "erp_menu": build_menu(request)}
