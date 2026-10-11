from django.urls import path

from . import hub, views

app_name = "configuration"

urlpatterns = [
    path("", hub.hub_index, name="hub"),
    # old addresses (kept so existing links and bookmarks work)
    path("pos/", views.pos_settings_view, name="pos_settings"),
    path("system/", views.system_settings_view, name="system_settings"),
    path("numbering/", hub.hub_section, {"section": "numbering"}, name="numbering_prefixes"),
    path("currencies/", views.CurrencyListView.as_view(), name="currency_list"),
    path("currencies/add/", views.CurrencyCreateView.as_view(), name="currency_add"),
    path("currencies/<int:pk>/edit/", views.CurrencyUpdateView.as_view(), name="currency_edit"),
    path("currencies/<int:pk>/delete/", views.CurrencyDeleteView.as_view(), name="currency_delete"),
    path("receipt-preview/", hub.receipt_preview, name="receipt_preview"),
    path("<slug:section>/", hub.hub_section, name="hub_section"),
]
