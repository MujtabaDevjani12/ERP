"""
Small helpers shared across apps: barcode image generation and simple
Pakistani-CNIC formatting/validation (used by customers.Customer.cnic).

Kept dependency-free of any single app's models so it can't create import
cycles.
"""
import io
import re

CNIC_RE = re.compile(r"^\d{5}-?\d{7}-?\d{1}$")


def is_valid_cnic(value: str) -> bool:
    return bool(value) and bool(CNIC_RE.match(value.strip()))


def format_cnic(value: str) -> str:
    """'3520112345671' -> '35201-1234567-1'. Leaves already-formatted values alone."""
    digits = re.sub(r"\D", "", value or "")
    if len(digits) != 13:
        return value
    return f"{digits[0:5]}-{digits[5:12]}-{digits[12]}"


def generate_barcode_png(code: str, symbology: str = "code128") -> bytes:
    """Render a barcode for `code` (Code 128 unless another symbology is named) and return PNG bytes."""
    import barcode
    from barcode.writer import ImageWriter

    buffer = io.BytesIO()
    kind = symbology if symbology in ("code128", "code39", "ean13", "ean8", "upca") else "code128"
    extra = {"add_checksum": False} if kind == "code39" else {}               # print exactly the typed text, like the on-screen preview
    barcode.get(kind, code, writer=ImageWriter(), options=extra).write(
        buffer,
        options={"write_text": True, "module_height": 10, "font_size": 8, "quiet_zone": 2},
    )
    return buffer.getvalue()


def generate_qrcode_png(data: str) -> bytes:
    import qrcode

    img = qrcode.make(data)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------- Theming
def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = (hex_color or "").lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(ch * 2 for ch in hex_color)
    if len(hex_color) != 6:
        hex_color = "4f46e5"  # fallback to the default indigo brand color
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def _mix(rgb: tuple[int, int, int], target: tuple[int, int, int], weight: float) -> tuple[float, float, float]:
    """Blend `rgb` toward `target` by `weight` (0 = no change, 1 = pure target)."""
    return tuple(c + (t - c) * weight for c, t in zip(rgb, target))


def generate_color_shades(primary_hex: str) -> dict:
    """Builds a Tailwind-style 50-900 shade scale from a single hex color,
    treating the given color as shade 600 (the app's main button/link
    color) — lighter shades blend toward white, darker ones toward black.
    Used to drive the dynamic `brand` palette injected into base.html's
    Tailwind config, so the whole UI re-colors from one 'Primary color'
    setting without touching every template."""
    base = _hex_to_rgb(primary_hex)
    white = (255, 255, 255)
    black = (0, 0, 0)
    weights = {
        50: ("white", 0.95), 100: ("white", 0.88), 200: ("white", 0.74),
        300: ("white", 0.56), 400: ("white", 0.30), 500: ("white", 0.12),
        600: ("none", 0), 700: ("black", 0.12), 800: ("black", 0.26), 900: ("black", 0.42),
    }
    shades = {}
    for shade, (direction, weight) in weights.items():
        if direction == "white":
            shades[str(shade)] = _rgb_to_hex(_mix(base, white, weight))
        elif direction == "black":
            shades[str(shade)] = _rgb_to_hex(_mix(base, black, weight))
        else:
            shades[str(shade)] = _rgb_to_hex(base)
    return shades


# Font choices offered in System Configuration > Branding, mapped to their
# Google Fonts family query string (weights picked to cover the UI's usage:
# 400/500 body text, 600/700 semibold labels, 800 headings).
GOOGLE_FONT_QUERIES = {
    "Inter": "Inter:wght@400;500;600;700;800",
    "Poppins": "Poppins:wght@400;500;600;700;800",
    "Roboto": "Roboto:wght@400;500;700;900",
    "Nunito": "Nunito:wght@400;600;700;800",
    "Manrope": "Manrope:wght@400;500;600;700;800",
    "Lato": "Lato:wght@400;700;900",
    "Work Sans": "Work+Sans:wght@400;500;600;700;800",
}
