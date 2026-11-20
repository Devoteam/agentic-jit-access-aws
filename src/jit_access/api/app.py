"""Just-in-time access API.

Five operations, all under ``/jit``. They are exposed to AI agents as MCP tools
through Amazon Bedrock AgentCore Gateway, so the descriptions below are written
for an agent as much as for a developer.

Authentication is done before this code runs: the Lambda authorizer validates
the bearer token and passes the verified user name in the request context. This
API decides what that user may do.

Note: this module must not use ``from __future__ import annotations``. Powertools
resolves the route parameter types at runtime, and postponed annotations break
that for models defined in another module.
"""

import uuid
from typing import Annotated, Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler import APIGatewayRestResolver, Response
from aws_lambda_powertools.event_handler.exceptions import (
    BadRequestError,
    NotFoundError,
    ServiceError,
    UnauthorizedError,
)
from aws_lambda_powertools.event_handler.openapi.exceptions import RequestValidationError
from aws_lambda_powertools.event_handler.openapi.params import Query
from aws_lambda_powertools.utilities.typing import LambdaContext

from jit_access import __version__
from jit_access.api import rules
from jit_access.api.models import RequestAccessBody
from jit_access.common import identity, store
from jit_access.common.config import Settings
from jit_access.common.policy import policy_fingerprint
from jit_access.common.usernames import same_user

logger = Logger(service="jit-access-api")
app = APIGatewayRestResolver(enable_validation=True)

TAG = "Just-in-time access"
_INTERNAL_FIELDS = {
    "ExpiresAt",
    "PermissionSetArn",
    "PrincipalId",
    "AssignmentCreationId",
    "AssignmentDeletionId",
}
_STATUSES = {store.STATUS_PENDING, store.STATUS_APPROVED, store.STATUS_REJECTED}


# --------------------------------------------------------------------------- errors


def _json(status_code: int, body: dict[str, Any]) -> Response:
    return Response(status_code=status_code, content_type="application/json", body=body)


@app.exception_handler(RequestValidationError)
def _validation_error(error: RequestValidationError) -> Response:
    detail = [
        {"loc": list(item.get("loc", [])), "type": item.get("type"), "msg": item.get("msg")} for item in error.errors()
    ]
    logger.info("Request validation failed", extra={"detail": detail})
    return _json(422, {"statusCode": 422, "message": "Request validation failed", "detail": detail})


@app.exception_handler(Exception)
def _unhandled(error: Exception) -> Response:
    if isinstance(error, ServiceError):
        return _json(error.status_code, {"statusCode": error.status_code, "message": error.msg})
    logger.exception("Unhandled error")
    body: dict[str, Any] = {"statusCode": 500, "message": "Internal server error"}
    if Settings.from_env().debug:
        # Debug mode only: name the failing call so it can be found without reading the logs.
        body["debug"] = f"{type(error).__name__}: {error}"[:500]
    return _json(500, body)


# --------------------------------------------------------------------------- helpers


def _caller() -> str:
    """The verified user name placed in the request context by the authorizer."""
    context = app.current_event.raw_event.get("requestContext", {}).get("authorizer") or {}
    username = str(context.get("username", "")).strip()
    if not username:
        raise UnauthorizedError("Could not determine the signed-in user. Sign in again and retry.")
    return username


def _not_submitted(code: str, message: str) -> Response:
    """An expected refusal of request_access, returned as 200.

    AgentCore Gateway replaces every error response with a generic "internal error",
    so a 4xx would never reach the user. A refusal is a normal answer, not a failure.
    """
    logger.info("Request not submitted", extra={"code": code})
    return _json(200, {"Submitted": False, "Code": code, "Message": message})


def _not_decided(code: str, message: str) -> Response:
    """An expected refusal of approve or reject, returned as 200 for the same reason as above."""
    logger.info("Request not decided", extra={"code": code})
    return _json(200, {"Decided": False, "Code": code, "Message": message})


