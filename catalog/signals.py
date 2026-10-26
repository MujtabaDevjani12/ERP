from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Product, ProductVariant


@receiver(post_save, sender=Product)
def ensure_default_variant(sender, instance: Product, created, **kwargs):
    """A 'single' product always needs exactly one sellable variant to hang
    price/stock/barcode off of. If none exists yet (e.g. created through
    the admin without one), make it — 'variant' products are left alone,
    their variants are added explicitly on the product form."""
    if kwargs.get("raw"):                                          # loading a backup: its own variants come with it
        return
    if getattr(instance, "_skip_default_variant", False):      # the product form creates the variants itself
        return
    if instance.product_type != "single":
        return
    if instance.variants.exists():
        return
    ProductVariant.objects.create(
        product=instance,
        name="Default",
        sku=f"{instance.sku}-DEF",
        cost_price=0,
        selling_price=0,
    )
