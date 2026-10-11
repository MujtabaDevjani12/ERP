from django.contrib import admin

from .models import DocumentLog, EmailTemplate, InvoiceTemplate


@admin.register(InvoiceTemplate)
class InvoiceTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "store", "is_default")


@admin.register(EmailTemplate)
class EmailTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "store", "is_default")


@admin.register(DocumentLog)
class DocumentLogAdmin(admin.ModelAdmin):
    list_display = ("sale", "channel", "recipient", "status", "created_at")
    list_filter = ("channel", "status")
