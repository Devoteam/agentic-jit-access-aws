"""The access API, end to end through the router, with in-memory DynamoDB."""

from __future__ import annotations

import json

import pytest

from jit_access.api import app as api
from jit_access.common import store
from jit_access.common.policy import policy_fingerprint

from .conftest import ACCOUNT, OTHER_ACCOUNT, clock, policy_document

pytestmark = pytest.mark.usefixtures("tables", "known_users")

NOW = 1_800_000_000


def call(method: str, path: str, user: str | None, query: dict | None = None, body: dict | None = None):
    """Send a request as the user the authorizer would have verified."""
    authorizer = {"username": user} if user else {}
    event = {
        "resource": path,
        "path": path,
        "httpMethod": method,
        "headers": {"content-type": "application/json"},
        "queryStringParameters": query,
        "multiValueQueryStringParameters": {key: [value] for key, value in query.items()} if query else None,
        "requestContext": {
            "requestId": "test",
            "stage": "prod",
            "httpMethod": method,
            "path": path,
            "resourcePath": path,
            "authorizer": authorizer,
        },
        "body": json.dumps(body) if body is not None else None,
        "isBase64Encoded": False,
    }
    response = api.app.resolve(event, None)
    return response["statusCode"], json.loads(response["body"])


def request_access(user: str = "dave", **overrides):
    body = {
        "AccountId": ACCOUNT,
        "Reason": "Debug incident 4711",
        "DurationHours": 2,
        "PolicyDocument": policy_document(),
    }
    body.update(overrides)
    return call("POST", "/jit/request-access", user, body=body)


def new_request(user: str = "dave", **overrides) -> dict:
    status, body = request_access(user, **overrides)
    assert status == 200, body
    return body


# --------------------------------------------------------------------------- requesting


def test_request_is_stored_as_pending_for_the_signed_in_user(monkeypatch):
    clock(monkeypatch, NOW)
    status, body = request_access("dave")
    assert status == 200
    assert body["Requester"] == "dave"
    assert body["Status"] == "Pending"
    assert body["PolicyFingerprint"] == policy_fingerprint(None, policy_document())
    assert body["PolicyDocument"] == policy_document()
    assert body["ApprovalDeadline"] == NOW + 24 * 3600
    stored = store.get_request(body["RequestId"])
    assert stored["Requester"] == "dave"
    assert "ExpiresAt" in stored and "ExpiresAt" not in body


def test_managed_policy_request_is_accepted():
    body = new_request(PolicyDocument=None, PolicyArn="arn:aws:iam::aws:policy/ReadOnlyAccess")
    assert body["PolicyArn"] == "arn:aws:iam::aws:policy/ReadOnlyAccess"
    assert "PolicyDocument" not in body


def test_nobody_can_request_access_for_someone_else():
    status, body = request_access("dave", UserId="alice")
    assert status == 422
    assert any(item["loc"][-1] == "UserId" and item["type"] == "extra_forbidden" for item in body["detail"])


def test_a_request_without_a_signed_in_user_is_unauthorized():
    status, _ = request_access(user=None)
    assert status == 401


@pytest.mark.parametrize(
    "overrides",
    [
        {"PolicyArn": "arn:aws:iam::aws:policy/ReadOnlyAccess"},  # both policies
        {"PolicyDocument": None},  # neither
        {"PolicyDocument": {"Version": "2012-10-17", "Statement": []}},
        {"PolicyDocument": None, "PolicyArn": "arn:aws:iam::111122223333:policy/Mine"},
        {"AccountId": "123"},
        {"Reason": ""},
        {"DurationHours": 0},
        {"DurationHours": 73},
    ],
)
def test_malformed_requests_are_rejected(overrides):
    status, body = request_access("dave", **overrides)
    assert status == 422, body
    assert store.list_requests_by_account(ACCOUNT) == []


def test_duration_is_limited_by_configuration(monkeypatch):
    monkeypatch.setenv("MAX_DURATION_HOURS", "2")
    assert request_access("dave", DurationHours=2)[0] == 200
    status, body = request_access("dave", DurationHours=3)
    assert status == 200 and body["Submitted"] is False and body["Code"] == "DurationTooLong"
    assert "2 or less" in body["Message"]


def test_managed_policies_can_be_limited_to_an_allow_list(monkeypatch):
    monkeypatch.setenv("ALLOWED_MANAGED_POLICY_ARNS", "arn:aws:iam::aws:policy/ReadOnlyAccess")
    allowed = request_access("dave", PolicyDocument=None, PolicyArn="arn:aws:iam::aws:policy/ReadOnlyAccess")
    denied = request_access("dave", PolicyDocument=None, PolicyArn="arn:aws:iam::aws:policy/AdministratorAccess")
    assert allowed[0] == 200
    assert denied[1]["Submitted"] is False and denied[1]["Code"] == "PolicyNotAllowed"


