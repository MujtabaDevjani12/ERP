"""Turn model values into the small bits of HTML the ERP list/detail screens show (status pills, Yes/No, numbers)."""
from dataclasses import dataclass
from decimal import Decimal

from django.contrib.admin.utils import display_for_field, display_for_value, label_for_field, lookup_field
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe

EMPTY = mark_safe('<span style="color:var(--ui-text-3)">&mdash;</span>')

_GOOD = {"approved", "posted", "completed", "paid", "active", "received", "delivered", "accepted", "passed", "confirmed",
         "closed", "released", "invoiced", "awarded", "resolved", "present", "done", "fully_received", "fully_paid",
         "converted", "in_stock", "success", "info_ok"}
_WARN = {"draft", "pending", "pending_approval", "submitted", "partial", "partially_received", "partially_paid", "on_hold",
         "hold", "in_progress", "sent", "calculated", "planned", "scheduled", "processing", "warning", "waiting", "open",
         "new", "late", "half_day"}
_BAD = {"rejected", "cancelled", "canceled", "failed", "reversed", "void", "overdue", "absent", "blocked", "expired",
        "critical", "error", "lost", "defective"}

_BADGE = {"good": "success", "warn": "warning", "bad": "danger", "neutral": "neutral"}


def tone_for(raw):
    key = str(raw).strip().lower().replace(" ", "_").replace("-", "_")
    if key in _GOOD:
        return "good"
    if key in _WARN:
        return "warn"
    if key in _BAD:
        return "bad"
    return "neutral"


def pill(label, tone="neutral"):
    return format_html('<span class="ui-badge ui-badge-{}">{}</span>', _BADGE.get(tone, "neutral"), label)


def yes_no(value):
    if value is None:
        return EMPTY
    return pill("Yes", "good") if value else pill("No", "neutral")


@dataclass
class Cell:
    html: object
    numeric: bool = False


def _is_status_field(name, field):
    return bool(getattr(field, "choices", None)) and (name == "status" or name.endswith("_status") or name in ("level", "priority", "severity"))


def cell(obj, name, model_admin):
    """The display value of `name` (field, property, method or admin column) for `obj`."""
    try:
        field, attr, value = lookup_field(name, obj, model_admin)
    except (AttributeError, ValueError, ObjectDoesNotExist):
        return Cell(EMPTY)
    if field is None:
        if getattr(attr, "boolean", False):
            return Cell(yes_no(value))
        if value is None or value == "":
            return Cell(EMPTY)
        if isinstance(value, bool):
            return Cell(yes_no(value))
        if isinstance(value, SafeString) or hasattr(value, "__html__"):
            return Cell(value)
        if name == "status" or name.endswith("_status"):
            return Cell(pill(str(value), tone_for(value)))
        return Cell(display_for_value(value, EMPTY), numeric=isinstance(value, (int, float, Decimal)))
    if isinstance(field, (models.BooleanField,)) or field.get_internal_type() == "NullBooleanField":
        return Cell(yes_no(value))
    if value is None or value == "":
        return Cell(EMPTY)
    if _is_status_field(name, field):
        label = display_for_field(value, field, EMPTY)
        return Cell(pill(label, tone_for(value)))
    if isinstance(field.remote_field, models.ManyToManyRel):
        try:
            return Cell(", ".join(str(v) for v in value.all()) or EMPTY)
        except Exception:
            return Cell(EMPTY)
    if field.is_relation:
        return Cell(str(value))
    numeric = isinstance(field, (models.DecimalField, models.IntegerField, models.FloatField)) and not field.choices
    return Cell(display_for_field(value, field, EMPTY), numeric=numeric)


def column_label(name, model, model_admin):
    try:
        return label_for_field(name, model, model_admin)
    except Exception:
        return name.replace("_", " ").capitalize()


def sort_field(name, model, model_admin):
    """Database field a column can be sorted by, or '' when it cannot."""
    if name == "__str__":
        return ""
    try:
        model._meta.get_field(name)
        return name
    except Exception:
        pass
    attr = getattr(model_admin, name, None) or getattr(model, name, None)
    return getattr(attr, "admin_order_field", "") or ""
