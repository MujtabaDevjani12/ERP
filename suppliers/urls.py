from django.urls import path

from . import views

app_name = "suppliers"

urlpatterns = [
    path("", views.SupplierListView.as_view(), name="supplier_list"),
    path("add/", views.SupplierCreateView.as_view(), name="supplier_add"),
    path("<int:pk>/edit/", views.SupplierUpdateView.as_view(), name="supplier_edit"),
    path("<int:pk>/delete/", views.SupplierDeleteView.as_view(), name="supplier_delete"),

    path("purchases/", views.PurchaseOrderListView.as_view(), name="purchase_list"),
    path("purchases/add/", views.purchase_order_form, name="purchase_add"),
    path("purchases/<int:pk>/edit/", views.purchase_order_form, name="purchase_edit"),
    path("purchases/<int:pk>/", views.purchase_order_detail, name="purchase_detail"),
    path("purchases/<int:pk>/return/", views.purchase_return_form, name="purchase_return"),
]