def _decidable(caller: str, request_id: str):
    """Load a request for approve or reject. Returns (request, account, refusal)."""
    request = store.get_request(request_id)
    account = store.get_account(request["AccountId"]) if request else None
    if not request or not rules.can_view(caller, request, account):
        # Same answer for "missing" and "not yours": do not reveal that the request exists.
        return None, None, _not_decided("RequestNotFound", f"Request '{request_id}' was not found.")
    if account is None:
        return (
            None,
            None,
            _not_decided(
                "AccountNotRegistered",
                f"Account {request['AccountId']} is no longer registered for just-in-time access.",
            ),
        )
    if request.get("Status") != store.STATUS_PENDING:
        return None, None, _not_decided("AlreadyDecided", f"Request is already {request.get('Status')}.")
    return request, account, None


def _view(item: dict[str, Any]) -> dict[str, Any]:
    """The public representation of a request."""
    return store.to_public({key: value for key, value in item.items() if key not in _INTERNAL_FIELDS})


def _load(request_id: str) -> dict[str, Any]:
    item = store.get_request(request_id)
    if not item:
        raise NotFoundError(f"Request '{request_id}' was not found.")
    return item


def _account_of(request: dict[str, Any]) -> dict[str, Any]:
    account = store.get_account(request["AccountId"])
    if not account:
        raise NotFoundError(f"Account '{request['AccountId']}' is no longer enabled for just-in-time access.")
    return account


# --------------------------------------------------------------------------- routes


@app.post(
    "/jit/request-access",
    operation_id="request_access",
    summary="Request just-in-time access to an AWS account",
    description=(
        "Request temporary, time-limited access to an AWS account. Send the account, a reason, the duration "
        "in hours, and exactly one policy: PolicyDocument (a custom inline IAM policy limited to what the task "
        "needs, preferred) or PolicyArn (an AWS managed policy). The requester is always the signed-in user, "
        "so do not send a user name. The request is stored as Pending and an Owner or Deputy of the account "
        "must approve it. Nothing is granted until then. Always check Submitted in the response: when true, "
        "report the RequestId and the PolicyFingerprint; when false, no request was created, so tell the user "
        "the Message exactly as given and do not retry."
    ),
    tags=[TAG],
)
def request_access(body: RequestAccessBody) -> dict:
    settings = Settings.from_env()
    requester = _caller()
    logger.debug(
        "request_access started",
        extra={"requester": requester, "account_id": body.AccountId, "duration_hours": body.DurationHours},
    )

    # Checked first: someone outside the organisation learns nothing about which accounts exist.
    user_id = identity.find_user_id(requester)
    if user_id is None:
        return _not_submitted(
            "UserNotInOrganisation",
            f"You ({requester}) are not registered as a member of the organisation for just-in-time access, "
            "so no request was created. If you should have access, contact the platform administrator.",
        )
    logger.debug("Requester found in Identity Center", extra={"requester": requester, "user_id": user_id})

    if body.DurationHours > settings.max_duration_hours:
        return _not_submitted(
            "DurationTooLong",
            f"DurationHours must be {settings.max_duration_hours} or less, so no request was created. "
            "Ask for a shorter duration.",
        )
    allowed = settings.allowed_managed_policy_arns
    if body.PolicyArn and allowed and body.PolicyArn not in allowed:
        return _not_submitted(
            "PolicyNotAllowed",
            "This managed policy is not allowed, so no request was created. Use a custom PolicyDocument "
            "or an allowed managed policy.",
        )

    account = store.get_account(body.AccountId)
    if not account:
        return _not_submitted(
            "AccountNotRegistered",
            f"Account {body.AccountId} is not registered for just-in-time access, so no request was created. "
            "Check the account ID, or ask the platform administrator to register the account.",
        )
    if not store.approvers_of(account):
        return _not_submitted(
            "NoApprovers",
            f"Account {body.AccountId} has no Owner or Deputy who can approve requests, so no request was "
            "created. Ask the platform administrator to add approvers for the account.",
        )
    logger.debug("Account checks passed", extra={"approvers": sorted(store.approvers_of(account))})

    now = store.now()
    item: dict[str, Any] = {
        "RequestId": str(uuid.uuid4()),
        "AccountId": body.AccountId,
        "Requester": requester,
        "Reason": body.Reason,
        "DurationHours": body.DurationHours,
        "Status": store.STATUS_PENDING,
        # Resolved now, so the workflow assigns access to exactly this user. Not shown in the API.
        "PrincipalId": user_id,
        "PolicyFingerprint": policy_fingerprint(body.PolicyArn, body.PolicyDocument),
        "CreatedAt": now,
        "ApprovalDeadline": now + settings.approval_window_hours * 3600,
        "ExpiresAt": now + settings.request_retention_days * 86400,
    }
    if body.PolicyArn:
        item["PolicyArn"] = body.PolicyArn
    else:
        item["PolicyDocument"] = body.PolicyDocument
    store.put_request(item)

    logger.info(
        "Access requested",
        extra={"request_id": item["RequestId"], "account_id": body.AccountId, "requester": requester},
    )
    # 200, not 201: the OpenAPI document declares 200, and AgentCore Gateway treats
    # an undeclared status as an internal error even though the request was stored.
    return _json(200, {"Submitted": True, **_view(item)})


