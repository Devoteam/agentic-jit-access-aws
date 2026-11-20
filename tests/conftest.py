"""Shared fixtures: environment, in-memory DynamoDB tables, and a fake identity store."""

from __future__ import annotations

import boto3
import pytest
from moto import mock_aws

from jit_access.common import identity, store

ACCOUNT = "111122223333"
OTHER_ACCOUNT = "444455556666"


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    for key, value in {
        "AWS_ACCESS_KEY_ID": "testing",
        "AWS_SECRET_ACCESS_KEY": "testing",
        "AWS_SESSION_TOKEN": "testing",
        "AWS_DEFAULT_REGION": "eu-central-1",
        "REQUESTS_TABLE": "jit-requests",
        "ACCOUNTS_TABLE": "jit-accounts",
        "IDENTITY_STORE_ID": "d-1234567890",
        "IDENTITY_CENTER_INSTANCE_ARN": "arn:aws:sso:::instance/ssoins-1234567890abcdef",
        "STATE_MACHINE_ARN": "arn:aws:states:eu-central-1:111122223333:stateMachine:jit",
        "MAX_DURATION_HOURS": "8",
        "APPROVAL_WINDOW_HOURS": "24",
        "STRIP_USERNAME_DOMAINS": "contoso.onmicrosoft.com",
    }.items():
        monkeypatch.setenv(key, value)
    for key in ("ALLOW_SELF_APPROVAL", "ALLOWED_MANAGED_POLICY_ARNS"):
        monkeypatch.delenv(key, raising=False)
    store.reset_clients()
    identity.reset_clients()
    yield
    store.reset_clients()
    identity.reset_clients()


@pytest.fixture
def tables():
    """In-memory DynamoDB with one registered account (owner alice, deputy bob)."""
    with mock_aws():
        db = boto3.resource("dynamodb", region_name="eu-central-1")
        db.create_table(
            TableName="jit-requests",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "RequestId", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "RequestId", "AttributeType": "S"},
                {"AttributeName": "AccountId", "AttributeType": "S"},
                {"AttributeName": "CreatedAt", "AttributeType": "N"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": store.ACCOUNT_INDEX,
                    "KeySchema": [
                        {"AttributeName": "AccountId", "KeyType": "HASH"},
                        {"AttributeName": "CreatedAt", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ],
        )
        accounts = db.create_table(
            TableName="jit-accounts",
            BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "AccountId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "AccountId", "AttributeType": "S"}],
        )
        accounts.put_item(Item={"AccountId": ACCOUNT, "Owners": {"alice"}, "Deputies": {"bob"}})
        accounts.put_item(Item={"AccountId": OTHER_ACCOUNT, "Owners": {"carol"}})
        store.reset_clients()
        yield db


@pytest.fixture
def known_users(monkeypatch):
    """A fake identity store that knows a fixed set of users."""
    users = {"alice": "uid-alice", "bob": "uid-bob", "dave": "uid-dave", "carol": "uid-carol"}
    monkeypatch.setattr(identity, "find_user_id", lambda username: users.get(username.lower()))
    return users


def policy_document(action: str = "s3:GetObject", resource: str = "arn:aws:s3:::incident-logs/*") -> dict:
    return {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Action": [action], "Resource": resource}]}


def clock(monkeypatch, value: int) -> None:
    monkeypatch.setattr(store, "now", lambda: value)
