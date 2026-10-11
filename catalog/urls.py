from django.urls import path

from . import barcode_views, views

app_name = "catalog"

urlpatterns = [
    path("products/", views.ProductListView.as_view(), name="product_list"),
    path("products/add/", views.product_form, name="product_add"),
    path("products/<int:pk>/edit/", views.product_form, name="product_edit"),
    path("barcode-generate/", views.barcode_generate, name="barcode_generate"),
    path("barcode-preview.png", views.barcode_preview, name="barcode_preview"),
    path("variants/<int:pk>/barcode.png", views.variant_barcode_png, name="variant_barcode"),
    path("api/variants/", views.variant_lookup, name="api_variant_lookup"),
    path("barcodes/", barcode_views.barcode_print, name="barcode_print"),
    path("api/barcode-items/", barcode_views.barcode_items, name="barcode_items"),

    path("categories/", views.CategoryListView.as_view(), name="category_list"),
    path("categories/add/", views.CategoryCreateView.as_view(), name="category_add"),
    path("categories/<int:pk>/edit/", views.CategoryUpdateView.as_view(), name="category_edit"),
    path("categories/<int:pk>/delete/", views.CategoryDeleteView.as_view(), name="category_delete"),

    path("brands/", views.BrandListView.as_view(), name="brand_list"),
    path("brands/add/", views.BrandCreateView.as_view(), name="brand_add"),
    path("brands/<int:pk>/edit/", views.BrandUpdateView.as_view(), name="brand_edit"),
    path("brands/<int:pk>/delete/", views.BrandDeleteView.as_view(), name="brand_delete"),

    path("units/", views.UnitListView.as_view(), name="unit_list"),
    path("units/add/", views.UnitCreateView.as_view(), name="unit_add"),
    path("units/<int:pk>/edit/", views.UnitUpdateView.as_view(), name="unit_edit"),
    path("units/<int:pk>/delete/", views.UnitDeleteView.as_view(), name="unit_delete"),

    path("attributes/", views.attribute_list, name="attribute_list"),
    path("attributes/<int:pk>/values/", views.attribute_values, name="attribute_values"),
]
