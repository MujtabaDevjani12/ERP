from core.utils import GOOGLE_FONT_QUERIES, generate_color_shades
from .models import POSConfiguration, SystemConfiguration


def pos_settings(request):
    """Exposes `pos_settings` (current store's POS config, or None) and
    `system_settings` to every template — used for things like the POS
    product-button color and the site name in the navbar. Also computes
    the live theme (brand color scale + Google Font query string) from
    System Configuration > Branding, so base.html can build a dynamic
    Tailwind config and font <link> without any template touching colors
    directly."""
    store = getattr(request, "store", None)
    pos_conf = None
    if store is not None:
        pos_conf = POSConfiguration.objects.filter(store=store).first()
    system_settings = SystemConfiguration.get()
    return {
        "pos_settings": pos_conf,
        "system_settings": system_settings,
        "brand_shades": generate_color_shades(system_settings.primary_color),
        "google_font_query": GOOGLE_FONT_QUERIES.get(system_settings.font_family, GOOGLE_FONT_QUERIES["Inter"]),
    }
