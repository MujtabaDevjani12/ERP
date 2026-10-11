from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from core.mixins import DeleteContextMixin, StoreCrudMixin
from .forms import CurrencyForm
from .models import Currency


# The old stand-alone POS / System / Numbering pages now live in the Settings hub (see hub.py). The URL names are kept so every
# existing link keeps working; they simply land on the matching hub section.
@login_required
def pos_settings_view(request):
    return redirect("configuration:hub_section", section="pos-behaviour")


@login_required
def system_settings_view(request):
    return redirect("configuration:hub_section", section="business")


# ---------------------------------------------------------------- extra currencies (shown on barcode labels)
class CurrencyListView(StoreCrudMixin, ListView):
    model = Currency
    template_name = "configuration/currency_list.html"
    context_object_name = "rows"
    permission_required = "configuration.view_currency"

    def get_queryset(self):
        return super().get_queryset().order_by("code")


class _CurrencyFormMixin:
    form_class = CurrencyForm
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("configuration:currency_list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["store"] = self.request.store
        return kwargs


class CurrencyCreateView(_CurrencyFormMixin, StoreCrudMixin, CreateView):
    model = Currency
    permission_required = "configuration.add_currency"

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Add currency", back_url=self.success_url, **kwargs)


class CurrencyUpdateView(_CurrencyFormMixin, StoreCrudMixin, UpdateView):
    model = Currency
    permission_required = "configuration.change_currency"

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Edit currency", back_url=self.success_url, **kwargs)


class CurrencyDeleteView(StoreCrudMixin, DeleteContextMixin, DeleteView):
    model = Currency
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("configuration:currency_list")
    permission_required = "configuration.delete_currency"
