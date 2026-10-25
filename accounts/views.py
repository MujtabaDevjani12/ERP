"""
Roles & Users — an in-app replacement for pointing staff at Django's own
/admin/auth/group/ and /admin/accounts/user/ screens. A "Role" here is
still just a django.contrib.auth Group under the hood (see accounts.forms
.RoleForm's docstring) — this just gives it a page that matches the rest
of the app's look instead of the bare Django admin UI.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.db.models import Count, ProtectedError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy

from .forms import ProfileForm, RoleForm, StaffUserForm
from .models import User

# Permissions from these apps are Django/Python internals a POS role never
# needs to grant directly (migrations bookkeeping, cache tables, ...) — kept
# out of the checklist so it stays focused on things that matter here.
HIDDEN_PERMISSION_APPS = ["admin", "sessions", "contenttypes"]


ACTION_COLUMNS = ("view", "add", "change", "delete")


def _permission_matrix(selected_ids, editable_ids=None):
    """Permissions as a matrix: one card per module, one row per screen (model),
    columns View / Add / Change / Delete + any extra actions (approve, post, print...).

    selected_ids : ids the role currently has.
    editable_ids : ids the signed-in editor may toggle (None = all). Others show disabled."""
    from django.apps import apps as django_apps
    from django.utils.text import capfirst

    rows = (
        Permission.objects.exclude(content_type__app_label__in=HIDDEN_PERMISSION_APPS)
        .select_related("content_type").order_by("content_type__app_label", "content_type__model", "codename")
    )
    modules = {}
    for perm in rows:
        ct = perm.content_type
        perm.checked = perm.id in selected_ids
        perm.locked = editable_ids is not None and perm.id not in editable_ids
        module = modules.get(ct.app_label)
        if module is None:
            try:
                title = str(django_apps.get_app_config(ct.app_label).verbose_name)
            except LookupError:
                title = ct.app_label
            if ct.app_label == "rbac":
                title = "Workflow actions (approve, post, print...)"
            module = modules[ct.app_label] = {"label": ct.app_label, "title": capfirst(title), "models": {}, "total": 0, "selected": 0}
        model_cls = ct.model_class()
        model_name = capfirst(str(model_cls._meta.verbose_name_plural)) if model_cls else ct.model
        if ct.app_label == "rbac":
            model_name = "Document actions"
        row = module["models"].setdefault(ct.model, {"name": model_name, "cells": {c: None for c in ACTION_COLUMNS}, "extras": []})
        action = perm.codename.split("_", 1)[0]
        if action in ACTION_COLUMNS and perm.codename == f"{action}_{ct.model}":
            row["cells"][action] = perm
        else:
            row["extras"].append(perm)
        module["total"] += 1
        module["selected"] += 1 if perm.checked else 0
    ordered = sorted(modules.values(), key=lambda m: (m["label"] != "rbac", m["title"].lower()))
    for module in ordered:
        module["rows"] = [dict(r, cell_list=[r["cells"][c] for c in ACTION_COLUMNS]) for r in sorted(module["models"].values(), key=lambda r: r["name"].lower())]
    return ordered


def _can(request, perm):
    return request.user.has_perm(perm)


def _shared_roles_locked(request):
    """Roles (groups) are shared by every company on the platform, so when several companies exist only the platform owner may
    create, change or delete them. Company admins still assign the existing roles to their own staff."""
    from billing.tenancy import tenant_company
    return tenant_company(request) is not None


def _limit_users(request):
    """(True, message) when the company has used all the users its plan allows."""
    from billing.access import company_of, is_platform_owner, limit_reached
    if is_platform_owner(request.user):
        return False, ""
    company = company_of(request)
    return limit_reached(company, "users") if company is not None else (False, "")


# ---------------------------------------------------------------- Roles
@login_required
@permission_required("auth.view_group", raise_exception=True)
def role_list(request):
    from rbac.backends import SUPER_ADMIN_ROLE
    roles = (Group.objects.annotate(member_count=Count("user", distinct=True), perm_count=Count("permissions", distinct=True))
             .order_by("name"))
    q = (request.GET.get("q") or "").strip()
    if q:
        roles = roles.filter(name__icontains=q)
    return render(request, "accounts/role_list.html", {
        "roles": roles, "q": q, "super_role": SUPER_ADMIN_ROLE,
        "total_permissions": Permission.objects.exclude(content_type__app_label__in=HIDDEN_PERMISSION_APPS).count(),
        "can_add": _can(request, "auth.add_group") and not _shared_roles_locked(request),
        "can_change": _can(request, "auth.change_group") and not _shared_roles_locked(request),
        "can_delete": _can(request, "auth.delete_group") and not _shared_roles_locked(request),
    })


@login_required
def role_form(request, pk=None):
    """Add (needs auth.add_group) or edit (needs auth.change_group) a role. The Super Admin role is
    read-only - it always has every permission. Someone who isn't a Super Admin can only grant
    permissions they hold themselves, so a role editor can't use this screen to promote themselves."""
    from rbac.backends import SUPER_ADMIN_ROLE, is_super_admin

    needed = "auth.change_group" if pk else "auth.add_group"
    if not request.user.has_perm(needed) or _shared_roles_locked(request):
        raise PermissionDenied
    role = get_object_or_404(Group, pk=pk) if pk else None
    is_super_role = bool(role and role.name == SUPER_ADMIN_ROLE)
    editor_super = is_super_admin(request.user)
    held_ids = None
    if not editor_super:
        held = request.user.get_all_permissions()
        held_ids = {p.id for p in Permission.objects.select_related("content_type") if f"{p.content_type.app_label}.{p.codename}" in held}

    copy_from = None
    if not role and request.GET.get("copy", "").isdigit():
        copy_from = Group.objects.filter(pk=request.GET["copy"]).first()

    if request.method == "POST":
        if is_super_role:
            messages.info(request, "The Super Admin role always has every permission and can't be edited.")
            return redirect("accounts:role_list")
        form = RoleForm(request.POST, instance=role)
        if form.is_valid():
            role = form.save()
            chosen = set(int(i) for i in request.POST.getlist("permissions") if i.isdigit())
            if held_ids is not None:
                existing = set(role.permissions.values_list("id", flat=True)) if pk else set()
                chosen = (chosen & held_ids) | (existing - held_ids)       # can't add what you don't hold; can't strip what you can't see
            role.permissions.set(Permission.objects.filter(pk__in=chosen))
            messages.success(request, f'Role "{role.name}" saved with {len(chosen)} permission{"s" if len(chosen) != 1 else ""}.')
            return redirect("accounts:role_list")
        selected = set(int(i) for i in request.POST.getlist("permissions") if i.isdigit())
    else:
        form = RoleForm(instance=role, initial={"name": f"{copy_from.name} (copy)"} if copy_from else None)
        source = role or copy_from
        selected = set(source.permissions.values_list("id", flat=True)) if source else set()

    matrix = _permission_matrix(selected, editable_ids=held_ids)
    return render(request, "accounts/role_form.html", {
        "form": form, "role": role, "matrix": matrix, "is_super_role": is_super_role, "copy_from": copy_from,
        "selected_count": sum(m["selected"] for m in matrix), "total_count": sum(m["total"] for m in matrix),
        "member_count": role.user_set.count() if role else 0, "limited": held_ids is not None,
    })


