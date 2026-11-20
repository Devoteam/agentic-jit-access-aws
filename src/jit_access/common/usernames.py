"""Mapping of sign-in names to IAM Identity Center user names."""

from __future__ import annotations


def normalize_username(value: str, strip_domains: tuple[str, ...] = ()) -> str:
    """Return the Identity Center user name for a sign-in name.

    ``alex@contoso.onmicrosoft.com`` becomes ``alex`` only when
    ``contoso.onmicrosoft.com`` is listed in ``strip_domains``. Names from any
    other domain keep their full form, so two people from different domains can
    never collapse into the same user name. Domains are compared
    case-insensitively. The local part keeps its case, because Identity Center
    user names are case-insensitive but are stored as entered.
    """
    value = (value or "").strip()
    local, separator, domain = value.rpartition("@")
    if separator and local and domain.lower() in strip_domains:
        return local
    return value


def same_user(left: str, right: str) -> bool:
    """Compare two normalized user names the way Identity Center does."""
    return left.strip().lower() == right.strip().lower()
