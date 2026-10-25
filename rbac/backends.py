"""
Super Admin = full access to everything, always.

A user is a Super Admin when they are `is_superuser` OR belong to the
"Super Admin" role (group). The group is kept in sync with the flag by
rbac.signals, but this backend checks both so that access never depends on
the group's permission snapshot being up to date (e.g. right after a new
app/model adds permissions and before `migrate` has run its sync).
"""
from django.contrib.auth.backends import ModelBackend

SUPER_ADMIN_ROLE = "Super Admin"


def is_super_admin(user) -> bool:
    """True for an active user that is a superuser or holds the Super Admin role."""
    if user is None or not getattr(user, "is_authenticated", False) or not user.is_active:
        return False
    cached = getattr(user, "_erp_super_admin", None)
    if cached is None:
        cached = bool(user.is_superuser) or user.groups.filter(name=SUPER_ADMIN_ROLE).exists()
        user._erp_super_admin = cached
    return cached


class SuperAdminBackend(ModelBackend):
    """Django's normal permission backend, except Super Admins pass every check."""

    def has_perm(self, user_obj, perm, obj=None):
        return is_super_admin(user_obj) or super().has_perm(user_obj, perm, obj)

    def has_module_perms(self, user_obj, app_label):
        return is_super_admin(user_obj) or super().has_module_perms(user_obj, app_label)
