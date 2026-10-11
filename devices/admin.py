from django.contrib import admin

from .models import Device, MediaFile


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("name", "type", "store", "connection_info", "is_active")
    list_filter = ("type", "store", "is_active")


@admin.register(MediaFile)
class MediaFileAdmin(admin.ModelAdmin):
    list_display = ("caption", "file", "store", "uploaded_by", "created_at")