@app.get(
    "/jit/requests",
    operation_id="list_requests",
    summary="List access requests for an AWS account",
    description=(
        "List just-in-time access requests for one AWS account, newest first. Only an Owner or Deputy of the "
        "account can list them. Each item includes the policy and the PolicyFingerprint needed to approve it. "
        "Use the optional Status filter (Pending, Approved or Rejected) to find requests waiting for a decision."
    ),
    tags=[TAG],
)
def list_requests(
    account_id: Annotated[
        str,
        Query(alias="AccountId", pattern=r"^\d{12}$", description="The 12-digit AWS account ID."),
    ],
    status: Annotated[
        str | None,
        Query(alias="Status", description="Optional filter: Pending, Approved or Rejected."),
    ] = None,
) -> dict:
    caller = _caller()
    if status is not None and status not in _STATUSES:
        raise BadRequestError("Status must be Pending, Approved or Rejected.")

    account = store.get_account(account_id)
    if not account:
        raise NotFoundError(f"Account '{account_id}' is not enabled for just-in-time access.")
    rules.require_approver(caller, account)

    items = store.list_requests_by_account(account_id)
    if status:
        items = [item for item in items if item.get("Status") == status]
    return _json(200, {"Items": [_view(item) for item in items], "Count": len(items)})


@app.get(
    "/jit/request",
    operation_id="get_request",
    summary="Get one access request with its policy and provisioning steps",
    description=(
        "Get one request by RequestId: who asked, the account, the duration, the reason, the exact policy, its "
        "PolicyFingerprint, the decision, and every provisioning step. Visible to the requester and to the "
        "Owners and Deputies of the account. Call this before approving, to show the approver the exact policy "
        "and fingerprint, and again afterwards to follow provisioning. Provisioning is complete when Phase is "
        "Active."
    ),
    tags=[TAG],
)
def get_request(
    request_id: Annotated[str, Query(alias="RequestId", min_length=1, max_length=64, description="The request ID.")],
) -> dict:
    caller = _caller()
    request = _load(request_id)
    account = store.get_account(request["AccountId"])
    if not rules.can_view(caller, request, account):
        # Do not reveal that the request exists to people who may not see it.
        raise NotFoundError(f"Request '{request_id}' was not found.")
    return _json(200, _view(request))


