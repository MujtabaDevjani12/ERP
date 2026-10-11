from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "level", "is_read", "emailed", "created_at")
    list_filter = ("level", "is_read")
    search_fields = ("title", "user__username")
