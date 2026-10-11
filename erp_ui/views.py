"""
Generic ERP screens: list (search / filters / sort / CSV / bulk workflow actions), add & edit forms with inline line-item
tables, record workflow actions, delete with protection, and a type-to-search lookup endpoint.

Everything is driven by each app's ModelAdmin, so permissions (add / change / delete / view per model), forms, inline
tables, immutability rules and workflow actions are defined once in the app's admin.py and behave identically here.
"""
import csv
import html as html_lib

from django.apps import apps
from django.contrib import admin, messages
from django.contrib.admin.utils import NotRelationField, flatten_fieldsets, get_deleted_objects, get_fields_from_path
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import FieldDoesNotExist, PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, models, transaction
from django.forms.formsets import all_valid
from django.http import Http404, HttpResponse, JsonResponse
from django.http.response import HttpResponseBase
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.html import strip_tags
from django.utils.text import capfirst
from django.views.decorators.http import require_POST

from core.errors import ERPError
from . import registry
from .display import Cell, cell, column_label, sort_field, tone_for, pill
from .widgets import normalize_form, normalize_formset

PAGE_SIZES = (25, 50, 100)
MAX_EXPORT_ROWS = 50000


# --------------------------------------------------------------------------- helpers
def _resolve(app_label, model_name):
    model, ma = registry.get_model_admin(app_label, model_name)
    if ma is None:
        raise Http404("This screen does not exist.")
    return model, ma


def _base_context(model, ma):
    app = model._meta.app_label
    return {
        "model": model,
        "app_label": app,
        "model_name": model._meta.model_name,
        "module_title": registry.MODULE_TITLES.get(app, capfirst(app)),
        "module_url": reverse("erp_ui:module", args=[app]),
        "title_plural": registry.plural_label(model),
        "title_singular": capfirst(str(model._meta.verbose_name)),
        "list_url": registry.list_url(model),
        "add_url": registry.add_url(model),
    }


def _actions_for(request, ma):
    actions = ma.get_actions(request) or {}
    return [(name, str(desc)) for name, (func, _n, desc) in actions.items() if name != "delete_selected"]


