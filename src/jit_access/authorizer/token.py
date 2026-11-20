"""Validation of Microsoft Entra ID access tokens.

The AgentCore Gateway validates the token first. This validation runs again,
independently, in the API's Lambda authorizer, so a request that bypasses the
gateway is still rejected. Both check that the token was issued by your tenant
for your API.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import jwt

from jit_access.common.usernames import normalize_username


class TokenError(Exception):
    """The token is missing, invalid, or not allowed to call this API."""


class SigningKeyProvider(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> Any: ...


@dataclass(frozen=True)
class EntraConfig:
    tenant_id: str
    audiences: tuple[str, ...]
    required_scope: str
    username_claim: str
    strip_username_domains: tuple[str, ...]

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def jwks_url(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"

    @classmethod
    def from_env(cls) -> EntraConfig:
        def csv(name: str) -> tuple[str, ...]:
            return tuple(part.strip() for part in os.environ.get(name, "").split(",") if part.strip())

        return cls(
            tenant_id=os.environ.get("ENTRA_TENANT_ID", "").strip(),
            audiences=csv("ENTRA_AUDIENCES"),
            required_scope=os.environ.get("ENTRA_REQUIRED_SCOPE", "access_as_user").strip(),
            username_claim=os.environ.get("USERNAME_CLAIM", "preferred_username").strip(),
            strip_username_domains=tuple(d.lower() for d in csv("STRIP_USERNAME_DOMAINS")),
        )


@lru_cache(maxsize=4)
def _jwks_client(jwks_url: str) -> jwt.PyJWKClient:
    # PyJWKClient caches keys in memory, which survives warm Lambda invocations.
    return jwt.PyJWKClient(jwks_url)


def validate(
    token: str,
    config: EntraConfig,
    key_provider: SigningKeyProvider | None = None,
) -> dict[str, Any]:
    """Validate an Entra ID v2.0 access token and return its claims.

    Checks signature, expiry, issuer, audience, tenant, and the delegated scope.
    Raises ``TokenError`` for anything else, without saying why to the caller.
    """
    if not config.tenant_id or not config.audiences:
        raise TokenError("Entra validation is not configured.")
    if not token:
        raise TokenError("Missing token.")

    provider = key_provider or _jwks_client(config.jwks_url)
    try:
        signing_key = provider.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=config.issuer,
            audience=list(config.audiences),
            options={"require": ["exp", "iss", "aud"]},
        )
    except (jwt.PyJWTError, ValueError) as error:
        raise TokenError(f"Invalid token: {error}") from error

    if claims.get("tid") != config.tenant_id:
        raise TokenError("Token was issued by a different tenant.")

    # Delegated (user) tokens carry "scp". Application-only tokens do not, so
    # requiring the scope also rejects tokens that do not represent a person.
    if config.required_scope and config.required_scope not in str(claims.get("scp", "")).split():
        raise TokenError("Token does not carry the required scope.")

    return claims


def username_from(claims: dict[str, Any], config: EntraConfig) -> str:
    """The Identity Center user name for the person the token represents."""
    value = claims.get(config.username_claim)
    if not isinstance(value, str) or not value.strip():
        raise TokenError(f"Token has no '{config.username_claim}' claim.")
    return normalize_username(value, config.strip_username_domains)
