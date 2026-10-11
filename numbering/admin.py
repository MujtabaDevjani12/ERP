from django.contrib import admin

from .models import DocumentSequence


@admin.register(DocumentSequence)
class DocumentSequenceAdmin(admin.ModelAdmin):
    list_display = ("document_type", "company", "store", "fiscal_year", "prefix", "padding", "next_number")
    list_filter = ("company", "document_type", "fiscal_year")
    search_fields = ("prefix",)