def _run_action(request, ma, name, queryset):
    """Run one admin action on `queryset`. Returns an HttpResponse when the action produced a file/page, else None."""
    actions = ma.get_actions(request) or {}
    if name not in actions or name == "delete_selected":
        messages.error(request, "That action is not available.")
        return None
    func = actions[name][0]
    try:
        response = func(ma, request, queryset)
    except PermissionDenied:
        raise
    except (ERPError, ValidationError) as exc:
        messages.error(request, "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
        return None
    if isinstance(response, HttpResponseBase):
        return response
    return None


def _plain(value):
    return html_lib.unescape(strip_tags(str(value))).replace("—", "").strip()


# --------------------------------------------------------------------------- list
def _build_filters(request, model, ma, qs):
    filters = []
    for entry in ma.get_list_filter(request) or []:
        if not isinstance(entry, str):
            continue                                    # custom FilterSpec classes are not supported here
        try:
            field = get_fields_from_path(model, entry)[-1]
        except (FieldDoesNotExist, NotRelationField):
            continue
        options = None
        if getattr(field, "choices", None):
            options = [(str(k), str(v)) for k, v in field.flatchoices]
        elif isinstance(field, models.BooleanField):
            options = [("1", "Yes"), ("0", "No")]
        elif getattr(field, "is_relation", False) and (field.many_to_one or field.one_to_one) and field.related_model is not None:
            options = [(str(o.pk), str(o)) for o in field.related_model._default_manager.all()[:150]]
        elif isinstance(field, models.IntegerField):
            values = model._default_manager.order_by().values_list(entry, flat=True).distinct()[:200]
            options = [(str(v), str(v)) for v in sorted((v for v in values if v is not None), reverse=True)[:40]]
        if not options:
            continue
        param = "f_" + entry
        selected = request.GET.get(param, "")
        if selected in {key for key, _ in options}:
            qs = qs.filter(**{entry: (selected == "1") if isinstance(field, models.BooleanField) else selected})
        else:
            selected = ""
        filters.append({"param": param, "label": capfirst(str(field.verbose_name)), "options": options, "selected": selected})
    return qs, filters


def _apply_dates(request, model, ma, qs):
    """date_hierarchy -> a simple from/to range."""
    name = getattr(ma, "date_hierarchy", None)
    if not name:
        return qs, None
    try:
        field = model._meta.get_field(name)
    except FieldDoesNotExist:
        return qs, None
    suffix = "__date" if isinstance(field, models.DateTimeField) else ""
    d_from, d_to = parse_date(request.GET.get("df", "") or ""), parse_date(request.GET.get("dt", "") or "")
    if d_from:
        qs = qs.filter(**{f"{name}{suffix}__gte": d_from})
    if d_to:
        qs = qs.filter(**{f"{name}{suffix}__lte": d_to})
    return qs, {"label": capfirst(str(field.verbose_name)), "df": d_from.isoformat() if d_from else "", "dt": d_to.isoformat() if d_to else ""}


@login_required
def model_list(request, app_label, model_name):
    model, ma = _resolve(app_label, model_name)
    if not ma.has_view_permission(request):
        raise PermissionDenied

    if request.method == "POST" and request.POST.get("action"):
        pks = request.POST.getlist("_selected")
        if not pks:
            messages.warning(request, "Tick at least one record first.")
        else:
            response = _run_action(request, ma, request.POST["action"], ma.get_queryset(request).filter(pk__in=pks))
            if response is not None:
                return response
        return redirect(request.get_full_path())

    qs = ma.get_queryset(request)
    select_related = getattr(ma, "list_select_related", False)
    if select_related is True:
        qs = qs.select_related()
    elif select_related:
        qs = qs.select_related(*select_related)

    q = request.GET.get("q", "").strip()
    if q and ma.search_fields:
        qs, may_duplicate = ma.get_search_results(request, qs, q)
        if may_duplicate:
            qs = qs.distinct()
    qs, filters = _build_filters(request, model, ma, qs)
    qs, date_range = _apply_dates(request, model, ma, qs)

    names = list(ma.get_list_display(request))
    columns = []
    for name in names:
        columns.append({"name": name, "label": column_label(name, model, ma), "sort": sort_field(name, model, ma)})
    order = request.GET.get("o", "")
    order_name, desc = order.lstrip("-"), order.startswith("-")
    sorted_col = next((c for c in columns if c["name"] == order_name and c["sort"]), None)
    if sorted_col:
        qs = qs.order_by(("-" if desc else "") + sorted_col["sort"], "-pk")
    elif not qs.ordered:
        qs = qs.order_by("-pk")
    for c in columns:
        if c["sort"]:
            current = sorted_col is c
            c["next"] = ("-" + c["name"]) if (current and not desc) else c["name"]
            c["state"] = ("desc" if desc else "asc") if current else ""

    if request.GET.get("export") == "csv":
        return _export_csv(qs, names, columns, model, ma)

    try:
        per = int(request.GET.get("per", PAGE_SIZES[0]))
    except ValueError:
        per = PAGE_SIZES[0]
    per = per if per in PAGE_SIZES else PAGE_SIZES[0]
    paginator = Paginator(qs, per)
    page = paginator.get_page(request.GET.get("page"))
    rows = [{"pk": obj.pk, "url": registry.edit_url(model, obj.pk), "cells": [cell(obj, n, ma) for n in names]} for obj in page.object_list]

    params = request.GET.copy()
    params.pop("page", None)
    params.pop("export", None)
    base_query = params.urlencode()
    params_no_sort = request.GET.copy()
    for key in ("page", "o", "export"):
        params_no_sort.pop(key, None)

    ctx = _base_context(model, ma)
    ctx.update({
        "page_range": list(paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1)), "ellipsis": Paginator.ELLIPSIS,
        "order": order if sorted_col else "",
        "columns": columns, "rows": rows, "page": page, "paginator": paginator, "per": per, "page_sizes": PAGE_SIZES,
        "filters": filters, "date_range": date_range, "q": q, "has_search": bool(ma.search_fields),
        "actions": _actions_for(request, ma), "can_add": ma.has_add_permission(request),
        "query": base_query, "sort_query": params_no_sort.urlencode(), "has_filters": bool(q or any(f["selected"] for f in filters) or (date_range and (date_range["df"] or date_range["dt"]))),
        "search_hint": _search_hint(ma.search_fields),
    })
    return render(request, "erp_ui/list.html", ctx)


