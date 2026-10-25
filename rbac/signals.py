"""Keep the "Super Admin" role and the `is_superuser` / `is_staff` flags in step."""
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db.models.signals import m2m_changed, post_migrate

from .backends import SUPER_ADMIN_ROLE


def sync_super_admin_role(**kwargs):
    """Super Admin role holds every permission that exists; superusers join the role.
    Runs once at the end of `migrate` (after every app has created its permissions)."""
    sender = kwargs.get("sender")
    configs = [c for c in django_apps.get_app_configs() if c.models_module is not None]
    if not configs or sender is not configs[-1]:
        return
    try:
        group, _ = Group.objects.get_or_create(name=SUPER_ADMIN_ROLE)
        group.permissions.set(Permission.objects.all())
        User = get_user_model()
        group.user_set.add(*User.objects.filter(is_superuser=True))
    except Exception:  # pragma: no cover - database not ready (first migrate of auth tables etc.)
        pass


def _sync_flags(sender, instance, action, reverse, model, pk_set, **kwargs):
    """Adding someone to Super Admin makes them is_superuser + is_staff; removing clears is_superuser."""
    if action not in ("post_add", "post_remove", "post_clear"):
        return
    User = get_user_model()
    super_group = Group.objects.filter(name=SUPER_ADMIN_ROLE).first()
    if super_group is None:
        return
    if not reverse:                                    # user.groups.add(...)
        if action == "post_clear" or (pk_set and super_group.pk in pk_set):
            user = instance
            should = user.groups.filter(pk=super_group.pk).exists()
            _apply(User, [user.pk], should)
    else:                                              # group.user_set.add(...)
        if instance.pk != super_group.pk:
            return
        if action == "post_clear":
            _apply(User, list(User.objects.filter(is_superuser=True).exclude(groups=super_group).values_list("pk", flat=True)), False)
        elif pk_set:
            _apply(User, list(pk_set), action == "post_add")


def _apply(User, pks, make_super):
    if not pks:
        return
    if make_super:
        User.objects.filter(pk__in=pks).update(is_superuser=True, is_staff=True)
    else:
        User.objects.filter(pk__in=pks).update(is_superuser=False)


def connect():
    post_migrate.connect(sync_super_admin_role, dispatch_uid="rbac_sync_super_admin")
    m2m_changed.connect(_sync_flags, sender=get_user_model().groups.through, dispatch_uid="rbac_super_admin_flags")
