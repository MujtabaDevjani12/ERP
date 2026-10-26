"""Data backup and restore - one engine for the ERP (one company's data) and the owner console (the owner database).

A backup is a ZIP file:
    manifest.json            what it is (kind, company, date, format, row counts, model order)
    data/<app.model>.json    the rows of one table, written by Django's own serializer (so every field type round-trips)
    media/<path>             uploaded files (logos, product pictures, payment proofs) that the rows point to

Company backup  = everything that belongs to ONE company (its branches, products, sales, stock, accounts, HR ...). It is found by
                  following foreign keys from the Company row, so a new module is included without touching this file. Users, roles,
                  the subscription and the audit log are NOT part of it: a restore can never change who can sign in or what plan
                  the company pays for, and a backup never carries another company's data.
Owner backup    = every table of the owner console (plans, subscriptions, invoices, payments, owner accounts, settings, e-mail log).

Restore REPLACES: inside one database transaction the current rows of that scope are deleted and the backup's rows are loaded with
their original ids. If anything fails the transaction is rolled back and nothing changes. The view makes a safety backup first.
"""
import io
import json
import os
import re
import time
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.color import no_style
from django.db import IntegrityError, connections, transaction
from django.db.models import FileField, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

FORMAT = 1
CHUNK = 400
# never part of a company backup: people & permissions, the platform's own tables, logs and framework tables
EXCLUDED_APPS = {"contenttypes", "sessions", "admin", "auth", "accounts", "ownerconsole", "billing", "audit"}
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class BackupError(Exception):
    """Something the person can fix (shown as a normal message)."""


# ------------------------------------------------------------------ finding what belongs to a scope
def _chunks(values, size=CHUNK):
    values = list(values)
    for i in range(0, len(values), size):
        yield values[i:i + size]


def _pks_where(model, using, **lookups):
    """Primary keys of `model` rows matching {field: iterable of values} (any one of them), chunked for SQLite's variable limit."""
    out = set()
    names = list(lookups)
    chunk_lists = [list(_chunks(lookups[n])) or [[]] for n in names]
    for i, name in enumerate(names):
        for chunk in chunk_lists[i]:
            if chunk:
                out.update(model._base_manager.using(using).filter(**{f"{name}__in": chunk}).values_list("pk", flat=True))
    return out


def _links(model, resolved):
    """Forward foreign keys of `model` that point at an already-owned table."""
    out = []
    for f in model._meta.concrete_fields:
        if f.is_relation and f.related_model in resolved and f.related_model._meta.app_label not in EXCLUDED_APPS:
            out.append(f)
    return out


def company_scope(company, using="default"):
    """{model: set of primary keys} for everything that belongs to `company`."""
    Company = type(company)
    resolved = {Company: {company.pk}}
    candidates = [m for m in apps.get_models() if m._meta.app_label not in EXCLUDED_APPS and not m._meta.proxy and m is not Company]
    for _ in range(12):                                     # follow foreign keys outwards until nothing new is found
        changed = False
        for model in candidates:
            links = _links(model, resolved)
            if not links:
                continue
            pks = _pks_where(model, using, **{f.name: resolved[f.related_model] for f in links})
            if model not in resolved or len(pks) != len(resolved[model]):
                resolved[model] = pks
                changed = True
        if not changed:
            break
    return {m: pks for m, pks in resolved.items() if pks}


def owner_scope(using):
    """Every row of the owner console's tables."""
    out = {}
    for model in apps.get_app_config("ownerconsole").get_models():
        out[model] = set(model._base_manager.using(using).values_list("pk", flat=True))
    return out


def ordered(models):
    """Parents before children (so a restore inserts in a sensible order and a delete runs in the reverse one)."""
    models = list(models)
    members = set(models)
    deps = {m: {f.related_model for f in m._meta.concrete_fields if f.is_relation and f.related_model in members and f.related_model is not m} for m in models}
    out, done = [], set()
    while len(out) < len(models):
        ready = [m for m in models if m not in done and deps[m] <= done]
        if not ready:                                        # a cycle: let the (deferred) database constraints sort it out
            ready = [m for m in models if m not in done][:1]
        for m in sorted(ready, key=lambda m: m._meta.label):
            out.append(m)
            done.add(m)
    return out


# ------------------------------------------------------------------ writing a backup
def _file_names(model, rows):
    fields = [f for f in model._meta.concrete_fields if isinstance(f, FileField)]
    for obj in rows:
        for f in fields:
            value = getattr(obj, f.attname, None)
            if value:
                yield str(value)