def _search_hint(fields):
    names = []
    for f in list(fields)[:5]:
        names.append(" ".join(f.lstrip("^=@").split("__")).replace("_", " "))
    return ", ".join(names)


def _export_csv(qs, names, columns, model, ma):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{model._meta.model_name}.csv"'
    response.write("﻿")                           # Excel needs the BOM to read UTF-8
    writer = csv.writer(response)
    writer.writerow([c["label"] for c in columns])
    for obj in qs[:MAX_EXPORT_ROWS]:
        writer.writerow([_plain(cell(obj, n, ma).html) for n in names])
    return response



# --------------------------------------------------------------------------- detail-page extras
_BAD_STATES = {"cancelled", "canceled", "rejected", "void", "voided", "reversed", "declined"}


def _timeline(request, model, obj):
    """Activity feed for a record, from the immutable audit trail (empty when audit is absent or not permitted)."""
    if obj is None:
        return []
    try:
        from audit.models import AuditLog
    except ImportError:
        return []
    if not request.user.has_perm("audit.view_auditlog"):
        return []
    rows = AuditLog.objects.filter(content_type=ContentType.objects.get_for_model(model), object_id=str(obj.pk)).select_related("user")[:25]
    events = []
    for r in rows:
        text = r.get_action_display()
        if r.action == "updated" and isinstance(r.old_values, dict) and isinstance(r.new_values, dict):
            changed = [str(k).replace("_", " ") for k, v in r.new_values.items() if r.old_values.get(k) != v][:6]
            if changed:
                text += ": " + ", ".join(changed)
        if r.note:
            text += " \u2014 " + r.note
        events.append({"text": text, "actor": r.user_repr or "System", "time": timezone.localtime(r.created_at).strftime("%d %b %Y, %H:%M"),
                       "state": "done" if r.action in ("approved", "posted", "created") else ""})
    return events


def _steps(obj):
    """Process tracker for records with a linear `status` workflow (draft -> approved -> posted ...)."""
    if obj is None:
        return []
    try:
        field = obj._meta.get_field("status")
    except FieldDoesNotExist:
        return []
    choices = [(str(k), str(v)) for k, v in (getattr(field, "flatchoices", None) or [])]
    flow = [(k, v) for k, v in choices if k.lower() not in _BAD_STATES]
    current = str(getattr(obj, "status", ""))
    keys = [k for k, _ in flow]
    if len(flow) < 3 or current not in keys:
        return []
    at = keys.index(current)
    return [{"label": label, "state": "done" if i < at else ("current" if i == at else "todo")} for i, (_k, label) in enumerate(flow)]

# --------------------------------------------------------------------------- add / edit
def _sections(model, ma, obj, form, readonly_names, fieldsets, view_only):
    sections = []
    for title, opts in fieldsets:
        items = []
        for entry in opts.get("fields", ()):
            for name in (entry if isinstance(entry, (list, tuple)) else [entry]):
                if form is not None and name in form.fields:
                    bound = form[name]
                    items.append({"kind": "input", "bound": bound, "wide": bound.field.widget.__class__.__name__ == "Textarea",
                                  "checkbox": bound.field.widget.__class__.__name__ == "CheckboxInput"})
                elif obj is not None and (view_only or name in readonly_names):
                    items.append({"kind": "value", "label": column_label(name, model, ma), "cell": cell(obj, name, ma)})
        if items:
            sections.append({"title": title or "", "description": opts.get("description", ""), "items": items})
    return sections


