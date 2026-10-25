"""
Minimal Stripe client (Checkout + webhook check) using only the standard library, so no extra package is needed.
Keys come from Platform settings. Tests replace `_request` with a fake.
"""
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.stripe.com/v1"
ZERO_DECIMAL = {"BIF", "CLP", "DJF", "GNF", "JPY", "KMF", "KRW", "MGA", "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF"}


class StripeError(Exception):
    pass


def amount_minor(amount, currency):
    return int(amount) if currency.upper() in ZERO_DECIMAL else int(round(float(amount) * 100))


def _flatten(prefix, value, out):
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(f"{prefix}[{k}]" if prefix else k, v, out)
    elif isinstance(value, (list, tuple)):
        for i, v in enumerate(value):
            _flatten(f"{prefix}[{i}]", v, out)
    elif value is not None:
        out.append((prefix, str(value)))


def _request(method, path, secret, data=None, timeout=20):
    url = API + path
    body = None
    if data:
        pairs = []
        _flatten("", data, pairs)
        encoded = urllib.parse.urlencode(pairs)
        if method == "GET":
            url += "?" + encoded
        else:
            body = encoded.encode()
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Authorization", "Bearer " + secret)
    if body is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:       # noqa: S310 - fixed https host
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read().decode()).get("error", {}).get("message", "")
        except Exception:                                                # noqa: BLE001
            message = ""
        raise StripeError(message or f"Stripe refused the request ({exc.code}).") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise StripeError("Could not reach Stripe. Please try again in a moment.") from exc


def create_checkout_session(invoice, *, secret, success_url, cancel_url, customer_email=""):
    data = {
        "mode": "payment",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "client_reference_id": invoice.number,
        "metadata": {"invoice_id": invoice.pk, "invoice_number": invoice.number, "company_id": invoice.company_id},
        "payment_intent_data": {"metadata": {"invoice_id": invoice.pk, "invoice_number": invoice.number}},
        "line_items": [{
            "quantity": 1,
            "price_data": {
                "currency": invoice.currency.lower(),
                "unit_amount": amount_minor(invoice.total, invoice.currency),
                "product_data": {"name": f"{invoice.plan_name} plan - {invoice.get_interval_display()} ({invoice.number})"},
            },
        }],
    }
    if customer_email:
        data["customer_email"] = customer_email
    return _request("POST", "/checkout/sessions", secret, data)


def retrieve_session(session_id, *, secret):
    return _request("GET", f"/checkout/sessions/{urllib.parse.quote(session_id)}", secret)


def verify_webhook(payload, sig_header, secret, tolerance=300, now=None):
    """Check Stripe's signature header (t=...,v1=...) and return the parsed event. Raises StripeError when it does not match."""
    if not secret or not sig_header:
        raise StripeError("Webhook secret is not configured.")
    parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
    timestamp = parts.get("t", "")
    signatures = [v for k, v in (p.split("=", 1) for p in sig_header.split(",") if "=" in p) if k == "v1"]
    if not timestamp.isdigit() or not signatures:
        raise StripeError("Bad signature header.")
    expected = hmac.new(secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in signatures):
        raise StripeError("Signature does not match.")
    if abs((now or time.time()) - int(timestamp)) > tolerance:
        raise StripeError("Webhook is too old.")
    return json.loads(payload.decode())
