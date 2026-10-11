"""Barcode symbologies a product can use, with the rules each one has (so a bad code is caught on the form, not on the label printer)."""
import random
import string

LABELS = {"code128": "Code 128", "code39": "Code 39", "ean13": "EAN-13", "ean8": "EAN-8", "upca": "UPC-A"}
# symbology -> number of digits WITHOUT the check digit
DIGITS = {"ean13": 12, "ean8": 7, "upca": 11}
CODE39_CHARS = set(string.ascii_uppercase + string.digits + "-. $/+%")


def check_digit(body: str) -> str:
    """EAN / UPC modulo-10 check digit of `body` (weights 3,1,3,1... from the right)."""
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(body)))
    return str((10 - total % 10) % 10)


def validate(symbology: str, value: str):
    """None when `value` can be drawn as `symbology`, else a short message for the cashier / shop owner."""
    value = (value or "").strip()
    if not value:
        return None
    name = LABELS.get(symbology, symbology)
    if symbology in DIGITS:
        size = DIGITS[symbology]
        if not value.isdigit() or len(value) not in (size, size + 1):
            return f"{name} needs {size} or {size + 1} digits (numbers only)."
        if len(value) == size + 1 and value[-1] != check_digit(value[:-1]):
            return f"The last digit of this {name} code is wrong (it should be {check_digit(value[:-1])})."
        return None
    if symbology == "code39":
        bad = sorted({c for c in value.upper() if c not in CODE39_CHARS})
        return f"Code 39 cannot hold “{bad[0]}” (capital letters, numbers and - . space $ / + % only)." if bad else None
    if any(ord(c) < 32 or ord(c) > 126 for c in value):
        return "Code 128 supports standard keyboard characters only."
    if len(value) > 64:
        return "A barcode can have at most 64 characters."
    return None


def generate(symbology: str, taken=lambda code: False) -> str:
    """A free, valid code for a numeric symbology (internal prefix 2, like shop-printed labels)."""
    size = DIGITS.get(symbology, 12)
    for _ in range(80):
        body = "2" + "".join(random.choice("0123456789") for _ in range(size - 1))
        code = body + check_digit(body)
        if not taken(code):
            return code
    raise RuntimeError("no free barcode")


def png_format(symbology: str) -> str:
    return {"code128": "code128", "code39": "code39", "ean13": "ean13", "ean8": "ean8", "upca": "upca"}.get(symbology, "code128")
