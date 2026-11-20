"""The users table maps Entra ID user names to IAM Identity Center user IDs."""

from __future__ import annotations

import pytest

from jit_access.common import identity, store


@pytest.fixture
def users_table(tables, monkeypatch):
    monkeypatch.setenv("USERS_TABLE", "jit-users")
    table = tables.create_table(
        TableName="jit-users",
        BillingMode="PAY_PER_REQUEST",
        KeySchema=[{"AttributeName": "EntraUsername", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "EntraUsername", "AttributeType": "S"}],
    )
    table.put_item(
        Item={
            "EntraUsername": "alex",
            "IdentityCenterUserName": "alex@example.com",
            "UserId": "11111111-2222-3333-4444-555555555555",
        }
    )
    store.reset_clients()
    return table


def test_mapped_user_resolves_to_identity_center_user_id(users_table):
    assert identity.find_user_id("alex") == "11111111-2222-3333-4444-555555555555"


def test_lookup_ignores_case_and_whitespace(users_table):
    assert identity.find_user_id(" Alex ") == "11111111-2222-3333-4444-555555555555"


def test_unmapped_user_is_unknown(users_table):
    assert identity.find_user_id("mallory") is None


def test_mapping_does_not_call_the_identity_store(users_table, monkeypatch):
    class NoIdentityStore:
        def __call__(self):
            raise AssertionError("identity store must not be called when USERS_TABLE is set")

        def cache_clear(self):
            pass

    monkeypatch.setattr(identity, "_identitystore", NoIdentityStore())
    assert identity.find_user_id("alex")