@login_required
@permission_required("auth.delete_group", raise_exception=True)
def role_delete(request, pk):
    from rbac.backends import SUPER_ADMIN_ROLE
    if _shared_roles_locked(request):
        raise PermissionDenied
    role = get_object_or_404(Group, pk=pk)
    if role.name == SUPER_ADMIN_ROLE:
        messages.error(request, "The Super Admin role is built in and can't be deleted.")
        return redirect("accounts:role_list")
    members = role.user_set.count()
    if request.method == "POST":
        name = role.name
        role.delete()
        messages.success(request, f'Role "{name}" deleted.' + (f" {members} user(s) lost it." if members else ""))
        return redirect("accounts:role_list")
    return render(request, "partials/confirm_delete.html", {
        "object": role, "success_url": reverse_lazy("accounts:role_list"),
        "warning": f"{members} user(s) currently have this role and will lose its access." if members else "",
    })


# ---------------------------------------------------------------- Staff users
def _guard_super_target(request, target):
    """Only a Super Admin may view-edit or remove a Super Admin account."""
    from rbac.backends import is_super_admin
    if target is not None and is_super_admin(target) and not is_super_admin(request.user):
        raise PermissionDenied("Only a Super Admin can manage a Super Admin account.")


@login_required
@permission_required("accounts.view_user", raise_exception=True)
def staff_user_list(request):
    from django.core.paginator import Paginator
    from django.db.models import Q
    from rbac.backends import SUPER_ADMIN_ROLE
    from billing.tenancy import scope_queryset
    qs = scope_queryset(request, User.objects.select_related("store").prefetch_related("groups").order_by("username"))
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q))
    if request.GET.get("status") in ("active", "inactive"):
        qs = qs.filter(is_active=request.GET["status"] == "active")
    if request.GET.get("biller") in ("1", "0"):          # billers = the people who can be picked as the seller at the POS
        qs = qs.filter(is_biller=request.GET["biller"] == "1")
    role = (request.GET.get("role") or "").strip()
    if role.isdigit():
        qs = qs.filter(groups__pk=int(role))
    try:
        per = int(request.GET.get("per", 25))
    except ValueError:
        per = 25
    per = per if per in (25, 50, 100) else 25
    page_obj = Paginator(qs.distinct(), per).get_page(request.GET.get("page"))
    return render(request, "accounts/user_list.html", {
        "staff_users": page_obj.object_list, "page_obj": page_obj, "paginator": page_obj.paginator, "per": per,
        "page_sizes": (25, 50, 100), "roles": Group.objects.order_by("name"), "role_filter": role, "super_role": SUPER_ADMIN_ROLE,
        "can_add": _can(request, "accounts.add_user") and not _limit_users(request)[0], "users_limit_msg": _limit_users(request)[1],
        "can_change": _can(request, "accounts.change_user"), "can_delete": _can(request, "accounts.delete_user"),
        "has_filters": bool(q or request.GET.get("status") or request.GET.get("biller") or role), "result_count": page_obj.paginator.count})


