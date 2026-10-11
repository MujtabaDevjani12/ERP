from django.urls import path

from . import views

app_name = "customers"

urlpatterns = [
    path("", views.CustomerListView.as_view(), name="customer_list"),
    path("add/", views.CustomerCreateView.as_view(), name="customer_add"),
    path("<int:pk>/edit/", views.CustomerUpdateView.as_view(), name="customer_edit"),
    path("<int:pk>/delete/", views.CustomerDeleteView.as_view(), name="customer_delete"),
    path("search/", views.customer_search, name="customer_search"),
    path("<int:pk>/deposit/", views.customer_deposit, name="customer_deposit"),
    path("quick-add/", views.customer_quick_add, name="customer_quick_add"),

    path("gift-cards/", views.GiftCardListView.as_view(), name="giftcard_list"),
    path("gift-cards/lookup/", views.giftcard_lookup, name="giftcard_lookup"),
    path("gift-cards/<int:pk>/topup/", views.giftcard_topup, name="giftcard_topup"),
    path("gift-cards/<int:pk>/history/", views.giftcard_history, name="giftcard_history"),
    path("gift-cards/add/", views.GiftCardCreateView.as_view(), name="giftcard_add"),
    path("gift-cards/<int:pk>/edit/", views.GiftCardUpdateView.as_view(), name="giftcard_edit"),
    path("gift-cards/<int:pk>/delete/", views.GiftCardDeleteView.as_view(), name="giftcard_delete"),
]
