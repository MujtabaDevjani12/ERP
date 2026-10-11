from django.urls import path

from . import transfer_views as tv, views, warehouse_views as wv

app_name = "inventory"

urlpatterns = [
    path("stock/", views.StockListView.as_view(), name="stock_list"),
    path("adjustments/", views.StockAdjustmentListView.as_view(), name="adjustment_list"),
    path("adjustments/scan/", views.quantity_adjustment_view, name="quantity_adjustment"),

    path("transfers/", tv.TransferListView.as_view(), name="transfer_list"),
    path("transfers/new/", tv.transfer_new, name="transfer_add"),
    path("transfers/lookup/", tv.transfer_lookup, name="transfer_lookup"),
    path("transfers/<int:pk>/", tv.transfer_detail, name="transfer_detail"),
    path("transfers/<int:pk>/send/", tv.transfer_send, name="transfer_send"),
    path("transfers/<int:pk>/receive/", tv.transfer_receive, name="transfer_receive"),
    path("transfers/<int:pk>/cancel/", tv.transfer_cancel, name="transfer_cancel"),
    path("transfers/<int:pk>/delete/", tv.transfer_delete, name="transfer_delete"),

    path("warehouses/", wv.warehouse_list, name="warehouse_list"),
    path("warehouses/new/", wv.warehouse_form, name="warehouse_add"),
    path("warehouses/<int:pk>/edit/", wv.warehouse_form, name="warehouse_edit"),
    path("warehouses/<int:pk>/default/", wv.warehouse_default, name="warehouse_default"),

    path("racks/", views.RackListView.as_view(), name="rack_list"),
    path("racks/add/", views.RackCreateView.as_view(), name="rack_add"),
    path("racks/<int:pk>/edit/", views.RackUpdateView.as_view(), name="rack_edit"),
    path("racks/<int:pk>/delete/", views.RackDeleteView.as_view(), name="rack_delete"),
]