def build_backup(scope, *, using, kind, label, extra=None):
    """-> (bytes, manifest). `scope` is {model: set(pk)}."""
    started = time.perf_counter()
    manifest = {"format": FORMAT, "kind": kind, "label": label, "created": timezone.now().isoformat(), "models": [], "counts": {},
                "app": "multiple-pos"}
    manifest.update(extra or {})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        files = set()
        for model in ordered(scope):
            pks = sorted(scope[model], key=lambda v: (str(type(v)), v))
            rows = []
            for chunk in _chunks(pks):
                rows.extend(model._base_manager.using(using).filter(pk__in=chunk).order_by("pk"))
            if not rows:
                continue
            label_ = model._meta.label_lower
            zf.writestr(f"data/{label_}.json", serializers.serialize("json", rows))
            manifest["models"].append(label_)
            manifest["counts"][label_] = len(rows)
            files.update(_file_names(model, rows))
        kept = []
        for name in sorted(files):
            try:
                if default_storage.exists(name):
                    with default_storage.open(name, "rb") as fh:
                        zf.writestr(f"media/{name}", fh.read())
                    kept.append(name)
            except Exception:                                # noqa: BLE001 - a missing / unreadable picture must not stop the backup
                continue
        manifest["media"] = kept
        manifest["seconds"] = round(time.perf_counter() - started, 2)
        zf.writestr("manifest.json", json.dumps(manifest, indent=1))
    return buf.getvalue(), manifest


def company_backup(company, using="default"):
    data, manifest = build_backup(company_scope(company, using), using=using, kind="company", label=company.name,
                                  extra={"company_id": company.pk, "company_name": company.name})
    return data, manifest


def owner_backup(using):
    return build_backup(owner_scope(using), using=using, kind="owner", label="Owner console")


def filename_for(manifest):
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    base = SAFE_NAME.sub("-", manifest.get("label") or manifest["kind"]).strip("-").lower() or manifest["kind"]
    ident = f"c{manifest['company_id']}-" if manifest.get("company_id") else ""
    return f"backup-{manifest['kind']}-{ident}{base}-{stamp}.zip"


# ------------------------------------------------------------------ copies kept on the server (the history shown on the Backup pages)
KEEP = 30                     # newest copies kept per company / per owner console; older ones are removed automatically


