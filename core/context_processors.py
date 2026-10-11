"""
`shell` — everything the application shell (templates/base.html) needs, computed lazily so pages that never touch
it (the POS screen, JSON/AJAX fragments, drawers) do not pay for it.
"""
from django.urls import NoReverseMatch, reverse
from django.utils.functional import cached_property

from .ui_nav import build_commands, build_nav


class Shell:
    def __init__(self, request):
        self.request = request

    @cached_property
    def nav(self):
        return build_nav(self.request)

    @cached_property
    def commands(self):
        return build_commands(self.nav, self.request)

    @cached_property
    def search_url(self):
        try:
            return reverse("erp_ui:search")
        except NoReverseMatch:
            return ""

    @cached_property
    def inbox_url(self):
        try:
            return reverse("notifications:inbox")
        except NoReverseMatch:
            return ""

    @cached_property
    def notifications(self):
        """Latest notifications for the bell menu (empty when the notifications app is not installed)."""
        if not self.inbox_url:
            return []
        try:
            from notifications.models import Notification
            return list(Notification.objects.filter(user=self.request.user).order_by("-created_at")[:6])
        except Exception:                                           # noqa: BLE001 - never break a page for the bell
            return []

    @cached_property
    def unread(self):
        if not self.inbox_url:
            return 0
        try:
            from notifications.models import Notification
            return Notification.objects.filter(user=self.request.user, is_read=False).count()
        except Exception:                                           # noqa: BLE001
            return 0

    @cached_property
    def messages(self):
        """Django flash messages, handed to the page as JSON so base.html can show them as toasts."""
        from django.contrib.messages import get_messages
        return [{"message": str(m), "tags": m.tags} for m in get_messages(self.request)]

    @cached_property
    def read_all_url(self):
        try:
            return reverse("notifications:read_all")
        except NoReverseMatch:
            return ""


def ui_shell(request):
    return {"shell": Shell(request)}
