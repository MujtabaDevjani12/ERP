"""Shared behaviour for the ERP list screens: server-side pagination ("Per page" selector), a stable page size,
and a `has_filters` flag so the toolbar can show a Reset button. Pair with templates/components/*.html."""
from django.utils.functional import cached_property


class PagedListMixin:
    paginate_by = 25
    page_sizes = (25, 50, 100)
    filter_params = ("q",)          # GET parameters that count as "a filter is applied"

    def get_paginate_by(self, queryset):
        try:
            n = int(self.request.GET.get("per", self.paginate_by))
        except (TypeError, ValueError):
            n = self.paginate_by
        return n if n in self.page_sizes else self.paginate_by

    @cached_property
    def active_filters(self):
        return any((self.request.GET.get(k) or "").strip() for k in self.filter_params)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update({
            "per": self.get_paginate_by(None), "page_sizes": self.page_sizes, "has_filters": self.active_filters,
            "result_count": ctx["paginator"].count if ctx.get("paginator") else len(ctx.get("object_list", [])),
        })
        return ctx