def backup_dir():
    path = Path(getattr(settings, "BACKUP_DIR", settings.BASE_DIR / "backups"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def _scope_marker(kind, company_id=None):
    return f"-company-c{company_id}-" if kind == "company" and company_id else f"-{kind}-"


def save_copy(data, manifest, why=""):
    """Write a backup into the server's backups folder (why="" = a normal backup, "before-import" = the safety copy before a restore)."""
    try:
        path = backup_dir() / f"{why + '-' if why else ''}{filename_for(manifest)}"
        path.write_bytes(data)
    except OSError as exc:
        raise BackupError(f"A copy could not be saved on the server ({exc.strerror or exc}). "
                          "Check that the 'backups' folder next to manage.py is writable.")
    _prune(manifest["kind"], manifest.get("company_id"))
    return path


def save_safety_copy(data, manifest, why="before-import"):
    try:
        return save_copy(data, manifest, why=why)
    except BackupError as exc:
        raise BackupError(f"{exc} Nothing was changed.")


def _prune(kind, company_id=None):
    try:
        marker = _scope_marker(kind, company_id)
        files = sorted((p for p in backup_dir().glob("*.zip") if marker in p.name), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[KEEP:]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


def manifest_of(path):
    try:
        with zipfile.ZipFile(path) as zf:
            return json.loads(zf.read("manifest.json"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None


def saved_copies(kind, key="", limit=20, company_id=None):
    """Newest copies on the server for this scope: [{name, size, when, records, seconds, safety}]. `when` is the moment the backup was made."""
    out = []
    marker = _scope_marker(kind, company_id)
    try:
        for p in sorted(backup_dir().glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
            if marker not in p.name or (key and key not in p.name):
                continue
            manifest = manifest_of(p) or {}
            if manifest.get("kind") != kind or (company_id and manifest.get("company_id") != company_id):
                continue
            st = p.stat()
            when = parse_datetime(manifest.get("created", "")) or datetime.fromtimestamp(st.st_mtime, tz=timezone.get_current_timezone())
            out.append({"name": p.name, "size": st.st_size, "when": timezone.localtime(when) if timezone.is_aware(when) else when,
                        "records": sum((manifest.get("counts") or {}).values()), "seconds": manifest.get("seconds"),
                        "safety": p.name.startswith("before-import")})
            if len(out) >= limit:
                break
    except OSError:                                          # an unreadable / read-only folder must never break the Settings page
        return []
    return out


def saved_copy_path(name, kind=None, company_id=None):
    """The file of a saved copy, only if it really belongs to this scope (the file's own manifest decides, not its name)."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.zip", name or ""):
        raise BackupError("That file name is not valid.")
    path = backup_dir() / name
    if not path.is_file():
        raise BackupError("That saved backup no longer exists.")
    if kind:
        manifest = manifest_of(path) or {}
        if manifest.get("kind") != kind or (kind == "company" and manifest.get("company_id") != company_id):
            raise BackupError("That backup does not belong here.")
    return path


# ------------------------------------------------------------------ reading a backup
def read_backup(fileobj, *, kind):
    """-> (manifest, {model: [DeserializedObject]}, media {name: bytes}). Raises BackupError for anything that is not a backup of this kind."""
    try:
        zf = zipfile.ZipFile(fileobj)
        manifest = json.loads(zf.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, ValueError):
        raise BackupError("This is not a backup file made by this system (it must be the .zip you downloaded from the Backup page).")
    if manifest.get("app") != "multiple-pos" or manifest.get("format") != FORMAT:
        raise BackupError("This backup was made by a different version and cannot be read.")
    if manifest.get("kind") != kind:
        raise BackupError(f"This is a '{manifest.get('kind')}' backup; this page restores '{kind}' backups only.")
    data = {}
    for label in manifest.get("models", []):
        try:
            model = apps.get_model(label)
        except LookupError:
            continue                                         # that module no longer exists - its rows are skipped
        try:
            data[model] = list(serializers.deserialize("json", zf.read(f"data/{label}.json").decode("utf-8"), ignorenonexistent=True))
        except Exception as exc:                             # noqa: BLE001
            raise BackupError(f"The backup is damaged ({label}: {exc}).")
    media = {}
    for name in manifest.get("media", []):
        try:
            media[name] = zf.read(f"media/{name}")
        except KeyError:
            continue
    return manifest, data, media


# ------------------------------------------------------------------ restoring
def _delete_rows(model, pks, using):
    """Remove rows without cascades or signals (the whole scope is removed, and the backup puts it back)."""
    for f in model._meta.local_many_to_many:
        through = f.remote_field.through
        if through._meta.auto_created:
            src = f.m2m_field_name()
            for chunk in _chunks(pks):
                through._base_manager.using(using).filter(**{f"{src}__in": chunk})._raw_delete(using)
    for chunk in _chunks(pks):
        model._base_manager.using(using).filter(pk__in=chunk)._raw_delete(using)


def _fix_outside_references(data, scope_models, using):
    """A row may point at something outside the backup (a user, a role ...). If that row is gone now, clear the link when it is optional."""
    cache = {}

    def exists(model, pk):
        key = (model, pk)
        if key not in cache:
            cache[key] = model._base_manager.using(using).filter(pk=pk).exists()
        return cache[key]

    problems = []
    for model, objs in data.items():
        for item in objs:
            for f in model._meta.concrete_fields:
                if not f.is_relation or f.related_model in scope_models or f.related_model is None:
                    continue
                pk = getattr(item.object, f.attname, None)
                if pk is None or exists(f.related_model, pk):
                    continue
                if f.null:
                    setattr(item.object, f.attname, None)
                else:
                    problems.append(f"{model._meta.verbose_name} #{item.object.pk} needs {f.related_model._meta.verbose_name} #{pk}, which no longer exists")
    if problems:
        raise BackupError("The backup cannot be restored because: " + "; ".join(problems[:5]) + ("..." if len(problems) > 5 else ""))


def restore(scope_now, manifest, data, media, *, using, keep=()):
    """Replace the rows in `scope_now` ({model: pks}) with `data`. `keep` models are updated in place, never deleted (the Company row)."""
    present = set(data)
    _fix_outside_references(data, present | set(scope_now), using)
    counts = {}
    try:
        with transaction.atomic(using=using):
            for model in reversed(ordered(scope_now)):
                if model in keep:
                    continue
                _delete_rows(model, scope_now[model], using)
            for model in ordered(present):
                for item in data[model]:
                    item.save(using=using)                   # raw save: keeps the original id, no signals that would add extra rows
                counts[model._meta.label_lower] = len(data[model])
            conn = connections[using]
            conn.check_constraints()
            sql = conn.ops.sequence_reset_sql(no_style(), list(present))
            if sql:
                with conn.cursor() as cur:
                    for statement in sql:
                        cur.execute(statement)
    except IntegrityError as exc:
        raise BackupError(f"The backup does not fit the current data, so nothing was changed. ({exc})")
    for name, blob in media.items():
        if ".." in Path(name).parts or os.path.isabs(name):
            continue
        if default_storage.exists(name):
            default_storage.delete(name)
        default_storage.save(name, ContentFile(blob))
    return counts


def restore_company(company, fileobj, using="default"):
    manifest, data, media = read_backup(fileobj, kind="company")
    if manifest.get("company_id") != company.pk:
        raise BackupError(f"This backup belongs to '{manifest.get('company_name', '?')}' (id {manifest.get('company_id')}), not to '{company.name}'. "
                          f"A backup can only be restored into the company it was made from.")
    return restore(company_scope(company, using), manifest, data, media, using=using, keep=(type(company),))


def restore_owner(fileobj, using):
    manifest, data, media = read_backup(fileobj, kind="owner")
    return restore(owner_scope(using), manifest, data, media, using=using)
