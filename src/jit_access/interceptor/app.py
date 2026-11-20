"""Amazon Bedrock AgentCore Gateway request interceptor.

The gateway validates the caller's token, then calls the API on the caller's
behalf. This interceptor forwards the caller's original ``Authorization`` header
to the API, unchanged, so the API's authorizer validates the same token and sees
the same person. Without it, the API would not know who is calling.

The event is deliberately not logged: it contains the bearer token.
"""

from __future__ import annotations

from typing import Any

from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext

logger = Logger(service="jit-access-interceptor")

_OUTPUT_VERSION = "1.0"


def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    gateway_request = (event.get("mcp") or {}).get("gatewayRequest") or {}
    body = gateway_request.get("body") or {}
    headers = gateway_request.get("headers") or {}

    authorization = next(
        (value for key, value in headers.items() if str(key).lower() == "authorization"),
        None,
    )

    transformed: dict[str, Any] = {"body": body}
    if authorization:
        transformed["headers"] = {"Authorization": authorization}
    else:
        logger.warning("Gateway request had no Authorization header")

    return {
        "interceptorOutputVersion": _OUTPUT_VERSION,
        "mcp": {"transformedGatewayRequest": transformed},
    }
