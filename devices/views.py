from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from core.mixins import DeleteContextMixin, SimpleListMixin, StoreCrudMixin
from .models import Device


class DeviceListView(SimpleListMixin, ListView):
    model = Device
    page_title = "Printers, scanners & scales"
    add_url_name = "devices:device_add"
    edit_url_name = "devices:device_edit"
    delete_url_name = "devices:device_delete"


class DeviceCreateView(StoreCrudMixin, CreateView):
    model = Device
    fields = ["type", "name", "connection_info", "is_active"]
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("devices:device_list")
    permission_required = "devices.add_device"

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Add device", back_url=self.success_url, **kwargs)


class DeviceUpdateView(StoreCrudMixin, UpdateView):
    model = Device
    fields = ["type", "name", "connection_info", "is_active"]
    template_name = "partials/simple_form.html"
    success_url = reverse_lazy("devices:device_list")
    permission_required = "devices.change_device"

    def get_context_data(self, **kwargs):
        return super().get_context_data(title="Edit device", back_url=self.success_url, **kwargs)


class DeviceDeleteView(StoreCrudMixin, DeleteContextMixin, DeleteView):
    model = Device
    template_name = "partials/confirm_delete.html"
    success_url = reverse_lazy("devices:device_list")
    permission_required = "devices.delete_device"
