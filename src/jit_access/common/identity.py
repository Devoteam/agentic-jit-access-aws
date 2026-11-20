"""Find the IAM Identity Center user for a signed-in Entra ID user.

When ``USERS_TABLE`` is set, the users table is the only source: it maps the
Entra ID user name to the Identity Center UserId, so the names do not have to
match and the identity store is not called. Without it, the user is looked up
in the identity store by ``userName``.
"""

from __future__ import annotations

from functools import lru_cache

import boto3
from botocore.exceptions import ClientError

from jit_access.common import store
from jit_access.common.config import Settings


@lru_cache(maxsize=1)
def _identitystore():
    return boto3.client("identitystore")


def reset_clients() -> None:
    _identitystore.cache_clear()


def find_user_id(username: str) -> str | None:
    """Return the Identity Center user ID for a user name, or ``None`` if unknown."""
    settings = Settings.from_env()
    if settings.users_table:
        item = store.get_user(username)
        user_id = str((item or {}).get("UserId", "")).strip()
        return user_id or None

    try:
        response = _identitystore().get_user_id(
            IdentityStoreId=settings.identity_store_id,
            AlternateIdentifier={
                "UniqueAttribute": {"AttributePath": "userName", "AttributeValue": username},
            },
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ResourceNotFoundException":
            return None
        raise
    return response["UserId"]
