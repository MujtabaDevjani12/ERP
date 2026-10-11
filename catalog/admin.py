from django.contrib import admin

from .models import Brand, Category, Product, ProductAttribute, ProductAttributeValue, ProductVariant, Unit


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = ("name", "sku", "barcode", "cost_price", "selling_price", "is_active")
    readonly_fields = ("barcode",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "sku", "store", "category", "brand", "product_type", "is_active")
    list_filter = ("store", "category", "brand", "product_type", "is_active")
    search_fields = ("name", "sku", "variants__sku", "variants__barcode")
    inlines = [ProductVariantInline]


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "store", "parent", "is_active")
    list_filter = ("store",)


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "store")


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("name", "short_name", "allow_decimal", "store")


class ProductAttributeValueInline(admin.TabularInline):
    model = ProductAttributeValue
    extra = 1


@admin.register(ProductAttribute)
class ProductAttributeAdmin(admin.ModelAdmin):
    list_display = ("name", "store")
    inlines = [ProductAttributeValueInline]
