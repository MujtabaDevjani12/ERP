from django.urls import path

from . import backup_views, hub, views

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
    path("backup/download/", backup_views.download, name="backup_download"),
    path("backup/import/", backup_views.import_view, name="backup_import"),
    path("backup/saved/<str:name>/", backup_views.saved, name="backup_saved"),
    path("backup/saved/<str:name>/restore/", backup_views.restore_saved, name="backup_restore_saved"),
    path("<slug:section>/", hub.hub_section, name="hub_section"),
]