def test_unregistered_accounts_cannot_be_requested():
    status, body = request_access("dave", AccountId="999999999999")
    assert status == 200 and body["Submitted"] is False and body["Code"] == "AccountNotRegistered"
    assert "RequestId" not in body
    assert store.list_requests_by_account("999999999999") == []


def test_people_outside_the_organisation_get_a_clear_answer():
    status, body = request_access("mallory")
    assert status == 200 and body["Submitted"] is False and body["Code"] == "UserNotInOrganisation"
    assert "not registered as a member of the organisation" in body["Message"]
    assert store.list_requests_by_account(ACCOUNT) == []


def test_outsiders_learn_nothing_about_accounts():
    # Same answer whether or not the account is registered.
    registered = request_access("mallory")[1]
    unregistered = request_access("mallory", AccountId="999999999999")[1]
    assert registered["Code"] == unregistered["Code"] == "UserNotInOrganisation"


def test_accepted_requests_say_they_were_submitted():
    assert new_request()["Submitted"] is True


# --------------------------------------------------------------------------- reading


def test_requester_and_approvers_can_read_a_request_but_strangers_cannot():
    request_id = new_request("dave")["RequestId"]
    assert call("GET", "/jit/request", "dave", {"RequestId": request_id})[0] == 200
    assert call("GET", "/jit/request", "alice", {"RequestId": request_id})[0] == 200  # owner
    assert call("GET", "/jit/request", "bob", {"RequestId": request_id})[0] == 200  # deputy
    assert call("GET", "/jit/request", "carol", {"RequestId": request_id})[0] == 404  # owner of another account
    assert call("GET", "/jit/request", "dave", {"RequestId": "missing"})[0] == 404


def test_internal_fields_are_not_returned():
    request_id = new_request("dave")["RequestId"]
    store.set_attributes(
        request_id, PermissionSetArn="arn:aws:sso:::permissionSet/x/y", PrincipalId="uid", Phase="Active"
    )
    _, body = call("GET", "/jit/request", "dave", {"RequestId": request_id})
    assert body["Phase"] == "Active"
    for hidden in ("PermissionSetArn", "PrincipalId", "ExpiresAt"):
        assert hidden not in body


def test_only_approvers_can_list_requests_for_an_account():
    new_request("dave")
    status, body = call("GET", "/jit/requests", "alice", {"AccountId": ACCOUNT})
    assert status == 200 and body["Count"] == 1
    assert call("GET", "/jit/requests", "bob", {"AccountId": ACCOUNT})[0] == 200
    assert call("GET", "/jit/requests", "dave", {"AccountId": ACCOUNT})[0] == 403
    assert call("GET", "/jit/requests", "carol", {"AccountId": ACCOUNT})[0] == 403
    assert call("GET", "/jit/requests", "alice", {"AccountId": "999999999999"})[0] == 404


def test_listing_filters_by_status_and_lists_newest_first(monkeypatch):
    clock(monkeypatch, NOW)
    first = new_request("dave")["RequestId"]
    clock(monkeypatch, NOW + 60)
    second = new_request("dave")["RequestId"]
    store.decide_request(first, store.STATUS_REJECTED, "alice")

    _, everything = call("GET", "/jit/requests", "alice", {"AccountId": ACCOUNT})
    assert [item["RequestId"] for item in everything["Items"]] == [second, first]

    _, pending = call("GET", "/jit/requests", "alice", {"AccountId": ACCOUNT, "Status": "Pending"})
    assert [item["RequestId"] for item in pending["Items"]] == [second]
    assert call("GET", "/jit/requests", "alice", {"AccountId": ACCOUNT, "Status": "Bogus"})[0] == 400


# --------------------------------------------------------------------------- approving


def approve(request_id: str, user: str, fingerprint: str | None):
    query = {"RequestId": request_id}
    if fingerprint is not None:
        query["ExpectedPolicyFingerprint"] = fingerprint
    return call("POST", "/jit/approve", user, query)


def test_owner_approves_the_exact_policy_version():
    created = new_request("dave")
    status, body = approve(created["RequestId"], "alice", created["PolicyFingerprint"])
    assert status == 200, body
    assert body["Status"] == "Approved"
    assert body["DecidedBy"] == "alice"
    assert body["PolicyFingerprint"] == created["PolicyFingerprint"]
    assert store.get_request(created["RequestId"])["Status"] == "Approved"


def test_deputy_can_approve_too():
    created = new_request("dave")
    assert approve(created["RequestId"], "bob", created["PolicyFingerprint"])[1]["Decided"] is True


def test_approval_needs_the_fingerprint():
    created = new_request("dave")
    assert approve(created["RequestId"], "alice", None)[0] == 422


def test_approval_with_another_fingerprint_is_rejected_and_changes_nothing():
    created = new_request("dave")
    wrong = policy_fingerprint(None, policy_document(action="s3:*"))
    status, body = approve(created["RequestId"], "alice", wrong)
    assert body["Decided"] is False and body["Code"] == "PolicyFingerprintMismatch"
    assert store.get_request(created["RequestId"])["Status"] == "Pending"


