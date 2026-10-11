"""
    from audit.services import log_action, snapshot, diff

    before = snapshot(invoice)
    ... change invoice ...
    old, new = diff(before, snapshot(invoice))
    log_action("updated", invoice, old=old, new=new, document_number=invoice.reference)

Call log_action inside the same transaction.atomic() as the business change,
so the audit row and the change succeed or fail together.
"""
from typing import Iterable, Optional

from django.contrib.contenttypes.models import ContentType

from .context import get_context
from .models import AuditLog


def snapshot(instance, fields: Optional[Iterable[str]] = None) -> dict:
    """Plain-dict copy of an instance's concrete field values (FKs as ids)."""
    names = set(fields) if fields is not None else None
    data = {}
    for field in instance._meta.concrete_fields:
        if names is not None and field.name not in names:
            continue
        value = field.value_from_object(instance)
        if hasattr(value, "name") and not isinstance(value, str):  # FieldFile
            value = value.name
        data[field.name] = value
    return data


def diff(old: dict, new: dict):
    """Return (old_changed, new_changed): only the keys whose value changed."""
    old_changed, new_changed = {}, {}
    for key in set(old) | set(new):
        if old.get(key) != new.get(key):
            old_changed[key] = old.get(key)
            new_changed[key] = new.get(key)
    return old_changed, new_changed


def _company_of(obj):
    if obj is None:
        return None
    company = getattr(obj, "company", None)
    if company is not None and hasattr(company, "pk"):
        return company
    store = getattr(obj, "store", None)
    return getattr(store, "company", None) if store is not None else None


def log_action(action, obj=None, *, user=None, old=None, new=None, note="", document_number="", company=None):
    ctx = get_context()
    user = user if user is not None else ctx["user"]
    if user is not None and not getattr(user, "is_authenticated", False):
        user = None

    content_type = None
    object_id = ""
    object_repr = ""
    if obj is not None:
        content_type = ContentType.objects.get_for_model(obj, for_concrete_model=False)
        object_id = str(obj.pk) if obj.pk is not None else ""
        object_repr = str(obj)[:255]

    return AuditLog.objects.create(
        user=user,
        user_repr=(user.get_username() if user is not None else "")[:150],
        ip_address=ctx["ip_address"],
        user_agent=ctx["user_agent"],
        company=company or _company_of(obj),
        action=action,
        content_type=content_type,
        object_id=object_id,
        object_repr=object_repr,
        document_number=document_number[:40],
        old_values=old,
        new_values=new,
        note=note[:255],
    )
