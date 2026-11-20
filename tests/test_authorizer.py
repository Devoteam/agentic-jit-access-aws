"""Token validation and the Lambda authorizer, with real signed JWTs."""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from jit_access.authorizer import app as authorizer
from jit_access.authorizer import token as tokens

TENANT = "11111111-2222-3333-4444-555555555555"
AUDIENCE = "api://aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
ISSUER = f"https://login.microsoftonline.com/{TENANT}/v2.0"

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJwks:
    """Stands in for the tenant's JWKS endpoint."""

    def get_signing_key_from_jwt(self, token: str):
        return SimpleNamespace(key=KEY.public_key())


CONFIG = tokens.EntraConfig(
    tenant_id=TENANT,
    audiences=(AUDIENCE,),
    required_scope="access_as_user",
    username_claim="preferred_username",
    strip_username_domains=("contoso.onmicrosoft.com",),
)


def mint(signing_key=KEY, **overrides) -> str:
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "tid": TENANT,
        "scp": "access_as_user",
        "preferred_username": "alice@contoso.onmicrosoft.com",
        "oid": "object-id-1",
        "exp": int(time.time()) + 600,
        "nbf": int(time.time()) - 10,
    }
    claims.update(overrides)
    claims = {key: value for key, value in claims.items() if value is not None}
    return jwt.encode(claims, signing_key, algorithm="RS256")


def validate(token: str) -> dict:
    return tokens.validate(token, CONFIG, key_provider=FakeJwks())


def test_valid_token_is_accepted_and_maps_to_a_username():
    claims = validate(mint())
    assert tokens.username_from(claims, CONFIG) == "alice"


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"aud": "api://someone-else"}, "token for another application, such as Microsoft Graph"),
        ({"iss": "https://login.microsoftonline.com/other/v2.0"}, "wrong issuer"),
        ({"tid": "99999999-9999-9999-9999-999999999999"}, "wrong tenant"),
        ({"exp": int(time.time()) - 60}, "expired"),
        ({"nbf": int(time.time()) + 600}, "not yet valid"),
        ({"scp": "User.Read"}, "missing the required scope"),
        ({"scp": None}, "application-only token without a scope"),
        ({"exp": None}, "no expiry"),
    ],
)
def test_bad_tokens_are_rejected(overrides, reason):
    with pytest.raises(tokens.TokenError):
        validate(mint(**overrides))


def test_token_signed_with_another_key_is_rejected():
    with pytest.raises(tokens.TokenError):
        validate(mint(signing_key=OTHER_KEY))


def test_unsigned_token_is_rejected():
    unsigned = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "tid": TENANT, "scp": "access_as_user"}, None, algorithm="none"
    )
    with pytest.raises(tokens.TokenError):
        validate(unsigned)


def test_garbage_and_empty_tokens_are_rejected():
    for value in ("", "not-a-jwt", "a.b.c"):
        with pytest.raises(tokens.TokenError):
            validate(value)


def test_unconfigured_validation_rejects_everything():
    empty = tokens.EntraConfig("", (), "access_as_user", "preferred_username", ())
    with pytest.raises(tokens.TokenError):
        tokens.validate(mint(), empty, key_provider=FakeJwks())


def test_token_without_a_username_claim_is_rejected():
    claims = validate(mint(preferred_username=None))
    with pytest.raises(tokens.TokenError):
        tokens.username_from(claims, CONFIG)


def test_other_domains_keep_their_full_name():
    claims = validate(mint(preferred_username="alice@partner.com"))
    assert tokens.username_from(claims, CONFIG) == "alice@partner.com"


# --------------------------------------------------------------------------- handler

METHOD_ARN = "arn:aws:execute-api:eu-central-1:111122223333:abc123/prod/POST/jit/request-access"


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv("ENTRA_TENANT_ID", TENANT)
    monkeypatch.setenv("ENTRA_AUDIENCES", AUDIENCE)
    monkeypatch.setenv("STRIP_USERNAME_DOMAINS", "contoso.onmicrosoft.com")
    monkeypatch.setattr(tokens, "_jwks_client", lambda url: FakeJwks())


def event(token: str | None, header: str = "Authorization") -> dict:
    headers = {header: f"Bearer {token}"} if token else {}
    return {"headers": headers, "methodArn": METHOD_ARN}


def test_handler_allows_a_valid_token_and_passes_the_username(configured):
    result = authorizer.lambda_handler.__wrapped__(event(mint()), None)
    statement = result["policyDocument"]["Statement"][0]
    assert statement["Effect"] == "Allow"
    assert result["context"]["username"] == "alice"
    assert result["context"]["subject"] == "object-id-1"
    assert result["principalId"] == "alice"


def test_handler_allows_every_method_of_the_stage_so_results_can_be_cached(configured):
    result = authorizer.lambda_handler.__wrapped__(event(mint()), None)
    assert (
        result["policyDocument"]["Statement"][0]["Resource"]
        == "arn:aws:execute-api:eu-central-1:111122223333:abc123/prod/*/*"
    )


def test_handler_reads_the_header_case_insensitively(configured):
    result = authorizer.lambda_handler.__wrapped__(event(mint(), header="authorization"), None)
    assert result["context"]["username"] == "alice"


@pytest.mark.parametrize("token", [None, "garbage", ""])
def test_handler_answers_unauthorized_for_missing_or_bad_tokens(configured, token):
    with pytest.raises(Exception, match="^Unauthorized$"):
        authorizer.lambda_handler.__wrapped__(event(token), None)


def test_handler_rejects_a_token_for_another_audience(configured):
    with pytest.raises(Exception, match="^Unauthorized$"):
        authorizer.lambda_handler.__wrapped__(event(mint(aud="api://other")), None)
