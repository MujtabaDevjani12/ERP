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
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy

from .forms import ProfileForm, RoleForm, StaffUserForm
from .models import User

# Permissions from these apps are Django/Python internals a POS role never
# needs to grant directly (migrations bookkeeping, cache tables, ...) — kept
# out of the checklist so it stays focused on things that matter here.
HIDDEN_PERMISSION_APPS = ["admin", "sessions", "contenttypes"]


def _grouped_permissions():
    permissions = (
        Permission.objects.exclude(content_type__app_label__in=HIDDEN_PERMISSION_APPS)
        .select_related("content_type")
        .order_by("content_type__app_label", "name")
    )
    grouped = {}
    for perm in permissions:
        grouped.setdefault(perm.content_type.app_label, []).append(perm)
    return grouped


# ---------------------------------------------------------------- Roles
@login_required
@permission_required("auth.view_group", raise_exception=True)
def role_list(request):
    roles = Group.objects.annotate(member_count=Count("user", distinct=True)).order_by("name")
    return render(request, "accounts/role_list.html", {"roles": roles})


@login_required
@permission_required("auth.change_group", raise_exception=True)
def role_form(request, pk=None):
    role = get_object_or_404(Group, pk=pk) if pk else None

    if request.method == "POST":
        form = RoleForm(request.POST, instance=role)
        if form.is_valid():
            role = form.save()
            selected_ids = request.POST.getlist("permissions")
            role.permissions.set(Permission.objects.filter(pk__in=selected_ids))
            messages.success(request, f'Role "{role.name}" saved.')
            return redirect("accounts:role_list")
    else:
        form = RoleForm(instance=role)

    selected_permission_ids = set(role.permissions.values_list("id", flat=True)) if role else set()
    return render(request, "accounts/role_form.html", {
        "form": form, "role": role,
        "grouped_permissions": _grouped_permissions(),
        "selected_permission_ids": selected_permission_ids,
    })


@login_required
@permission_required("auth.delete_group", raise_exception=True)
def role_delete(request, pk):
    role = get_object_or_404(Group, pk=pk)
    if request.method == "POST":
        name = role.name
        role.delete()
        messages.success(request, f'Role "{name}" deleted.')
        return redirect("accounts:role_list")
    return render(request, "partials/confirm_delete.html", {
        "object": role, "success_url": reverse_lazy("accounts:role_list"),
    })


# ---------------------------------------------------------------- Staff users
@login_required
@permission_required("accounts.view_user", raise_exception=True)
def staff_user_list(request):
    from django.core.paginator import Paginator
    from django.db.models import Q
    qs = User.objects.select_related("store").prefetch_related("groups").order_by("username")
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q) | Q(email__icontains=q))
    if request.GET.get("status") in ("active", "inactive"):
        qs = qs.filter(is_active=request.GET["status"] == "active")
    if request.GET.get("biller") in ("1", "0"):          # billers = the people who can be picked as the seller at the POS
        qs = qs.filter(is_biller=request.GET["biller"] == "1")
    try:
        per = int(request.GET.get("per", 25))
    except ValueError:
        per = 25
    per = per if per in (25, 50, 100) else 25
    page_obj = Paginator(qs, per).get_page(request.GET.get("page"))
    return render(request, "accounts/user_list.html", {
        "staff_users": page_obj.object_list, "page_obj": page_obj, "paginator": page_obj.paginator, "per": per,
        "page_sizes": (25, 50, 100), "has_filters": bool(q or request.GET.get("status") or request.GET.get("biller")), "result_count": page_obj.paginator.count})


@login_required
@permission_required("accounts.add_user", raise_exception=True)
def staff_user_form(request, pk=None):
    staff_user = get_object_or_404(User, pk=pk) if pk else None

    if request.method == "POST":
        form = StaffUserForm(request.POST, instance=staff_user)
        if form.is_valid():
            saved_user = form.save()
            messages.success(request, f'User "{saved_user.username}" saved.')
            return redirect("accounts:biller_list" if request.GET.get("biller") == "1" else "accounts:staff_user_list")
    else:
        form = StaffUserForm(instance=staff_user, initial={"is_biller": True} if request.GET.get("biller") == "1" and not staff_user else None)

    return render(request, "accounts/user_form.html", {"form": form, "staff_user": staff_user})


@login_required
@permission_required("accounts.delete_user", raise_exception=True)
def staff_user_delete(request, pk):
    staff_user = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        if staff_user == request.user:
            messages.error(request, "You can't delete your own account while logged in as it.")
            return redirect("accounts:staff_user_list")
        username = staff_user.username
        staff_user.delete()
        messages.success(request, f'User "{username}" deleted.')
        return redirect("accounts:staff_user_list")
    return render(request, "partials/confirm_delete.html", {
        "object": staff_user, "success_url": reverse_lazy("accounts:staff_user_list"),
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
