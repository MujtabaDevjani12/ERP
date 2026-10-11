"""
Per-request context (user, IP, user agent) so services can write audit
rows without every function having to be passed `request`.
Uses contextvars, so it is safe for threads and async.
"""
from contextvars import ContextVar

_context: ContextVar = ContextVar("audit_context", default=None)


def set_context(user=None, ip_address=None, user_agent=""):
    return _context.set({"user": user, "ip_address": ip_address, "user_agent": user_agent or ""})


def reset_context(token):
    _context.reset(token)


def get_context():
    return _context.get() or {"user": None, "ip_address": None, "user_agent": ""}