@login_required
def staff_user_form(request, pk=None):
    """Add needs accounts.add_user; edit needs accounts.change_user."""
    from billing.tenancy import scope_queryset
    if not request.user.has_perm("accounts.change_user" if pk else "accounts.add_user"):
        raise PermissionDenied
    staff_user = get_object_or_404(scope_queryset(request, User.objects.all()), pk=pk) if pk else None
    _guard_super_target(request, staff_user)
    if not pk:
        reached, why = _limit_users(request)
        if reached:
            messages.error(request, why)
            return redirect("accounts:staff_user_list")

    if request.method == "POST":
        form = StaffUserForm(request.POST, instance=staff_user, editor=request.user)
        if form.is_valid():
            if not pk and form.cleaned_data.get("is_active", True) is not False and _limit_users(request)[0]:
                messages.error(request, _limit_users(request)[1])
                return redirect("accounts:staff_user_list")
            saved_user = form.save()
            messages.success(request, f'User "{saved_user.username}" saved.')
            return redirect("accounts:biller_list" if request.GET.get("biller") == "1" else "accounts:staff_user_list")
    else:
        form = StaffUserForm(instance=staff_user, editor=request.user,
                             initial={"is_biller": True} if request.GET.get("biller") == "1" and not staff_user else None)

    return render(request, "accounts/user_form.html", {"form": form, "staff_user": staff_user})


@login_required
@permission_required("accounts.delete_user", raise_exception=True)
def staff_user_delete(request, pk):
    from rbac.backends import SUPER_ADMIN_ROLE, is_super_admin
    from billing.tenancy import scope_queryset
    staff_user = get_object_or_404(scope_queryset(request, User.objects.all()), pk=pk)
    _guard_super_target(request, staff_user)
    if request.method == "POST":
        if staff_user == request.user:
            messages.error(request, "You can't delete your own account while logged in as it.")
            return redirect("accounts:staff_user_list")
        if is_super_admin(staff_user):
            others = User.objects.filter(is_active=True).exclude(pk=staff_user.pk)
            if not (others.filter(is_superuser=True) | others.filter(groups__name=SUPER_ADMIN_ROLE)).exists():
                messages.error(request, "This is the last active Super Admin - it can't be deleted.")
                return redirect("accounts:staff_user_list")
        username = staff_user.username
        try:
            staff_user.delete()
        except ProtectedError:
            messages.error(request, f'"{username}" has records linked to them (sales, journals...). Deactivate the user instead of deleting.')
            return redirect("accounts:staff_user_list")
        messages.success(request, f'User "{username}" deleted.')
        return redirect("accounts:staff_user_list")
    return render(request, "partials/confirm_delete.html", {
        "object": staff_user, "success_url": reverse_lazy("accounts:staff_user_list"),
        "warning": "Tip: deactivate the user instead if they have sales or other history." ,
    })


