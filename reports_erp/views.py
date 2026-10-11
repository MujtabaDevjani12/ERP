from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import redirect, render
from django.contrib import messages
from django.utils import timezone

from core.errors import ERPError
from . import excel, kpis, registry


def _company(request):
    store = getattr(request, "store", None)
    company = getattr(store, "company", None)
    if company is None:
        from company.models import Company
        company = Company.objects.order_by("id").first()
    return company


def _date(value, default):
    try:
        return date.fromisoformat(value) if value else default
    except ValueError:
        return default


@login_required
def dashboard(request):
    company = _company(request)
    if company is None:
        messages.error(request, "No company is set up yet. Run: python manage.py bootstrap_company")
        return redirect("/")
    store = getattr(request, "store", None)
    blocks = kpis.dashboard(company, request.user, store=store)
    trend = kpis.sales_trend(company, store=store) if request.user.has_perm("sales.view_sale") or request.user.is_superuser else []
    peak = max((t["total"] for t in trend), default=0) or 1
    for t in trend:
        t["pct"] = int(t["total"] * 100 / peak)
    reports = registry.visible_reports(request.user)
    return render(request, "reports_erp/dashboard.html", {"blocks": blocks, "trend": trend, "reports": reports, "company": company})


@login_required
def report(request, key):
    if key not in registry.REPORTS:
        raise Http404
    rep = registry.REPORTS[key]
    if not (request.user.is_superuser or request.user.has_perm(rep.perm)):
        return HttpResponse("You do not have access to this report.", status=403)
    company = _company(request)
    today = timezone.localdate()
    params = registry.Params(
        date_from=_date(request.GET.get("from"), today.replace(day=1)), date_to=_date(request.GET.get("to"), today),
        as_of=_date(request.GET.get("as_of"), today), store=getattr(request, "store", None) if request.GET.get("this_branch") else None)
    try:
        _, headers, rows = registry.run(key, company, params)
        error = ""
    except ERPError as exc:
        headers, rows, error = [], [], str(exc)
    subtitle = {"range": f"{params.date_from} to {params.date_to}", "as_of": f"As of {params.as_of}", "none": f"Printed {today}"}[rep.uses]
    fmt = request.GET.get("format")
    if fmt == "xlsx" and not error:
        resp = HttpResponse(excel.to_xlsx(rep.title, headers, rows, subtitle=subtitle),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        resp["Content-Disposition"] = f'attachment; filename="{key}-{today}.xlsx"'
        return resp
    if fmt == "pdf" and not error:
        pdf = excel.to_pdf(rep.title, headers, rows, subtitle)
        if pdf is None:
            messages.error(request, "PDF export needs WeasyPrint and its system libraries on this machine. Use Excel or the browser's Print instead.")
        else:
            resp = HttpResponse(pdf, content_type="application/pdf")
            resp["Content-Disposition"] = f'inline; filename="{key}-{today}.pdf"'
            return resp
    return render(request, "reports_erp/report.html", {"report": rep, "headers": headers, "rows": rows, "params": params, "error": error,
                                                         "subtitle": subtitle, "query": request.GET.urlencode()})
