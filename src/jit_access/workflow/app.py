"""Tasks of the access workflow (AWS Step Functions).

The state machine calls this function with an ``Action``. Each action is small
and idempotent, so Step Functions can retry it safely:

* ``provision``       create a temporary permission set, attach the approved
                      policy and start assigning it to the requester
* ``check_provision`` report whether the assignment finished
* ``activate``        record when access started and when it ends
* ``revoke``          start removing the assignment
* ``check_revoke``    report whether it is removed, then delete the permission set
* ``finalize``        record the outcome

Every resource is saved to the request record as soon as it is created. If a
step fails halfway, ``revoke`` knows exactly what to remove, so a failure never
leaves access or permission sets behind.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
from botocore.config import Config
from botocore.exceptions import ClientError

from jit_access.common import identity, store
from jit_access.common.config import Settings

logger = Logger(service="jit-access-workflow")


class RetryLater(Exception):
    """A transient condition. The state machine retries the task with a backoff."""


class ProvisioningFailed(Exception):
    """Access could not be granted. The workflow cleans up and records the failure."""


class RevocationFailed(Exception):
    """Access could not be removed. The workflow fails so an alarm can raise it."""


@lru_cache(maxsize=1)
def _sso():
    """Identity Center client. Uses IDENTITY_CENTER_ROLE_ARN when Identity Center is in another account."""
    config = Config(retries={"max_attempts": 5, "mode": "standard"})
    role_arn = Settings.from_env().identity_center_role_arn
    if not role_arn:
        return boto3.client("sso-admin", config=config)
    credentials = boto3.client("sts").assume_role(RoleArn=role_arn, RoleSessionName="jit-access-workflow")[
        "Credentials"
    ]
    return boto3.client(
        "sso-admin",
        config=config,
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
    )


def reset_clients() -> None:
    _sso.cache_clear()
    identity.reset_clients()


def _code(error: ClientError) -> str:
    return error.response.get("Error", {}).get("Code", "")


def _load(request_id: str) -> dict[str, Any]:
    request = store.get_request(request_id)
    if not request:
        raise ProvisioningFailed(f"Request '{request_id}' was not found.")
    return request


def permission_set_name(request_id: str) -> str:
    """A short, unique name. It is the role name the user sees in the AWS access portal."""
    return "jit_" + request_id.replace("-", "")[:12]


def _find_permission_set(instance_arn: str, name: str) -> str | None:
    """Find a permission set by name. Only needed to recover after a crash."""
    paginator = _sso().get_paginator("list_permission_sets")
    for page in paginator.paginate(InstanceArn=instance_arn):
        for arn in page["PermissionSets"]:
            described = _sso().describe_permission_set(InstanceArn=instance_arn, PermissionSetArn=arn)
            if described["PermissionSet"]["Name"] == name:
                return arn
    return None


def _create_permission_set(settings: Settings, request_id: str, name: str) -> str:
    try:
        response = _sso().create_permission_set(
            InstanceArn=settings.instance_arn,
            Name=name,
            Description=f"Just-in-time access {request_id}",
            SessionDuration=settings.session_duration,
        )
        return response["PermissionSet"]["PermissionSetArn"]
    except ClientError as error:
        if _code(error) == "ConflictException":
            existing = _find_permission_set(settings.instance_arn, name)
            if existing:
                return existing
        raise


def _attach_policy(settings: Settings, request: dict[str, Any], permission_set_arn: str) -> None:
    if request.get("PolicyArn"):
        try:
            _sso().attach_managed_policy_to_permission_set(
                InstanceArn=settings.instance_arn,
                PermissionSetArn=permission_set_arn,
                ManagedPolicyArn=request["PolicyArn"],
            )
        except ClientError as error:
            if _code(error) != "ConflictException":  # already attached
                raise
    else:
        _sso().put_inline_policy_to_permission_set(
            InstanceArn=settings.instance_arn,
            PermissionSetArn=permission_set_arn,
            InlinePolicy=json.dumps(store.to_public(request["PolicyDocument"])),
        )


def _assignment_exists(settings: Settings, request: dict[str, Any]) -> bool:
    paginator = _sso().get_paginator("list_account_assignments")
    pages = paginator.paginate(
        InstanceArn=settings.instance_arn,
        AccountId=request["AccountId"],
        PermissionSetArn=request["PermissionSetArn"],
    )
    return any(
        assignment.get("PrincipalId") == request.get("PrincipalId")
        for page in pages
        for assignment in page["AccountAssignments"]
    )


# --------------------------------------------------------------------------- actions


def provision(request_id: str) -> dict[str, Any]:
    settings = Settings.from_env()
    request = _load(request_id)
    # Never provision a request that was not approved, whatever started this task.
    if request.get("Status") != store.STATUS_APPROVED:
        raise ProvisioningFailed("The request is not approved.")

    arn = request.get("PermissionSetArn")
    if not arn:
        name = permission_set_name(request_id)
        arn = _create_permission_set(settings, request_id, name)
        store.set_attributes(request_id, PermissionSetArn=arn, PermissionSetName=name, Phase=store.PHASE_PROVISIONING)
        store.append_step(request_id, "CreatePermissionSet", "Succeeded", name)
        request["PermissionSetArn"] = arn

    _attach_policy(settings, request, arn)
    store.append_step(request_id, "AttachPolicy", "Succeeded")

    user_id = request.get("PrincipalId") or identity.find_user_id(request["Requester"])
    if not user_id:
        raise ProvisioningFailed(f"User '{request['Requester']}' was not found in AWS IAM Identity Center.")
    store.set_attributes(request_id, PrincipalId=user_id)

    creation_id = None
    try:
        response = _sso().create_account_assignment(
            InstanceArn=settings.instance_arn,
            TargetId=request["AccountId"],
            TargetType="AWS_ACCOUNT",
            PermissionSetArn=arn,
            PrincipalType="USER",
            PrincipalId=user_id,
        )
        creation_id = response["AccountAssignmentCreationStatus"]["RequestId"]
    except ClientError as error:
        # Already assigned or being assigned (a retry): check_provision will confirm.
        if _code(error) != "ConflictException":
            raise
    store.set_attributes(request_id, AssignmentRequested=True, AssignmentCreationId=creation_id or "")
    store.append_step(request_id, "CreateAccountAssignment", "InProgress")
    return {"RequestId": request_id}


def check_provision(request_id: str) -> dict[str, Any]:
    settings = Settings.from_env()
    request = _load(request_id)
    creation_id = request.get("AssignmentCreationId")

    reason = ""
    if creation_id:
        response = _sso().describe_account_assignment_creation_status(
            InstanceArn=settings.instance_arn,
            AccountAssignmentCreationRequestId=creation_id,
        )
        status = response["AccountAssignmentCreationStatus"]["Status"]
        reason = response["AccountAssignmentCreationStatus"].get("FailureReason", "")
    else:
        status = "SUCCEEDED" if _assignment_exists(settings, request) else "FAILED"
        reason = "" if status == "SUCCEEDED" else "The assignment does not exist."

    if status == "SUCCEEDED":
        store.append_step(request_id, "CreateAccountAssignment", "Succeeded")
    elif status == "FAILED":
        store.append_step(request_id, "CreateAccountAssignment", "Failed", reason)
    return {"Status": status, "Reason": reason}


def activate(request_id: str) -> dict[str, Any]:
    request = _load(request_id)
    started = store.now()
    seconds = int(request["DurationHours"]) * 3600
    store.set_attributes(
        request_id,
        Phase=store.PHASE_ACTIVE,
        AccessStartedAt=started,
        AccessExpiresAt=started + seconds,
    )
    store.append_step(request_id, "AccessActive", "Succeeded")
    logger.info("Access active", extra={"request_id": request_id, "account_id": request["AccountId"]})
    return {"WaitSeconds": seconds}


def revoke(request_id: str) -> dict[str, Any]:
    settings = Settings.from_env()
    request = _load(request_id)
    store.set_attributes(request_id, Phase=store.PHASE_REVOKING)

    needs_deletion = (
        request.get("AssignmentRequested")
        and request.get("PrincipalId")
        and request.get("PermissionSetArn")
        and not request.get("AssignmentDeletionId")
        and not request.get("AssignmentDeleted")
    )
    if needs_deletion:
        try:
            response = _sso().delete_account_assignment(
                InstanceArn=settings.instance_arn,
                TargetId=request["AccountId"],
                TargetType="AWS_ACCOUNT",
                PermissionSetArn=request["PermissionSetArn"],
                PrincipalType="USER",
                PrincipalId=request["PrincipalId"],
            )
            store.set_attributes(
                request_id,
                AssignmentDeletionId=response["AccountAssignmentDeletionStatus"]["RequestId"],
            )
            store.append_step(request_id, "DeleteAccountAssignment", "InProgress")
        except ClientError as error:
            code = _code(error)
            if code == "ResourceNotFoundException":
                store.set_attributes(request_id, AssignmentDeleted=True)  # nothing to remove
            elif code in {"ConflictException", "ThrottlingException"}:
                raise RetryLater(f"Could not start removal yet: {code}") from error
            else:
                raise
    return {"Status": "IN_PROGRESS"}


def check_revoke(request_id: str) -> dict[str, Any]:
    settings = Settings.from_env()
    request = _load(request_id)

    deletion_id = request.get("AssignmentDeletionId")
    if deletion_id and not request.get("AssignmentDeleted"):
        response = _sso().describe_account_assignment_deletion_status(
            InstanceArn=settings.instance_arn,
            AccountAssignmentDeletionRequestId=deletion_id,
        )
        status = response["AccountAssignmentDeletionStatus"]
        if status["Status"] == "IN_PROGRESS":
            return {"Status": "IN_PROGRESS"}
        if status["Status"] == "FAILED":
            store.append_step(request_id, "DeleteAccountAssignment", "Failed", status.get("FailureReason", ""))
            raise RevocationFailed(status.get("FailureReason", "Removing the assignment failed."))
        store.set_attributes(request_id, AssignmentDeleted=True)
        store.append_step(request_id, "DeleteAccountAssignment", "Succeeded")

    arn = request.get("PermissionSetArn")
    if arn and not request.get("PermissionSetDeleted"):
        try:
            _sso().delete_permission_set(InstanceArn=settings.instance_arn, PermissionSetArn=arn)
        except ClientError as error:
            code = _code(error)
            if code in {"ConflictException", "ThrottlingException"}:
                raise RetryLater(f"Could not delete the permission set yet: {code}") from error
            if code != "ResourceNotFoundException":
                raise
        store.set_attributes(request_id, PermissionSetDeleted=True)
        store.append_step(request_id, "DeletePermissionSet", "Succeeded")
    return {"Status": "DONE"}


def finalize(request_id: str, outcome: str, reason: str = "") -> dict[str, Any]:
    phase = store.PHASE_EXPIRED if outcome == "Expired" else store.PHASE_FAILED
    store.set_attributes(request_id, Phase=phase)
    if reason:
        store.set_attributes(request_id, FailureReason=reason[:500])
    store.append_step(request_id, "Finalize", outcome, reason)
    logger.info("Access workflow finished", extra={"request_id": request_id, "outcome": outcome})
    return {"Outcome": outcome}


# --------------------------------------------------------------------------- entry point


@logger.inject_lambda_context
def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    action = event.get("Action")
    request_id = event["RequestId"]
    if Settings.from_env().identity_center_role_arn:
        # Assumed-role credentials expire after an hour. Get fresh ones for every task.
        _sso.cache_clear()
    if action == "provision":
        return provision(request_id)
    if action == "check_provision":
        return check_provision(request_id)
    if action == "activate":
        return activate(request_id)
    if action == "revoke":
        return revoke(request_id)
    if action == "check_revoke":
        return check_revoke(request_id)
    if action == "finalize":
        return finalize(request_id, event["Outcome"], event.get("Reason", ""))
    raise ValueError(f"Unknown action '{action}'.")