def _inline_blocks(request, obj, formsets, inlines, can_change):
    blocks = []
    for formset, inline in zip(formsets, inlines):
        title = capfirst(str(inline.verbose_name_plural))
        editable = can_change and (inline.has_change_permission(request, obj) or inline.has_add_permission(request, obj))
        ro_names = [n for n in inline.get_readonly_fields(request, obj)]
        if editable:
            cols = [f for f in formset.empty_form.visible_fields() if f.name not in ("DELETE", "ORDER")]
            headers = [{"label": f.label, "required": f.field.required} for f in cols] + [{"label": column_label(n, inline.model, inline), "required": False} for n in ro_names]

            def row(form, cols=cols, ro_names=ro_names, inline=inline):
                saved = form.instance.pk is not None
                return {
                    "form": form,
                    "fields": [form[c.name] for c in cols],
                    "readonly": [cell(form.instance, n, inline) if saved else Cell("") for n in ro_names],
                    "delete": form["DELETE"] if "DELETE" in form.fields else None,
                    "hidden": form.hidden_fields(),
                    "errors": form.non_field_errors(),
                }
            blocks.append({
                "title": title, "editable": True, "formset": formset, "headers": headers,
                "rows": [row(f) for f in formset.forms], "empty_row": row(formset.empty_form),
                "can_add": bool(inline.has_add_permission(request, obj)), "can_delete": bool(formset.can_delete),
                "prefix": formset.prefix,
            })
        else:
            if obj is None:
                continue
            fk_name = getattr(getattr(formset, "fk", None), "name", None)
            names = [n for n in flatten_fieldsets(inline.get_fieldsets(request, obj)) if n != fk_name]
            blocks.append({
                "title": title, "editable": False,
                "headers": [{"label": column_label(n, inline.model, inline), "required": False} for n in names],
                "rows": [{"cells": [cell(o, n, inline) for n in names]} for o in formset.get_queryset()],
            })
    return blocks


def _log(ma, request, obj, message, add):
    (ma.log_addition if add else ma.log_change)(request, obj, message)


