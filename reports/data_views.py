"""Import & export hub: Excel (.xlsx) downloads, import templates and uploads for the lists a shop keeps (see exchange.py)."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import exchange
from . import xlsx as xl

DATED = {"sales", "expenses", "purchases"}              # exports that can be limited to a date range


def _resource(key):
    res = exchange.get(key)
    if res is None:
        raise Http404("No such list")
    return res


def _cards(user):
    cards = []
    for res in exchange.REGISTRY.values():
        can_exp, can_imp = res.can_export(user), res.can_import(user)
        if can_exp or can_imp:
            cards.append({"key": res.key, "label": res.label, "plural": res.plural, "icon": res.icon, "text": res.description, "export": can_exp, "import": can_imp,
                          "export_only": res.export_only, "dated": res.key in DATED, "columns": [c for c in res.cols if not res.export_only]})
    return cards


@login_required
def data_hub(request, result=None, result_key=None, error=None):
    cards = _cards(request.user)
    if not cards:
        raise PermissionDenied
    return render(request, "reports/data_hub.html", {"cards": cards, "result": result, "result_key": result_key,
                                                      "result_card": next((c for c in cards if c["key"] == result_key), None), "error": error})


@login_required
def data_export(request, key):
    res = _resource(key)
    if not res.can_export(request.user):
        raise PermissionDenied
    try:
        name = f"{key}-{timezone.localdate()}"
        return xl.xlsx_response(name, res.export_sheets(request.user, request.store, request.GET))
    except xl.ExcelError as exc:
        messages.error(request, str(exc))
        return data_hub(request)


@login_required
def data_template(request, key):
    res = _resource(key)
    if not res.can_import(request.user):
        raise PermissionDenied
    try:
        return xl.xlsx_response(f"{key}-import-template", res.template_sheets())
    except xl.ExcelError as exc:
        messages.error(request, str(exc))
        return data_hub(request)


@login_required
@require_POST
def data_import(request, key):
    res = _resource(key)
    if not res.can_import(request.user):
        raise PermissionDenied
    dry = request.POST.get("mode") == "check"
    try:
        result = exchange.run_import(res, request.user, request.store, request.FILES.get("file"), dry_run=dry)
    except xl.ExcelError as exc:
        return data_hub(request, error=str(exc), result_key=key)
    if not dry and (result["created"] or result["updated"]):
        messages.success(request, f"{res.plural} import finished: {result['created']} new, {result['updated']} updated"
                                  + (f", {result['failed']} with problems" if result["failed"] else "") + ".")
    return data_hub(request, result=result, result_key=key)