def test_approval_is_checked_against_the_stored_policy_not_the_stored_fingerprint():
    """If the stored policy is changed behind the API's back, the old fingerprint no longer matches."""
    created = new_request("dave")
    store.set_attributes(created["RequestId"], PolicyDocument=policy_document(action="s3:*", resource="*"))
    status, body = approve(created["RequestId"], "alice", created["PolicyFingerprint"])
    assert body["Decided"] is False and body["Code"] == "PolicyFingerprintMismatch"
    assert store.get_request(created["RequestId"])["Status"] == "Pending"


def test_people_who_do_not_own_the_account_cannot_approve():
    created = new_request("dave")
    for user in ("dave", "carol", "mallory"):
        _, body = approve(created["RequestId"], user, created["PolicyFingerprint"])
        assert body["Decided"] is False and body["Code"] in {"NotAnApprover", "RequestNotFound"}, user
    assert store.get_request(created["RequestId"])["Status"] == "Pending"


def test_owners_cannot_approve_their_own_request_by_default():
    created = new_request("alice")
    status, body = approve(created["RequestId"], "alice", created["PolicyFingerprint"])
    assert body["Decided"] is False and body["Code"] == "OwnRequest"
    assert (
        approve(created["RequestId"], "bob", created["PolicyFingerprint"])[1]["Decided"] is True
    )  # a second approver can


def test_self_approval_can_be_enabled_for_small_setups(monkeypatch):
    monkeypatch.setenv("ALLOW_SELF_APPROVAL", "true")
    created = new_request("alice")
    assert approve(created["RequestId"], "alice", created["PolicyFingerprint"])[1]["Decided"] is True


def test_a_request_can_only_be_decided_once():
    created = new_request("dave")
    assert approve(created["RequestId"], "alice", created["PolicyFingerprint"])[1]["Decided"] is True
    status, body = approve(created["RequestId"], "bob", created["PolicyFingerprint"])
    assert body["Code"] == "AlreadyDecided" and "already Approved" in body["Message"]


def test_a_request_cannot_be_approved_after_the_approval_window(monkeypatch):
    clock(monkeypatch, NOW)
    created = new_request("dave")
    clock(monkeypatch, NOW + 24 * 3600 + 1)
    status, body = approve(created["RequestId"], "alice", created["PolicyFingerprint"])
    assert body["Decided"] is False and body["Code"] == "ApprovalWindowPassed"
    assert store.get_request(created["RequestId"])["Status"] == "Pending"


def test_unknown_requests_cannot_be_approved():
    assert approve("missing", "alice", "sha256:abc")[1]["Code"] == "RequestNotFound"


# --------------------------------------------------------------------------- rejecting


def test_owner_rejects_a_request():
    created = new_request("dave")
    status, body = call("POST", "/jit/reject", "alice", {"RequestId": created["RequestId"]})
    assert status == 200 and body["Status"] == "Rejected" and body["DecidedBy"] == "alice"


def test_requester_can_withdraw_their_own_request():
    created = new_request("dave")
    status, body = call("POST", "/jit/reject", "dave", {"RequestId": created["RequestId"]})
    assert status == 200 and body["Status"] == "Rejected"


def test_strangers_cannot_reject():
    created = new_request("dave")
    assert call("POST", "/jit/reject", "carol", {"RequestId": created["RequestId"]})[1]["Decided"] is False
    assert store.get_request(created["RequestId"])["Status"] == "Pending"


def test_a_rejected_request_cannot_be_approved():
    created = new_request("dave")
    call("POST", "/jit/reject", "alice", {"RequestId": created["RequestId"]})
    assert approve(created["RequestId"], "bob", created["PolicyFingerprint"])[1]["Code"] == "AlreadyDecided"


def test_concurrent_decisions_have_one_winner():
    """The conditional write lets exactly one of two racing decisions through."""
    created = new_request("dave")
    first = store.decide_request(created["RequestId"], store.STATUS_APPROVED, "alice", created["PolicyFingerprint"])
    second = store.decide_request(created["RequestId"], store.STATUS_REJECTED, "bob")
    assert first is not None and second is None
    assert store.get_request(created["RequestId"])["Status"] == "Approved"


def test_other_accounts_have_their_own_approvers():
    other = new_request("dave", AccountId=OTHER_ACCOUNT)
    assert approve(other["RequestId"], "alice", other["PolicyFingerprint"])[1]["Decided"] is False
    assert approve(other["RequestId"], "carol", other["PolicyFingerprint"])[1]["Decided"] is True


def test_unexpected_errors_do_not_leak_details(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(store, "get_account", boom)
    status, body = request_access("dave")
    assert status == 500
    assert body["message"] == "Internal server error"
    assert "secret" not in json.dumps(body)
