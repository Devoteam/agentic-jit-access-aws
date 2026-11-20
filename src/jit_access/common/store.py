"""DynamoDB access for access requests and account ownership.

The ``requests`` table holds one item per request for its whole life: the
submission, the decision, and every provisioning step. Keeping everything in
one item gives a complete record of a grant and lets a single conditional write
decide a request.

The ``accounts`` table maps each AWS account that can be requested to the people
who may approve requests for it.
"""

from __future__ import annotations

import json
import time
from decimal import Decimal
from functools import lru_cache
from typing import Any

import boto3
from botocore.exceptions import ClientError

from jit_access.common.config import Settings

ACCOUNT_INDEX = "AccountId-CreatedAt-index"

STATUS_PENDING = "Pending"
STATUS_APPROVED = "Approved"
STATUS_REJECTED = "Rejected"

PHASE_PROVISIONING = "Provisioning"
PHASE_ACTIVE = "Active"
PHASE_REVOKING = "Revoking"
PHASE_EXPIRED = "Expired"
PHASE_FAILED = "Failed"


def now() -> int:
    """Current time as epoch seconds. Tests replace this to control time."""
    return int(time.time())


@lru_cache(maxsize=1)
def _dynamodb():
    return boto3.resource("dynamodb")


def reset_clients() -> None:
    """Drop cached AWS clients. Used by tests that create fresh mocks."""
    _dynamodb.cache_clear()


def _requests_table():
    return _dynamodb().Table(Settings.from_env().requests_table)


def _accounts_table():
    return _dynamodb().Table(Settings.from_env().accounts_table)


def _to_dynamodb(value: Any) -> Any:
    """Convert JSON-style data to values DynamoDB accepts (no floats)."""
    return json.loads(json.dumps(value), parse_float=Decimal)


def to_public(value: Any) -> Any:
    """Convert a DynamoDB item to plain JSON-serializable data."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: to_public(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_public(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(to_public(item) for item in value)
    return value


# --------------------------------------------------------------------------- accounts


def get_account(account_id: str) -> dict[str, Any] | None:
    """Return the ownership record of an account, or ``None`` if not registered."""
    response = _accounts_table().get_item(Key={"AccountId": account_id}, ConsistentRead=True)
    return response.get("Item")


def approvers_of(account: dict[str, Any]) -> frozenset[str]:
    """Owners and deputies of an account, lower-cased for comparison."""
    names: set[str] = set()
    for attribute in ("Owners", "Deputies"):
        names.update(str(name).strip().lower() for name in account.get(attribute, set()) or ())
    return frozenset(name for name in names if name)


# --------------------------------------------------------------------------- users


def get_user(entra_username: str) -> dict[str, Any] | None:
    """Return the Identity Center mapping of an Entra ID user name, or ``None`` if not mapped."""
    table = _dynamodb().Table(Settings.from_env().users_table)
    response = table.get_item(Key={"EntraUsername": entra_username.strip().lower()}, ConsistentRead=True)
    return response.get("Item")


# --------------------------------------------------------------------------- requests


def put_request(item: dict[str, Any]) -> None:
    """Store a new request. Fails if the ID already exists."""
    _requests_table().put_item(
        Item=_to_dynamodb(item),
        ConditionExpression="attribute_not_exists(RequestId)",
    )


def get_request(request_id: str) -> dict[str, Any] | None:
    response = _requests_table().get_item(Key={"RequestId": request_id}, ConsistentRead=True)
    return response.get("Item")


def list_requests_by_account(account_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Requests for one account, newest first."""
    response = _requests_table().query(
        IndexName=ACCOUNT_INDEX,
        KeyConditionExpression="AccountId = :account",
        ExpressionAttributeValues={":account": account_id},
        ScanIndexForward=False,
        Limit=limit,
    )
    return response.get("Items", [])


def decide_request(
    request_id: str,
    status: str,
    decided_by: str,
    expected_fingerprint: str | None = None,
) -> dict[str, Any] | None:
    """Move a request from Pending to Approved or Rejected in one atomic write.

    The write succeeds only if the request is still pending and, when
    ``expected_fingerprint`` is given, its stored fingerprint still matches.
    Returns the updated item, or ``None`` if the condition failed.
    """
    condition = "#status = :pending"
    values: dict[str, Any] = {
        ":pending": STATUS_PENDING,
        ":status": status,
        ":by": decided_by,
        ":at": now(),
    }
    if expected_fingerprint is not None:
        condition += " AND PolicyFingerprint = :fingerprint"
        values[":fingerprint"] = expected_fingerprint
    try:
        response = _requests_table().update_item(
            Key={"RequestId": request_id},
            UpdateExpression="SET #status = :status, DecidedBy = :by, DecidedAt = :at",
            ConditionExpression=condition,
            ExpressionAttributeNames={"#status": "Status"},
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise
    return response.get("Attributes")


# --------------------------------------------------------------------------- workflow


def set_attributes(request_id: str, **attributes: Any) -> None:
    """Set top-level attributes on a request."""
    if not attributes:
        return
    names = {f"#a{index}": key for index, key in enumerate(attributes)}
    values = {f":v{index}": _to_dynamodb(value) for index, value in enumerate(attributes.values())}
    expression = "SET " + ", ".join(f"#a{index} = :v{index}" for index in range(len(attributes)))
    _requests_table().update_item(
        Key={"RequestId": request_id},
        UpdateExpression=expression,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
        ConditionExpression="attribute_exists(RequestId)",
    )


def append_step(request_id: str, step: str, status: str, detail: str = "") -> None:
    """Append a provisioning step to the request's audit trail."""
    entry = {"Step": step, "Status": status, "At": now()}
    if detail:
        entry["Detail"] = detail[:500]
    _requests_table().update_item(
        Key={"RequestId": request_id},
        UpdateExpression="SET Steps = list_append(if_not_exists(Steps, :empty), :entry)",
        ExpressionAttributeValues={":empty": [], ":entry": [_to_dynamodb(entry)]},
        ConditionExpression="attribute_exists(RequestId)",
    )