@app.post(
    "/jit/approve",
    operation_id="approve_request",
    summary="Approve a pending access request",
    description=(
        "Approve a pending request. First call get_request and show the approver the requester, the account, the "
        "duration, the reason, the complete policy and its PolicyFingerprint. After the approver confirms that "
        "exact policy, pass its fingerprint as ExpectedPolicyFingerprint. The approval is applied only if the "
        "request is still pending and the stored policy still has that fingerprint. Only an Owner or Deputy of "
        "the account can approve, and by default not their own request. Access is then provisioned "
        "automatically. Always check Decided in the response: when false, nothing was approved, so tell the user "
        "the Message exactly as given."
    ),
    tags=[TAG],
)
def approve_request(
    request_id: Annotated[
        str,
        Query(alias="RequestId", min_length=1, max_length=64, description="The request ID to approve."),
    ],
    expected_policy_fingerprint: Annotated[
        str,
        Query(
            alias="ExpectedPolicyFingerprint",
            min_length=1,
            max_length=128,
            description=(
                "The PolicyFingerprint of the policy the approver reviewed, for example sha256:0a1b2c. "
                "Take it from get_request or list_requests, never invent it."
            ),
        ),
    ],
) -> dict:
    settings = Settings.from_env()
    caller = _caller()
    request, account, refusal = _decidable(caller, request_id)
    if refusal:
        return refusal
    if not rules.is_approver(caller, account):
        return _not_decided("NotAnApprover", "Only an Owner or Deputy of this account can approve this request.")
    if same_user(caller, request.get("Requester", "")) and not settings.allow_self_approval:
        return _not_decided(
            "OwnRequest",
            "You cannot approve your own request. Ask another Owner or Deputy of this account to approve it.",
        )
    if store.now() > int(request.get("ApprovalDeadline", 0)):
        return _not_decided(
            "ApprovalWindowPassed",
            "The approval window for this request has passed. Ask the requester to submit a new request.",
        )

    current = policy_fingerprint(request.get("PolicyArn"), request.get("PolicyDocument"))
    if expected_policy_fingerprint != current:
        return _not_decided(
            "PolicyFingerprintMismatch",
            "The policy does not match the fingerprint you reviewed. Get the request again and review the "
            "current policy.",
        )

    updated = store.decide_request(request_id, store.STATUS_APPROVED, caller, expected_fingerprint=current)
    if updated is None:
        return _not_decided(
            "AlreadyDecided", "The request is no longer pending or its policy changed. Get it again and review it."
        )

    logger.info(
        "Access approved",
        extra={
            "request_id": request_id,
            "account_id": request["AccountId"],
            "requester": request.get("Requester"),
            "approver": caller,
            "policy_fingerprint": current,
        },
    )
    return _json(200, {"Decided": True, **_view(updated)})


@app.post(
    "/jit/reject",
    operation_id="reject_request",
    summary="Reject or withdraw a pending access request",
    description=(
        "Reject a pending request. An Owner or Deputy of the account can reject any pending request, and the "
        "requester can withdraw their own. Nothing is provisioned for a rejected request. To change the policy "
        "or the duration, reject the request and ask the requester to submit a new one. Always check Decided in "
        "the response: when false, nothing was rejected, so tell the user the Message exactly as given."
    ),
    tags=[TAG],
)
def reject_request(
    request_id: Annotated[
        str,
        Query(alias="RequestId", min_length=1, max_length=64, description="The request ID to reject."),
    ],
) -> dict:
    caller = _caller()
    request, account, refusal = _decidable(caller, request_id)
    if refusal:
        return refusal
    if not same_user(caller, request.get("Requester", "")) and not rules.is_approver(caller, account):
        return _not_decided(
            "NotAnApprover", "Only an Owner or Deputy of this account, or the requester, can reject this request."
        )

    updated = store.decide_request(request_id, store.STATUS_REJECTED, caller)
    if updated is None:
        return _not_decided("AlreadyDecided", "The request is no longer pending.")

    logger.info(
        "Access rejected",
        extra={"request_id": request_id, "account_id": request["AccountId"], "rejected_by": caller},
    )
    return _json(200, {"Decided": True, **_view(updated)})


# --------------------------------------------------------------------------- entry points


@logger.inject_lambda_context
def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    return app.resolve(event, context)


def openapi_document(server_url: str) -> str:
    """The OpenAPI document of this API as JSON, used to define the agent's tools."""
    return app.get_openapi_json_schema(
        title="Just-in-time access API",
        version=__version__,
        servers=[{"url": server_url}],
        security_schemes={
            "BearerAuth": {
                "type": "apiKey",
                "name": "Authorization",
                "in": "header",
                "description": "Bearer access token issued by Microsoft Entra ID for this API.",
            }
        },
        security=[{"BearerAuth": []}],
    )
