"""Authorization rules of the access API.

These are plain functions on plain data so they are easy to test. Authentication
happens before the API is called: the Lambda authorizer validates the token and
passes the verified user name. The API decides what that user may do.
"""

from __future__ import annotations

from typing import Any

from aws_lambda_powertools.event_handler.exceptions import ServiceError

from jit_access.common import store
from jit_access.common.usernames import same_user


class ForbiddenError(ServiceError):
    """The signed-in user is not allowed to do this (HTTP 403)."""

    def __init__(self, msg: str):
        super().__init__(403, msg)


def is_approver(username: str, account: dict[str, Any]) -> bool:
    """True if the user is an Owner or Deputy of the account."""
    return username.strip().lower() in store.approvers_of(account)


def require_approver(username: str, account: dict[str, Any]) -> None:
    if not is_approver(username, account):
        raise ForbiddenError("Only an Owner or Deputy of this account can do this.")


def can_view(username: str, request: dict[str, Any], account: dict[str, Any] | None) -> bool:
    """The requester and the account's approvers can see a request."""
    if same_user(username, request.get("Requester", "")):
        return True
    return account is not None and is_approver(username, account)


def require_may_approve(
    username: str,
    request: dict[str, Any],
    account: dict[str, Any],
    allow_self_approval: bool,
) -> None:
    """Only an approver may approve, and not their own request unless allowed."""
    require_approver(username, account)
    if same_user(username, request.get("Requester", "")) and not allow_self_approval:
        raise ForbiddenError("You cannot approve your own request. Ask another Owner or Deputy of this account.")


def require_may_reject(username: str, request: dict[str, Any], account: dict[str, Any]) -> None:
    """Approvers can reject a request. Requesters can withdraw their own."""
    if same_user(username, request.get("Requester", "")):
        return
    require_approver(username, account)