# ---------------------------------------------------------------------------------------------------------- my profile
@login_required
def profile(request):
    """The signed-in person's own page: their details, photo and password (no permission needed - it is only about themselves)."""
    from django.contrib.auth import update_session_auth_hash
    from django.contrib.auth.forms import PasswordChangeForm
    from register.views import get_active_session

    user = request.user
    details, password = ProfileForm(instance=user), PasswordChangeForm(user)
    if request.method == "POST":
        which = request.POST.get("form")
        if which == "details":
            details = ProfileForm(request.POST, request.FILES, instance=user)
            if details.is_valid():
                details.save()
                messages.success(request, "Your details were saved.")
                return redirect("accounts:profile")
        elif which == "password":
            password = PasswordChangeForm(user, request.POST)
            if password.is_valid():
                password.save()
                update_session_auth_hash(request, password.user)             # stay signed in after the change
                messages.success(request, "Your password was changed.")
                return redirect("accounts:profile")
    for f in password.fields.values():
        f.widget.attrs.setdefault("placeholder", "")
    session = get_active_session(request)
    return render(request, "accounts/profile.html", {
        "details": details, "password_form": password, "register_session": session,
        "roles": ", ".join(user.groups.order_by("name").values_list("name", flat=True)),
        "open_tab": request.POST.get("form") or "details",
    })


# ---------------------------------------------------------------------------------------------------------- billers
@login_required
@permission_required("accounts.view_user", raise_exception=True)
def biller_list(request):
    """The people who can be picked as the seller on a sale, with what each one sold over the last 30 days."""
    from datetime import timedelta
    from django.db.models import Max, Q, Sum
    from django.utils import timezone
    from configuration.models import POSConfiguration

    since = timezone.now() - timedelta(days=30)
    recent = Q(sales_billed__created_at__gte=since, sales_billed__status="completed")
    qs = (User.objects.filter(is_biller=True).select_related("store")
          .annotate(n=Count("sales_billed", filter=recent), value=Sum("sales_billed__total_amount", filter=recent), last_sale=Max("sales_billed__created_at"))
          .order_by("-is_active", "first_name", "username"))
    store = getattr(request, "store", None)
    if store is not None:
        qs = qs.filter(Q(store=store) | Q(store__isnull=True))
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q) | Q(phone__icontains=q))
    if request.GET.get("status") in ("active", "inactive"):
        qs = qs.filter(is_active=request.GET["status"] == "active")
    conf = POSConfiguration.objects.filter(store=store).first() if store is not None else None
    rows = list(qs)
    return render(request, "accounts/biller_list.html", {
        "billers": rows, "default_biller_id": conf.default_biller_id if conf else None, "has_filters": bool(q or request.GET.get("status")),
        "total": len(rows), "active": sum(1 for r in rows if r.is_active), "sold": sum((r.value or 0) for r in rows), "count": sum(r.n for r in rows),
        "can_default": request.user.has_perm("configuration.change_posconfiguration") and conf is not None,
    })


@login_required
@permission_required("configuration.change_posconfiguration", raise_exception=True)
def biller_default(request, pk):
    """Make one biller the one pre-selected at the POS for this branch."""
    from configuration.models import POSConfiguration
    if request.method != "POST":
        return redirect("accounts:biller_list")
    biller = get_object_or_404(User, pk=pk, is_biller=True, is_active=True)
    conf = POSConfiguration.objects.filter(store=request.store).first()
    if conf is None:
        messages.error(request, "This branch has no POS settings yet.")
    else:
        conf.default_biller = biller
        conf.save(update_fields=["default_biller", "updated_at"])
        messages.success(request, f"{biller} is now the default biller at the POS.")
    return redirect("accounts:biller_list")
