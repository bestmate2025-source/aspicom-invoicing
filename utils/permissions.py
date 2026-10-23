"""utils/permissions.py
One place that says which role may change (POST / PUT / DELETE / PATCH) what.

Reading (GET) is allowed for every logged-in role, except the Users pages, which
routes/users.py keeps admin-only. Anything not listed below can only be changed
by an admin, so a new screen is locked until you decide who may use it.
"""

from __future__ import annotations

import re

WRITE_METHODS = ("POST", "PUT", "DELETE", "PATCH")

ROLES = ("admin", "manager", "sales", "store", "accountant", "user")

ROLE_LABELS = {
    "admin": "Admin",
    "manager": "Manager",
    "sales": "Sales staff",
    "store": "Purchasing / Store",
    "accountant": "Accountant",
    "user": "View-only",
}

# First matching line wins. (pattern on the URL path, roles allowed to change data)
RULES = [
    (r"^/api/users(/|$)", {"admin"}),
    (r"^/api/compan(y|ies)(/|$)", {"admin"}),
    # payments: sales and accountants record money received
    (r"^/api/payments(/|$)", {"admin", "manager", "sales", "accountant"}),
    (r"^/api/invoices/\d+/payments(/|$)", {"admin", "manager", "sales", "accountant"}),
    # invoices, quotations, clients
    (r"^/api/(invoices|clients)(/|$)", {"admin", "manager", "sales"}),
    # purchasing and stock
    (r"^/api/(inventory|suppliers|purchase-orders|stock-movements|parsed-invoices)(/|$)",
     {"admin", "manager", "store"}),
    # reading a supplier PDF is used from both the sales and purchasing screens
    (r"^/api/(parse-pdf|import-pdf)(/|$)", {"admin", "manager", "store", "sales"}),
]
_COMPILED = [(re.compile(p), roles) for p, roles in RULES]


def can_access(role: str, method: str, path: str) -> bool:
    """True if a user with this role may make this request."""
    if method.upper() not in WRITE_METHODS:
        return True
    for pattern, roles in _COMPILED:
        if pattern.match(path):
            return role in roles
    return role == "admin"
