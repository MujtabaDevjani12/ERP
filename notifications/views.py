from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .models import Notification
from . import services


@login_required
def inbox(request):
    items = Notification.objects.filter(user=request.user)[:100]
    return render(request, "notifications/inbox.html", {"items": items, "unread": services.unread_count(request.user)})


@login_required
@require_POST
def read(request, pk=None):
    services.mark_read(request.user, pk)
    if request.headers.get("x-requested-with") == "XMLHttpRequest":      # the bell menu marks items read without leaving the page
        return JsonResponse({"ok": True, "unread": services.unread_count(request.user)})
    return redirect("notifications:inbox")
