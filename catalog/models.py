from django.db import models

from core.models import TimeStampedModel
from tenants.models import Store


class Category(TimeStampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="categories")
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="children")
    name = models.CharField(max_length=100)
    image = models.ImageField(upload_to="categories/", blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Categories"
        constraints = [
            models.UniqueConstraint(fields=["store", "parent", "name"], name="uniq_category_name_per_parent"),
        ]

    def __str__(self):
        return self.name


class Brand(TimeStampedModel):
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="brands")
    name = models.CharField(max_length=100)
    image = models.ImageField(upload_to="brands/", blank=True, null=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["store", "name"], name="uniq_brand_name_per_store")]

    def __str__(self):
        return self.name


class Unit(TimeStampedModel):
    """Piece, Kg, Gram, Tola, Litre, Dozen... allow_decimal off for things
    you always sell whole (Piece), on for weight/volume (Kg, Gram, Tola)."""
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="units")
    name = models.CharField(max_length=50)
    short_name = models.CharField(max_length=10)
    allow_decimal = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["store", "name"], name="uniq_unit_name_per_store")]

    def __str__(self):
        return self.short_name


class ProductAttribute(TimeStampedModel):
    """A variant dimension: Size, Color, Karat, Weight, Fabric... reusable
    across products. Values live in ProductAttributeValue below."""
    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="attributes")
    name = models.CharField(max_length=60)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["store", "name"], name="uniq_attribute_name_per_store")]

    def __str__(self):
        return self.name


class ProductAttributeValue(TimeStampedModel):
    attribute = models.ForeignKey(ProductAttribute, on_delete=models.CASCADE, related_name="values")
    value = models.CharField(max_length=60)

    class Meta:
        ordering = ["value"]
        constraints = [models.UniqueConstraint(fields=["attribute", "value"], name="uniq_value_per_attribute")]

    def __str__(self):
        return f"{self.attribute.name}: {self.value}"


class Product(TimeStampedModel):
    PRODUCT_TYPE_CHOICES = [("single", "Single product"), ("variant", "Has variants")]
    # What the thing is. Only "standard" goods are counted in stock; a service/digital item is always available and a combo sells
    # a bundle of other products (its components are what leave the shelf).
    ITEM_STANDARD, ITEM_SERVICE, ITEM_COMBO, ITEM_DIGITAL = "standard", "service", "combo", "digital"
    ITEM_TYPE_CHOICES = [(ITEM_STANDARD, "Standard"), (ITEM_SERVICE, "Service"), (ITEM_COMBO, "Combo"), (ITEM_DIGITAL, "Digital")]
    SYMBOLOGY_CHOICES = [("code128", "Code 128"), ("code39", "Code 39"), ("ean13", "EAN-13"), ("ean8", "EAN-8"), ("upca", "UPC-A")]

    store = models.ForeignKey(Store, on_delete=models.CASCADE, related_name="products")
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="products")
    brand = models.ForeignKey(Brand, on_delete=models.SET_NULL, null=True, blank=True, related_name="products")
    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="products")
    tax = models.ForeignKey("pricing.Tax", on_delete=models.SET_NULL, null=True, blank=True, related_name="products")

    name = models.CharField(max_length=180)
    sku = models.CharField(max_length=60, help_text="Base SKU — each variant gets its own suffix.")
    description = models.TextField(blank=True)
    product_type = models.CharField(max_length=10, choices=PRODUCT_TYPE_CHOICES, default="single")
    item_type = models.CharField(max_length=10, choices=ITEM_TYPE_CHOICES, default=ITEM_STANDARD)
    barcode_symbology = models.CharField(max_length=10, choices=SYMBOLOGY_CHOICES, default="code128")
    promo_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, help_text="Special price while the promotion runs.")
    promo_start = models.DateField(null=True, blank=True)
    promo_end = models.DateField(null=True, blank=True)
    image = models.ImageField(upload_to="products/", blank=True, null=True)
    alert_quantity = models.DecimalField(max_digits=12, decimal_places=3, default=0, help_text="Low-stock warning threshold.")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [models.UniqueConstraint(fields=["store", "sku"], name="uniq_product_sku_per_store")]

    def __str__(self):
        return self.name

    @property
    def has_multiple_variants(self) -> bool:
        return self.variants.count() > 1

    @property
    def tracks_stock(self) -> bool:
        """Standard goods only. Services and digital items have no shelf; a combo's stock is its components' stock."""
        return self.item_type == self.ITEM_STANDARD

    def promo_is_live(self, today=None) -> bool:
        if self.promo_price is None:
            return False
        from django.utils import timezone
        today = today or timezone.localdate()
        return (self.promo_start is None or self.promo_start <= today) and (self.promo_end is None or today <= self.promo_end)


class ProductImage(TimeStampedModel):
    """Extra pictures of a product (the cover picture stays on Product.image and is what the POS tile shows)."""
    MAX_PER_PRODUCT = 8
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="gallery")
    image = models.ImageField(upload_to="products/gallery/")
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.product.name} picture {self.pk}"


class ProductVariant(TimeStampedModel):
    """Every product — even a 'single' one — has at least one variant; a
    simple product just has exactly one, named 'Default'. Sales, purchases
    and stock all point at a variant, never at Product directly, so the
    rest of the system doesn't need to special-case simple vs variant
    products."""
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    name = models.CharField(max_length=120, default="Default", help_text="e.g. 'Small / Red' or '22K / 5g'. 'Default' for a simple product.")
    attribute_values = models.ManyToManyField(ProductAttributeValue, blank=True, related_name="variants")
    sku = models.CharField(max_length=80, unique=True)
    barcode = models.CharField(max_length=64, unique=True, blank=True)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    selling_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    image = models.ImageField(upload_to="products/variants/", blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["product", "name"]

    def __str__(self):
        return f"{self.product.name} — {self.name}" if self.name != "Default" else self.product.name

    @property
    def current_price(self):
        """What the POS charges: the product's promotion price while it runs (and only when it is really lower), else the normal price."""
        product = self.product
        if product.promo_is_live() and product.promo_price < self.selling_price:
            return product.promo_price
        return self.selling_price

    @property
    def on_promotion(self) -> bool:
        return self.current_price != self.selling_price

    def save(self, *args, **kwargs):
        if not self.barcode:
            self.barcode = self._generate_barcode_value()
        super().save(*args, **kwargs)

    def _generate_barcode_value(self) -> str:
        import random
        from configuration.models import SystemConfiguration

        sep = SystemConfiguration.get().barcode_separator or ""
        store_code = str(self.product.store_id).zfill(3)
        while True:
            candidate = f"{store_code}{sep}{self.product_id or 0}{sep}{random.randint(1000, 9999)}"
            if not ProductVariant.objects.filter(barcode=candidate).exists():
                return candidate


class ComboItem(TimeStampedModel):
    """One ingredient of a combo product: selling the combo takes `quantity` of `component` off the shelf."""
    combo = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="combo_items")
    component = models.ForeignKey(ProductVariant, on_delete=models.PROTECT, related_name="used_in_combos")
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=1)

    class Meta:
        ordering = ["id"]
        constraints = [models.UniqueConstraint(fields=["combo", "component"], name="uniq_component_per_combo")]

    def __str__(self):
        return f"{self.combo.name}: {self.quantity} x {self.component}"
