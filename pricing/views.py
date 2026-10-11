from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from core.mixins import DeleteContextMixin, SimpleListMixin, StoreCrudMixin
from .models import CustomerGroup, Discount, Tax

FORM_CLASSES = "partials/simple_form.html"
DELETE_TEMPLATE = "partials/confirm_delete.html"


# ---------------------------------------------------------------- Tax
class TaxListView(SimpleListMixin, ListView):
    model = Tax
    page_title = "Taxes"
    add_url_name = "pricing:tax_add"
    edit_url_name = "pricing:tax_edit"
    delete_url_name = "pricing:tax_delete"


class TaxCreateView(StoreCrudMixin, CreateView):
    model = Tax
    fields = ["name", "rate", "applies_to", "is_default"]
    template_name = FORM_CLASSES
    success_url = reverse_lazy("pricing:tax_list")
    permission_required = "pricing.add_tax"

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Add tax", back_url=self.success_url, **kwargs)


class TaxUpdateView(StoreCrudMixin, UpdateView):
    model = Tax
    fields = ["name", "rate", "applies_to", "is_default"]
    template_name = FORM_CLASSES
    success_url = reverse_lazy("pricing:tax_list")
    permission_required = "pricing.change_tax"

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Edit tax", back_url=self.success_url, **kwargs)


class TaxDeleteView(StoreCrudMixin, DeleteContextMixin, DeleteView):
    model = Tax
    template_name = DELETE_TEMPLATE
    success_url = reverse_lazy("pricing:tax_list")
    permission_required = "pricing.delete_tax"


# ---------------------------------------------------------------- Discount
class DiscountListView(SimpleListMixin, ListView):
    model = Discount
    page_title = "Discounts"
    add_url_name = "pricing:discount_add"
    edit_url_name = "pricing:discount_edit"
    delete_url_name = "pricing:discount_delete"


class DiscountCreateView(StoreCrudMixin, CreateView):
    model = Discount
    fields = ["code", "type", "value", "valid_from", "valid_to", "is_active"]
    template_name = FORM_CLASSES
    success_url = reverse_lazy("pricing:discount_list")
    permission_required = "pricing.add_discount"

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Add discount", back_url=self.success_url, **kwargs)


class DiscountUpdateView(StoreCrudMixin, UpdateView):
    model = Discount
    fields = ["code", "type", "value", "valid_from", "valid_to", "is_active"]
    template_name = FORM_CLASSES
    success_url = reverse_lazy("pricing:discount_list")
    permission_required = "pricing.change_discount"

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Edit discount", back_url=self.success_url, **kwargs)


class DiscountDeleteView(StoreCrudMixin, DeleteContextMixin, DeleteView):
    model = Discount
    template_name = DELETE_TEMPLATE
    success_url = reverse_lazy("pricing:discount_list")
    permission_required = "pricing.delete_discount"


# ---------------------------------------------------------------- Customer group
class CustomerGroupListView(SimpleListMixin, ListView):
    model = CustomerGroup
    page_title = "Customer / price groups"
    add_url_name = "pricing:group_add"
    edit_url_name = "pricing:group_edit"
    delete_url_name = "pricing:group_delete"


class CustomerGroupCreateView(StoreCrudMixin, CreateView):
    model = CustomerGroup
    fields = ["name", "price_adjustment_percentage", "is_default"]
    template_name = FORM_CLASSES
    success_url = reverse_lazy("pricing:group_list")
    permission_required = "pricing.add_customergroup"

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Add price group", back_url=self.success_url, **kwargs)


class CustomerGroupUpdateView(StoreCrudMixin, UpdateView):
    model = CustomerGroup
    fields = ["name", "price_adjustment_percentage", "is_default"]
    template_name = FORM_CLASSES
    success_url = reverse_lazy("pricing:group_list")
    permission_required = "pricing.change_customergroup"

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Edit price group", back_url=self.success_url, **kwargs)


class CustomerGroupDeleteView(StoreCrudMixin, DeleteContextMixin, DeleteView):
    model = CustomerGroup
    template_name = DELETE_TEMPLATE
    success_url = reverse_lazy("pricing:group_list")
    permission_required = "pricing.delete_customergroup"
