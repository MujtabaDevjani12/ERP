"""
The only thing the legacy POS views import. Every function swallows its own errors, so the sale
screen keeps working even if this app is misconfigured or accounting is not set up yet.
"""
import logging

logger = logging.getLogger(__name__)


def on_sale_completed(sale, payments=()):
    try:
        from .services import on_sale_completed as impl
        impl(sale, payments)
    except Exception:                                    # noqa: BLE001
        logger.exception("pos_erp.on_sale_completed failed for %s", getattr(sale, "pk", "?"))


def on_payment_added(payment):
    try:
        from .services import on_payment_added as impl
        impl(payment)
    except Exception:                                    # noqa: BLE001
        logger.exception("pos_erp.on_payment_added failed for %s", getattr(payment, "pk", "?"))


def on_register_closed(session, user=None):
    try:
        from .services import on_register_closed as impl
        impl(session, user)
    except Exception:                                    # noqa: BLE001
        logger.exception("pos_erp.on_register_closed failed for %s", getattr(session, "pk", "?"))
