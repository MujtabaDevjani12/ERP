from django.core.exceptions import PermissionDenied


def require(user, codename: str) -> None:
    """Raise PermissionDenied unless `user` holds rbac.<codename>.
    `user=None` means a system action (management command / scheduled job)
    and is allowed. Superusers always pass."""
    if user is None:
        return
    if not user.has_perm(f"rbac.{codename}"):
        raise PermissionDenied(f"You do not have permission: {codename.replace('_', ' ')}.")
