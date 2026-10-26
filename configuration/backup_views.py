"""Settings > Data backup: download this company's data as a .zip and restore it from one.

Download needs the "change system settings" right. Restoring replaces the company's current data, so it also needs the person's
password, the word RESTORE typed in, and a safety copy of the current data is saved on the server first."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import FileResponse, HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from audit.models import AuditLog
from audit.services import log_action
from core import backup

SYS = "configuration.change_systemconfiguration"


def _company(request):
    company = getattr(request, "company", None)
    if company is None:
        raise PermissionDenied
    return company


def _allowed(request):
    if not request.user.has_perm(SYS):
        raise PermissionDenied


def _back():
    return redirect(reverse("configuration:hub_section", args=["backup"]))


@login_required
def download(request):
    _allowed(request)
    company = _company(request)
    data, manifest = backup.company_backup(company)
    try:
        name = backup.save_copy(data, manifest).name                  # kept on the server too: it appears in the history on the Backup page
    except backup.BackupError:
        name = backup.filename_for(manifest)                          # a read-only folder must not stop the download itself
    log_action(AuditLog.CREATED, company, user=request.user, company=company, note=f"Data backup downloaded ({sum(manifest['counts'].values())} rows)")
    resp = HttpResponse(data, content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="{name}"'
    return resp


@login_required
@require_POST
def import_view(request):
    _allowed(request)
    company = _company(request)
    upload = request.FILES.get("backup_file")
    if upload is None:
        messages.error(request, "Choose the backup .zip file first.")
        return _back()
    if (request.POST.get("confirm") or "").strip().upper() != "RESTORE":
        messages.error(request, "Type the word RESTORE to confirm. Nothing was changed.")
        return _back()
    if not request.user.check_password(request.POST.get("password") or ""):
        messages.error(request, "Your password is not correct. Nothing was changed.")
        return _back()
    try:
        # read the file first: a wrong file is refused before anything is touched
        backup.read_backup(upload, kind="company")
        upload.seek(0)
        current, manifest = backup.company_backup(company)
        safety = backup.save_safety_copy(current, manifest)
        counts = backup.restore_company(company, upload)
    except backup.BackupError as exc:
        messages.error(request, str(exc))
        return _back()
    log_action(AuditLog.CREATED, company, user=request.user, company=company,
               note=f"Data restored from a backup ({sum(counts.values())} rows). Safety copy: {safety.name}")
    messages.success(request, f"Backup restored: {sum(counts.values())} records in {len(counts)} tables. "
                              f"Your previous data was saved first as {safety.name} (listed below) in case you need to go back.")
    return _back()


@login_required
@require_POST
def restore_saved(request, name):
    """The Restore button next to a backup in the history list: needs the person's password."""
    _allowed(request)
    company = _company(request)
    if not request.user.check_password(request.POST.get("password") or ""):
        messages.error(request, "Your password is not correct. Nothing was changed.")
        return _back()
    try:
        path = backup.saved_copy_path(name, "company", company.pk)
        current, manifest = backup.company_backup(company)
        safety = backup.save_safety_copy(current, manifest)
        with open(path, "rb") as fh:
            counts = backup.restore_company(company, fh)
    except backup.BackupError as exc:
        messages.error(request, str(exc))
        return _back()
    log_action(AuditLog.CREATED, company, user=request.user, company=company,
               note=f"Data restored from the saved backup {name} ({sum(counts.values())} rows). Safety copy: {safety.name}")
    messages.success(request, f"Backup {name} restored: {sum(counts.values())} records in {len(counts)} tables. "
                              f"Your previous data was saved first as {safety.name}.")
    return _back()


@login_required
def saved(request, name):
    _allowed(request)
    company = _company(request)
    try:
        path = backup.saved_copy_path(name, "company", company.pk)         # only this company's own copies
    except backup.BackupError:
        messages.error(request, "That saved backup was not found.")
        return _back()
    return FileResponse(open(path, "rb"), as_attachment=True, filename=path.name)
