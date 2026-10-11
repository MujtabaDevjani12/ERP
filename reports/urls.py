from django.urls import path

from . import data_views, views

app_name = "reports"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("all/", views.pack_index, name="pack_index"),
    path("r/<slug:slug>/", views.pack_report, name="pack_report"),
    path("r/<slug:slug>/csv/", views.pack_report_csv, name="pack_report_csv"),
    path("r/<slug:slug>/<str:fmt>/", views.pack_report_export, name="pack_report_export"),
    path("data/", data_views.data_hub, name="data_hub"),
    path("data/<slug:key>/export/", data_views.data_export, name="data_export"),
    path("data/<slug:key>/template/", data_views.data_template, name="data_template"),
    path("data/<slug:key>/import/", data_views.data_import, name="data_import"),
    path("export/sales.csv", views.export_sales_csv, name="export_sales_csv"),
    path("export/products.csv", views.export_products_csv, name="export_products_csv"),
    path("import/products/", views.import_products_csv, name="import_products_csv"),
]