@login_required
def model_edit(request, app_label, model_name, pk=None):
    model, ma = _resolve(app_label, model_name)
    add = pk is None
    obj = None
    if add:
        if not ma.has_add_permission(request):
            raise PermissionDenied
    else:
        obj = ma.get_object(request, str(pk))
        if obj is None:
            raise Http404("Record not found.")
        if not ma.has_view_permission(request, obj):
            raise PermissionDenied
    can_change = add or ma.has_change_permission(request, obj)
    drawer = (request.GET.get("_drawer") == "1" or request.POST.get("_drawer") == "1") and can_change   # quick-create panel: no line items

    form, formsets, inlines, saved_obj = None, [], [], None
    fieldsets = ma.get_fieldsets(request, obj)
    readonly_names = set(ma.get_readonly_fields(request, obj))

    if can_change:
        form_class = ma.get_form(request, obj, change=not add, fields=flatten_fieldsets(fieldsets))
        if request.method == "POST":
            form = normalize_form(form_class(request.POST, request.FILES, instance=obj))
            formsets, inlines = ([], []) if drawer else ma._create_formsets(request, form.instance, change=not add)
            for fs in formsets:
                normalize_formset(fs)
            try:
                with transaction.atomic():
                    form_ok = form.is_valid()
                    new_object = ma.save_form(request, form, change=not add) if form_ok else form.instance
                    formsets_ok = all_valid(formsets)
                    if form_ok and formsets_ok:
                        ma.save_model(request, new_object, form, not add)
                        ma.save_related(request, form, formsets, not add)
                        _log(ma, request, new_object, ma.construct_change_message(request, form, formsets, add), add)
                        saved_obj = new_object
            except (ERPError, ValidationError) as exc:
                saved_obj = None
                form.add_error(None, "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
            except IntegrityError:
                saved_obj = None
                form.add_error(None, "Could not save: the data conflicts with an existing record or a database rule.")
            if saved_obj is not None and drawer:
                return JsonResponse({"ok": True, "id": saved_obj.pk, "text": str(saved_obj), "reload": False,
                                     "message": f'{capfirst(str(model._meta.verbose_name))} "{saved_obj}" created.'})
            if saved_obj is not None:
                messages.success(request, f'{capfirst(str(model._meta.verbose_name))} "{saved_obj}" saved.')
                if "_addanother" in request.POST:
                    return redirect(registry.add_url(model))
                if "_tolist" in request.POST:
                    return redirect(registry.list_url(model))
                return redirect(registry.edit_url(model, saved_obj.pk))
            if not drawer:
                messages.error(request, "Nothing was saved. Please fix the highlighted problems below.")
        else:
            if add:
                form = normalize_form(form_class(initial=ma.get_changeform_initial_data(request)))
            else:
                form = normalize_form(form_class(instance=obj))
            formsets, inlines = ([], []) if drawer else ma._create_formsets(request, form.instance, change=not add)
            for fs in formsets:
                normalize_formset(fs)
    else:
        formsets, inlines = ma._create_formsets(request, obj, change=True)

    ctx = _base_context(model, ma)
    status_field = getattr(obj, "status", None) if obj is not None else None
    ctx.update({
        "obj": obj, "add": add, "form": form, "can_change": can_change,
        "sections": _sections(model, ma, obj, form, readonly_names, fieldsets, view_only=not can_change),
        "inline_blocks": _inline_blocks(request, obj, formsets, inlines, can_change),
        "can_delete": (not add) and ma.has_delete_permission(request, obj),
        "actions": [] if add else _actions_for(request, ma),
        "status_pill": pill(obj.get_status_display() if hasattr(obj, "get_status_display") else status_field, tone_for(status_field)) if status_field else "",
        "edit_url": "" if add else registry.edit_url(model, obj.pk),
        "title": f"New {str(model._meta.verbose_name)}" if add else str(obj),
        "steps": [] if add or drawer else _steps(obj),
        "timeline": [] if add or drawer else _timeline(request, model, obj),
        "drawer": drawer,
    })
    return render(request, "erp_ui/_drawer_form.html" if drawer else "erp_ui/form.html", ctx)


@login_required
def model_preview(request, app_label, model_name, pk):
    """Read-only summary of a record, loaded into the side drawer from list pages."""
    model, ma = _resolve(app_label, model_name)
    obj = ma.get_object(request, str(pk))
    if obj is None:
        raise Http404("Record not found.")
    if not ma.has_view_permission(request, obj):
        raise PermissionDenied
    sections = _sections(model, ma, obj, None, set(), ma.get_fieldsets(request, obj), view_only=True)
    status_field = getattr(obj, "status", None)
    ctx = _base_context(model, ma)
    ctx.update({"obj": obj, "sections": sections, "edit_url": registry.edit_url(model, obj.pk), "title": str(obj),
                "status_pill": pill(obj.get_status_display() if hasattr(obj, "get_status_display") else status_field, tone_for(status_field)) if status_field else ""})
    return render(request, "erp_ui/_preview.html", ctx)


@login_required
@require_POST
def object_action(request, app_label, model_name, pk):
    model, ma = _resolve(app_label, model_name)
    obj = ma.get_object(request, str(pk))
    if obj is None:
        raise Http404("Record not found.")
    if not ma.has_view_permission(request, obj):
        raise PermissionDenied
    name = request.POST.get("action", "")
    if not name:
        messages.warning(request, "Choose an action first.")
    else:
        response = _run_action(request, ma, name, ma.get_queryset(request).filter(pk=obj.pk))
        if response is not None:
            return response
    return redirect(registry.edit_url(model, obj.pk))


# --------------------------------------------------------------------------- delete
@login_required
def model_delete(request, app_label, model_name, pk):
    model, ma = _resolve(app_label, model_name)
    obj = ma.get_object(request, str(pk))
    if obj is None:
        raise Http404("Record not found.")
    if not ma.has_delete_permission(request, obj):
        raise PermissionDenied
    to_delete, _counts, perms_needed, protected = get_deleted_objects([obj], request, admin.site)
    blocked = bool(perms_needed or protected)
    if request.method == "POST" and not blocked:
        label = str(obj)
        try:
            with transaction.atomic():
                if hasattr(ma, "log_deletions"):
                    ma.log_deletions(request, ma.get_queryset(request).filter(pk=obj.pk))
                else:
                    ma.log_deletion(request, obj, label)
                ma.delete_model(request, obj)
        except (ERPError, ValidationError, IntegrityError) as exc:
            messages.error(request, "; ".join(exc.messages) if isinstance(exc, ValidationError) else (str(exc) or "This record is in use and cannot be deleted."))
            return redirect(registry.edit_url(model, pk))
        messages.success(request, f'{capfirst(str(model._meta.verbose_name))} "{label}" deleted.')
        return redirect(registry.list_url(model))
    ctx = _base_context(model, ma)
    ctx.update({"obj": obj, "to_delete": to_delete, "protected": protected, "perms_needed": perms_needed, "blocked": blocked,
                "edit_url": registry.edit_url(model, obj.pk)})
    return render(request, "erp_ui/confirm_delete.html", ctx)


# --------------------------------------------------------------------------- module landing page
@login_required
def module_home(request, app_label):
    if app_label not in registry.MODULE_APPS:
        raise Http404
    cards = []
    for model, ma in registry.model_registry():
        if model._meta.app_label != app_label or not ma.has_view_permission(request):
            continue
        try:
            count = ma.get_queryset(request).count()
        except Exception:
            count = None
        cards.append({"label": registry.plural_label(model), "url": registry.list_url(model), "count": count,
                      "can_add": ma.has_add_permission(request), "add_url": registry.add_url(model)})
    if not cards:
        raise PermissionDenied
    return render(request, "erp_ui/module.html", {"module_title": registry.MODULE_TITLES[app_label], "cards": cards, "app_label": app_label})


# --------------------------------------------------------------------------- lookup (type-to-search picker)
@login_required
def lookup(request, app_label, model_name):
    try:
        target = apps.get_model(app_label, model_name)
        for_model = apps.get_model(request.GET.get("for", ""))
    except (LookupError, ValueError):
        raise Http404
    user = request.user
    label = for_model._meta
    if not (user.has_perm(f"{label.app_label}.add_{label.model_name}") or user.has_perm(f"{label.app_label}.change_{label.model_name}")):
        raise PermissionDenied
    if not any(f.is_relation and f.related_model is target for f in label.get_fields()):
        raise PermissionDenied
    ma = admin.site._registry.get(target)
    qs = ma.get_queryset(request) if ma is not None else target._default_manager.all()
    q = request.GET.get("q", "").strip()
    if not qs.ordered:
        qs = qs.order_by("pk")
    if q and ma is not None and ma.search_fields:
        qs, _dup = ma.get_search_results(request, qs, q)
        found = list(qs.distinct()[:20])
    elif q:
        needle = q.lower()
        found = [o for o in qs[:500] if needle in str(o).lower()][:20]
    else:
        found = list(qs[:20])
    return JsonResponse({"results": [{"id": o.pk, "text": str(o)} for o in found]})
