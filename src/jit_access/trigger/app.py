"""Starts the access workflow when a request is approved.

Listens to the DynamoDB stream of the requests table. Approval is recorded
durably first, and provisioning follows from the stream, so a failure to start
the workflow is retried by the stream instead of leaving an approved request
that nobody provisions.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.utilities.typing import LambdaContext
from boto3.dynamodb.types import TypeDeserializer
from botocore.exceptions import ClientError

from jit_access.common.config import Settings
from jit_access.common.store import STATUS_APPROVED, STATUS_PENDING

logger = Logger(service="jit-access-trigger")
_deserializer = TypeDeserializer()


@lru_cache(maxsize=1)
def _states():
    return boto3.client("stepfunctions")


def _status(image: dict[str, Any]) -> str | None:
    value = image.get("Status")
    return _deserializer.deserialize(value) if value else None


def _is_approval(record: dict[str, Any]) -> bool:
    """True when the request just moved from Pending to Approved."""
    if record.get("eventName") != "MODIFY":
        return False
    change = record["dynamodb"]
    return (
        _status(change.get("NewImage", {})) == STATUS_APPROVED and _status(change.get("OldImage", {})) == STATUS_PENDING
    )


def _start(request_id: str) -> None:
    try:
        _states().start_execution(
            stateMachineArn=Settings.from_env().state_machine_arn,
            # The name makes the start idempotent: a retry cannot start a second workflow.
            name=f"jit-{request_id}",
            input=json.dumps({"RequestId": request_id}),
        )
    except ClientError as error:
        if error.response["Error"]["Code"] == "ExecutionAlreadyExists":
            logger.info("Workflow already started", extra={"request_id": request_id})
            return
        raise
    logger.info("Workflow started", extra={"request_id": request_id})


def lambda_handler(event: dict[str, Any], context: LambdaContext) -> dict[str, Any]:
    failures: list[dict[str, str]] = []
    for record in event.get("Records", []):
        try:
            if _is_approval(record):
                keys = record["dynamodb"]["Keys"]
                _start(_deserializer.deserialize(keys["RequestId"]))
        except Exception:
            logger.exception("Could not start workflow")
            failures.append({"itemIdentifier": record["dynamodb"]["SequenceNumber"]})
    # Failed records are retried by the stream.
    return {"batchItemFailures": failures}
