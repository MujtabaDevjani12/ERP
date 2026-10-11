from django.urls import path

from . import views

app_name = "pricing"

urlpatterns = [
    path("taxes/", views.TaxListView.as_view(), name="tax_list"),
    path("taxes/add/", views.TaxCreateView.as_view(), name="tax_add"),
    path("taxes/<int:pk>/edit/", views.TaxUpdateView.as_view(), name="tax_edit"),
    path("taxes/<int:pk>/delete/", views.TaxDeleteView.as_view(), name="tax_delete"),

    path("discounts/", views.DiscountListView.as_view(), name="discount_list"),
    path("discounts/add/", views.DiscountCreateView.as_view(), name="discount_add"),
    path("discounts/<int:pk>/edit/", views.DiscountUpdateView.as_view(), name="discount_edit"),
    path("discounts/<int:pk>/delete/", views.DiscountDeleteView.as_view(), name="discount_delete"),

    path("groups/", views.CustomerGroupListView.as_view(), name="group_list"),
    path("groups/add/", views.CustomerGroupCreateView.as_view(), name="group_add"),
    path("groups/<int:pk>/edit/", views.CustomerGroupUpdateView.as_view(), name="group_edit"),
    path("groups/<int:pk>/delete/", views.CustomerGroupDeleteView.as_view(), name="group_delete"),
]
