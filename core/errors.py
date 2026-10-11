"""Business-rule exceptions shared by every ERP service. Views can catch
ERPError and show str(error) to the user as a normal validation message."""


class ERPError(Exception):
    """A business rule was violated (nothing was saved)."""


class InsufficientStock(ERPError):
    pass


class UnbalancedJournal(ERPError):
    pass


class ApprovalError(ERPError):
    pass
