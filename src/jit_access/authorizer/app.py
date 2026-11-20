"""API Gateway Lambda authorizer (REQUEST type).

This function authenticates. It validates the bearer token and passes the
verified user name to the API in the request context. It does not decide what
the user may do: the API applies the ownership rules. Keeping the two apart
makes the authorizer's result safe to cache by token.
"""

from __future__ import annotations

from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

from jit_access.authorizer import token as tokens

logger = Logger(service="jit-access-authorizer")


def _bearer_token(event: dict[str, Any]) -> str:
    headers = {str(key).lower(): value for key, value in (event.get("headers") or {}).items()}
    value = str(headers.get("authorization", "")).strip()
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return ""


def _allow_all_methods(method_arn: str) -> str:
    """Allow every method of the API stage, so a cached result works for all routes.

    ``arn:aws:execute-api:region:account:api/stage/METHOD/path`` becomes
    ``arn:aws:execute-api:region:account:api/stage/*/*``.
    """
    prefix, _, rest = method_arn.partition("/")
    stage = rest.split("/", 1)[0]
    return f"{prefix}/{stage}/*/*"


def _policy(effect: str, principal: str, resource: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "principalId": principal,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [{"Action": "execute-api:Invoke", "Effect": effect, "Resource": resource}],
        },
        "context": context or {},
    }


@logger.inject_lambda_context
def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    config = tokens.EntraConfig.from_env()
    try:
        claims = tokens.validate(_bearer_token(event), config)
        username = tokens.username_from(claims, config)
    except tokens.TokenError as error:
        logger.info("Token rejected", extra={"reason": str(error)})
        # API Gateway turns this into a 401 response.
        raise Exception("Unauthorized") from error

    logger.info("Token accepted", extra={"username": username})
    return _policy(
        "Allow",
        username,
        _allow_all_methods(event["methodArn"]),
        {
            "username": username,
            "subject": str(claims.get("oid") or claims.get("sub") or ""),
            "tenant": str(claims.get("tid", "")),
        },
    )
