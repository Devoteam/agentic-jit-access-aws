import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from jit_access.interceptor import app as interceptor
from jit_access.trigger import app as trigger

REQUEST_ID = "0a1b2c3d-1111-2222-3333-444455556666"


def record(old: str | None, new: str | None, event: str = "MODIFY", sequence: str = "1") -> dict:
    change = {"Keys": {"RequestId": {"S": REQUEST_ID}}, "SequenceNumber": sequence}
    if old:
        change["OldImage"] = {"Status": {"S": old}}
    if new:
        change["NewImage"] = {"Status": {"S": new}}
    return {"eventName": event, "dynamodb": change}


@pytest.fixture
def states(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(trigger, "_states", lambda: client)
    return client


def run(*records: dict) -> dict:
    return trigger.lambda_handler({"Records": list(records)}, None)


def test_an_approval_starts_the_workflow_once(states):
    assert run(record("Pending", "Approved")) == {"batchItemFailures": []}
    states.start_execution.assert_called_once()
    arguments = states.start_execution.call_args.kwargs
    assert arguments["name"] == f"jit-{REQUEST_ID}"
    assert json.loads(arguments["input"]) == {"RequestId": REQUEST_ID}


@pytest.mark.parametrize(
    "old, new, event",
    [
        ("Pending", "Rejected", "MODIFY"),
        ("Approved", "Approved", "MODIFY"),  # a later update of an approved request (workflow progress)
        ("Rejected", "Approved", "MODIFY"),  # cannot happen, but must never start access
        (None, "Approved", "INSERT"),
        ("Approved", None, "REMOVE"),
        (None, "Pending", "INSERT"),
    ],
)
def test_everything_else_is_ignored(states, old, new, event):
    run(record(old, new, event))
    states.start_execution.assert_not_called()


def test_a_workflow_that_already_exists_is_not_an_error(states):
    states.start_execution.side_effect = ClientError({"Error": {"Code": "ExecutionAlreadyExists"}}, "StartExecution")
    assert run(record("Pending", "Approved")) == {"batchItemFailures": []}


def test_a_failure_is_reported_so_the_stream_retries_only_that_record(states):
    states.start_execution.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "StartExecution")
    result = run(record("Pending", "Approved", sequence="42"), record("Pending", "Rejected", sequence="43"))
    assert result == {"batchItemFailures": [{"itemIdentifier": "42"}]}


def gateway_event(headers: dict | None) -> dict:
    return {"mcp": {"gatewayRequest": {"body": {"method": "tools/call"}, "headers": headers or {}}}}


def test_the_interceptor_forwards_the_callers_token_unchanged():
    result = interceptor.lambda_handler(gateway_event({"Authorization": "Bearer abc.def.ghi", "X-Other": "1"}), None)
    transformed = result["mcp"]["transformedGatewayRequest"]
    assert transformed["headers"] == {"Authorization": "Bearer abc.def.ghi"}
    assert transformed["body"] == {"method": "tools/call"}
    assert result["interceptorOutputVersion"] == "1.0"


def test_the_interceptor_reads_the_header_case_insensitively():
    result = interceptor.lambda_handler(gateway_event({"authorization": "Bearer abc"}), None)
    assert result["mcp"]["transformedGatewayRequest"]["headers"] == {"Authorization": "Bearer abc"}


def test_the_interceptor_sends_no_header_when_there_is_none():
    result = interceptor.lambda_handler(gateway_event({}), None)
    assert "headers" not in result["mcp"]["transformedGatewayRequest"]


def test_the_interceptor_does_not_log_the_token(caplog, capsys):
    interceptor.lambda_handler(gateway_event({"Authorization": "Bearer super-secret-token"}), None)
    assert "super-secret-token" not in capsys.readouterr().out + caplog.text
