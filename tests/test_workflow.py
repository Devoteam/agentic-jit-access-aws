"""The access workflow tasks, against a fake IAM Identity Center and in-memory DynamoDB."""

from __future__ import annotations

import json

import pytest
from botocore.exceptions import ClientError

from jit_access.common import store
from jit_access.workflow import app as workflow

from .conftest import ACCOUNT, clock, policy_document

pytestmark = pytest.mark.usefixtures("tables", "known_users")

REQUEST_ID = "0a1b2c3d-1111-2222-3333-444455556666"
PERMISSION_SET = "arn:aws:sso:::permissionSet/ssoins-1234567890abcdef/ps-1"
NOW = 1_800_000_000


def error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "operation")


class Pages:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **kwargs):
        return iter(self._pages)


class FakeSso:
    """Records every call and fails on demand."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, ClientError] = {}
        self.creation_status = ["SUCCEEDED"]
        self.deletion_status = ["SUCCEEDED"]
        self.existing_permission_sets: dict[str, str] = {}
        self.assignments: list[dict] = []

    def _record(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, kwargs))
        if name in self.fail:
            raise self.fail[name]

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def create_permission_set(self, **kwargs):
        self._record("create_permission_set", kwargs)
        return {"PermissionSet": {"PermissionSetArn": PERMISSION_SET, "Name": kwargs["Name"]}}

    def describe_permission_set(self, **kwargs):
        self._record("describe_permission_set", kwargs)
        arn = kwargs["PermissionSetArn"]
        name = next(n for n, a in self.existing_permission_sets.items() if a == arn)
        return {"PermissionSet": {"Name": name}}

    def get_paginator(self, name):
        if name == "list_permission_sets":
            return Pages([{"PermissionSets": list(self.existing_permission_sets.values())}])
        return Pages([{"AccountAssignments": self.assignments}])

    def attach_managed_policy_to_permission_set(self, **kwargs):
        self._record("attach_managed_policy_to_permission_set", kwargs)

    def put_inline_policy_to_permission_set(self, **kwargs):
        self._record("put_inline_policy_to_permission_set", kwargs)

    def create_account_assignment(self, **kwargs):
        self._record("create_account_assignment", kwargs)
        return {"AccountAssignmentCreationStatus": {"Status": "IN_PROGRESS", "RequestId": "create-1"}}

    def describe_account_assignment_creation_status(self, **kwargs):
        self._record("describe_account_assignment_creation_status", kwargs)
        status = self.creation_status.pop(0) if len(self.creation_status) > 1 else self.creation_status[0]
        result = {"Status": status}
        if status == "FAILED":
            result["FailureReason"] = "Account is not part of the organization"
        return {"AccountAssignmentCreationStatus": result}

    def delete_account_assignment(self, **kwargs):
        self._record("delete_account_assignment", kwargs)
        return {"AccountAssignmentDeletionStatus": {"Status": "IN_PROGRESS", "RequestId": "delete-1"}}

    def describe_account_assignment_deletion_status(self, **kwargs):
        self._record("describe_account_assignment_deletion_status", kwargs)
        status = self.deletion_status.pop(0) if len(self.deletion_status) > 1 else self.deletion_status[0]
        result = {"Status": status}
        if status == "FAILED":
            result["FailureReason"] = "Internal failure"
        return {"AccountAssignmentDeletionStatus": result}

    def delete_permission_set(self, **kwargs):
        self._record("delete_permission_set", kwargs)


@pytest.fixture
def sso(monkeypatch):
    fake = FakeSso()
    workflow.reset_clients()
    monkeypatch.setattr(workflow, "_sso", lambda: fake)
    return fake


def stored_request(status: str = "Approved", **overrides) -> str:
    item = {
        "RequestId": REQUEST_ID,
        "AccountId": ACCOUNT,
        "Requester": "dave",
        "Reason": "Debug incident 4711",
        "DurationHours": 2,
        "Status": status,
        "PolicyDocument": policy_document(),
        "CreatedAt": NOW,
    }
    item.update(overrides)
    store.put_request(item)
    return REQUEST_ID


def current() -> dict:
    return store.get_request(REQUEST_ID)


def step_names() -> list[str]:
    return [f"{step['Step']}:{step['Status']}" for step in current().get("Steps", [])]


# --------------------------------------------------------------------------- the whole life of a grant


def test_full_lifecycle_grants_then_removes_access(sso, monkeypatch):
    clock(monkeypatch, NOW)
    request_id = stored_request()
    sso.creation_status = ["IN_PROGRESS", "SUCCEEDED"]
    sso.deletion_status = ["IN_PROGRESS", "SUCCEEDED"]

    workflow.provision(request_id)
    assert workflow.check_provision(request_id)["Status"] == "IN_PROGRESS"
    assert workflow.check_provision(request_id)["Status"] == "SUCCEEDED"
    assert workflow.activate(request_id) == {"WaitSeconds": 2 * 3600}
    workflow.revoke(request_id)
    assert workflow.check_revoke(request_id)["Status"] == "IN_PROGRESS"
    assert workflow.check_revoke(request_id)["Status"] == "DONE"
    workflow.finalize(request_id, "Expired")

    item = current()
    assert item["Phase"] == "Expired"
    assert item["PermissionSetName"] == "jit_0a1b2c3d1111"
    assert int(item["AccessStartedAt"]) == NOW
    assert int(item["AccessExpiresAt"]) == NOW + 2 * 3600
    assert step_names() == [
        "CreatePermissionSet:Succeeded",
        "AttachPolicy:Succeeded",
        "CreateAccountAssignment:InProgress",
        "CreateAccountAssignment:Succeeded",
        "AccessActive:Succeeded",
        "DeleteAccountAssignment:InProgress",
        "DeleteAccountAssignment:Succeeded",
        "DeletePermissionSet:Succeeded",
        "Finalize:Expired",
    ]


def test_the_permission_set_carries_the_approved_policy_and_the_assignment_targets_the_user(sso):
    request_id = stored_request()
    workflow.provision(request_id)

    created = dict(sso.calls)["create_permission_set"]
    assert created["Name"] == "jit_0a1b2c3d1111"
    assert created["SessionDuration"] == "PT1H"

    inline = dict(sso.calls)["put_inline_policy_to_permission_set"]
    assert json.loads(inline["InlinePolicy"]) == policy_document()

    assignment = dict(sso.calls)["create_account_assignment"]
    assert assignment["TargetId"] == ACCOUNT
    assert assignment["TargetType"] == "AWS_ACCOUNT"
    assert assignment["PrincipalType"] == "USER"
    assert assignment["PrincipalId"] == "uid-dave"
    assert assignment["PermissionSetArn"] == PERMISSION_SET


def test_managed_policy_requests_attach_the_managed_policy(sso):
    request_id = stored_request(PolicyDocument=None, PolicyArn="arn:aws:iam::aws:policy/ReadOnlyAccess")
    workflow.provision(request_id)
    attached = dict(sso.calls)["attach_managed_policy_to_permission_set"]
    assert attached["ManagedPolicyArn"] == "arn:aws:iam::aws:policy/ReadOnlyAccess"
    assert "put_inline_policy_to_permission_set" not in sso.names()


def test_removal_deletes_the_assignment_before_the_permission_set(sso):
    request_id = stored_request()
    workflow.provision(request_id)
    workflow.check_provision(request_id)
    workflow.revoke(request_id)
    workflow.check_revoke(request_id)
    names = sso.names()
    assert names.index("delete_account_assignment") < names.index("delete_permission_set")
    assert names.index("describe_account_assignment_deletion_status") < names.index("delete_permission_set")


# --------------------------------------------------------------------------- safety


def test_a_request_that_is_not_approved_is_never_provisioned(sso):
    request_id = stored_request(status="Pending")
    with pytest.raises(workflow.ProvisioningFailed, match="not approved"):
        workflow.provision(request_id)
    assert sso.calls == []


def test_a_rejected_request_is_never_provisioned(sso):
    request_id = stored_request(status="Rejected")
    with pytest.raises(workflow.ProvisioningFailed):
        workflow.provision(request_id)
    assert sso.calls == []


# --------------------------------------------------------------------------- failure and cleanup


def test_a_user_missing_from_identity_center_fails_and_cleanup_removes_the_permission_set(sso, monkeypatch):
    request_id = stored_request(Requester="ghost")
    with pytest.raises(workflow.ProvisioningFailed, match="ghost"):
        workflow.provision(request_id)
    assert "create_account_assignment" not in sso.names()
    assert current()["PermissionSetArn"] == PERMISSION_SET  # saved, so cleanup can find it

    workflow.revoke(request_id)
    assert "delete_account_assignment" not in sso.names()  # there is no assignment to remove
    assert workflow.check_revoke(request_id)["Status"] == "DONE"
    assert "delete_permission_set" in sso.names()


def test_a_failed_assignment_is_reported_and_cleaned_up(sso):
    request_id = stored_request()
    sso.creation_status = ["FAILED"]
    sso.deletion_status = ["SUCCEEDED"]
    workflow.provision(request_id)
    result = workflow.check_provision(request_id)
    assert result == {"Status": "FAILED", "Reason": "Account is not part of the organization"}

    workflow.revoke(request_id)
    assert workflow.check_revoke(request_id)["Status"] == "DONE"
    workflow.finalize(request_id, "Failed", result["Reason"])
    item = current()
    assert item["Phase"] == "Failed"
    assert "Account is not part of the organization" in item["FailureReason"]
    assert item["PermissionSetDeleted"] is True


def test_a_crash_before_the_permission_set_was_saved_is_recovered(sso):
    request_id = stored_request()
    sso.fail["create_permission_set"] = error("ConflictException")
    sso.existing_permission_sets = {"jit_0a1b2c3d1111": PERMISSION_SET}
    workflow.provision(request_id)  # finds the permission set that already exists
    assert current()["PermissionSetArn"] == PERMISSION_SET


def test_provisioning_can_be_retried_without_creating_a_second_permission_set(sso):
    request_id = stored_request()
    workflow.provision(request_id)
    workflow.provision(request_id)
    assert sso.names().count("create_permission_set") == 1


def test_an_assignment_that_already_exists_is_confirmed_by_listing(sso):
    request_id = stored_request()
    sso.fail["create_account_assignment"] = error("ConflictException")
    sso.assignments = [{"PrincipalId": "uid-dave", "PrincipalType": "USER"}]
    workflow.provision(request_id)
    assert workflow.check_provision(request_id)["Status"] == "SUCCEEDED"
    assert "describe_account_assignment_creation_status" not in sso.names()


def test_revoking_twice_does_not_delete_twice(sso):
    request_id = stored_request()
    workflow.provision(request_id)
    workflow.revoke(request_id)
    workflow.revoke(request_id)
    assert sso.names().count("delete_account_assignment") == 1


def test_removal_is_retried_while_the_assignment_is_still_being_created(sso):
    request_id = stored_request()
    workflow.provision(request_id)
    sso.fail["delete_account_assignment"] = error("ConflictException")
    with pytest.raises(workflow.RetryLater):
        workflow.revoke(request_id)
    del sso.fail["delete_account_assignment"]
    workflow.revoke(request_id)  # the retry succeeds
    assert current()["AssignmentDeletionId"] == "delete-1"


def test_an_assignment_that_is_already_gone_needs_no_removal(sso):
    request_id = stored_request()
    workflow.provision(request_id)
    sso.fail["delete_account_assignment"] = error("ResourceNotFoundException")
    workflow.revoke(request_id)
    assert workflow.check_revoke(request_id)["Status"] == "DONE"
    assert "describe_account_assignment_deletion_status" not in sso.names()


def test_a_failed_removal_is_not_swallowed(sso):
    request_id = stored_request()
    sso.deletion_status = ["FAILED"]
    workflow.provision(request_id)
    workflow.revoke(request_id)
    with pytest.raises(workflow.RevocationFailed, match="Internal failure"):
        workflow.check_revoke(request_id)
    assert "delete_permission_set" not in sso.names()  # never delete while the assignment may remain


def test_the_permission_set_deletion_is_retried_when_it_is_still_in_use(sso):
    request_id = stored_request()
    workflow.provision(request_id)
    workflow.revoke(request_id)
    sso.fail["delete_permission_set"] = error("ConflictException")
    with pytest.raises(workflow.RetryLater):
        workflow.check_revoke(request_id)
    del sso.fail["delete_permission_set"]
    assert workflow.check_revoke(request_id)["Status"] == "DONE"
    assert current()["PermissionSetDeleted"] is True


def test_a_permission_set_that_is_already_deleted_is_not_an_error(sso):
    request_id = stored_request()
    workflow.provision(request_id)
    workflow.revoke(request_id)
    sso.fail["delete_permission_set"] = error("ResourceNotFoundException")
    assert workflow.check_revoke(request_id)["Status"] == "DONE"


# --------------------------------------------------------------------------- entry point


def test_the_handler_dispatches_actions(sso):
    request_id = stored_request()
    assert workflow.lambda_handler.__wrapped__({"Action": "provision", "RequestId": request_id}, None) == {
        "RequestId": request_id
    }
    assert (
        workflow.lambda_handler.__wrapped__({"Action": "check_provision", "RequestId": request_id}, None)["Status"]
        == "SUCCEEDED"
    )
    out = workflow.lambda_handler.__wrapped__(
        {"Action": "finalize", "RequestId": request_id, "Outcome": "Expired"}, None
    )
    assert out == {"Outcome": "Expired"}


def test_the_handler_rejects_unknown_actions(sso):
    with pytest.raises(ValueError, match="Unknown action"):
        workflow.lambda_handler.__wrapped__({"Action": "explode", "RequestId": "x"}, None)


def test_identity_center_calls_use_the_configured_role(monkeypatch):
    """Identity Center in another account: the sso-admin client runs on the assumed role's credentials."""
    role = "arn:aws:iam::999988887777:role/DelegatedAdministration"
    monkeypatch.setenv("IDENTITY_CENTER_ROLE_ARN", role)
    calls = []

    class FakeSts:
        def assume_role(self, **kwargs):
            calls.append(("assume_role", kwargs["RoleArn"]))
            return {"Credentials": {"AccessKeyId": "AKIDASSUMED", "SecretAccessKey": "s", "SessionToken": "t"}}

    def fake_client(service, **kwargs):
        if service == "sts":
            return FakeSts()
        calls.append((service, kwargs.get("aws_access_key_id")))
        return object()

    monkeypatch.setattr(workflow.boto3, "client", fake_client)
    workflow.reset_clients()
    workflow._sso()
    assert calls == [("assume_role", role), ("sso-admin", "AKIDASSUMED")]
    workflow.reset_clients()
