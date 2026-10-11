from django.contrib import admin

from .models import Attachment


@admin.register(Attachment)
class AttachmentAdmin(admin.ModelAdmin):
    list_display = ("original_name", "category", "content_type", "object_id", "size_bytes", "uploaded_by", "created_at")
    list_filter = ("category", "content_type")
    search_fields = ("original_name", "description")
    readonly_fields = ("content_type", "object_id", "original_name", "size_bytes", "uploaded_by")
