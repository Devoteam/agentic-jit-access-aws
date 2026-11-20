"""The OpenAPI document defines the agent's tools, so its shape is part of the contract."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from jit_access.api.app import openapi_document

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


flatten = load_script("flatten_openapi")


@pytest.fixture(scope="module")
def raw() -> dict:
    return json.loads(openapi_document("https://api.example.com"))


@pytest.fixture(scope="module")
def flat(raw) -> dict:
    return flatten.flatten_spec(raw)


def operations(spec: dict) -> dict[str, dict]:
    return {op["operationId"]: op for methods in spec["paths"].values() for op in methods.values()}


def test_the_agent_gets_exactly_five_tools(flat):
    assert sorted(operations(flat)) == [
        "approve_request",
        "get_request",
        "list_requests",
        "reject_request",
        "request_access",
    ]


def test_every_tool_requires_the_bearer_token(flat):
    assert flat["security"] == [{"BearerAuth": []}]
    assert flat["components"]["securitySchemes"]["BearerAuth"]["name"] == "Authorization"


def test_the_flattened_document_has_nothing_copilot_studio_drops(flat):
    assert flatten.remaining_problems(flat) == []
    assert "schemas" not in flat.get("components", {})


def test_the_request_body_is_flat_and_has_no_requester_field(flat):
    schema = operations(flat)["request_access"]["requestBody"]["content"]["application/json"]["schema"]
    assert set(schema["properties"]) == {"AccountId", "Reason", "DurationHours", "PolicyDocument", "PolicyArn"}
    assert set(schema["required"]) == {"AccountId", "Reason"}
    assert schema["additionalProperties"] is False
    assert all(prop.get("type") in {"string", "integer", "object"} for prop in schema["properties"].values())
    assert not any(prop["type"] == "object" and "properties" in prop for prop in schema["properties"].values())


def test_approval_requires_the_fingerprint(flat):
    parameters = {p["name"]: p for p in operations(flat)["approve_request"]["parameters"]}
    assert parameters["ExpectedPolicyFingerprint"]["required"] is True
    assert parameters["RequestId"]["required"] is True


def test_every_tool_describes_itself_for_the_agent(flat):
    for name, operation in operations(flat).items():
        assert len(operation.get("description", "")) > 80, name
        assert operation.get("summary"), name


def test_flattening_does_not_change_the_operations(raw, flat):
    def shape(spec):
        return sorted(
            (path, method, op["operationId"])
            for path, methods in spec["paths"].items()
            for method, op in methods.items()
        )

    assert shape(raw) == shape(flat)


def test_flattening_inlines_references_and_collapses_nullable_unions():
    spec = {
        "paths": {
            "/x": {
                "post": {
                    "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Body"}}}},
                }
            }
        },
        "components": {
            "schemas": {
                "Body": {
                    "type": "object",
                    "properties": {
                        "name": {"anyOf": [{"type": "string"}, {"type": "null"}], "description": "A name"},
                        "age": {"type": ["integer", "null"]},
                        "loc": {"anyOf": [{"type": "string"}, {"type": "integer"}]},
                        "inner": {"$ref": "#/components/schemas/Inner"},
                    },
                },
                "Inner": {"type": "object", "properties": {"flag": {"type": "boolean"}}},
            }
        },
    }
    result = flatten.flatten_spec(spec)
    body = result["paths"]["/x"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert body["properties"]["name"] == {"type": "string", "description": "A name"}
    assert body["properties"]["age"] == {"type": "integer"}
    assert body["properties"]["loc"] == {"type": "string"}
    assert body["properties"]["inner"]["properties"]["flag"] == {"type": "boolean"}
    assert flatten.remaining_problems(result) == []


def test_recursive_references_are_reported_instead_of_looping_forever():
    spec = {
        "paths": {"/x": {"get": {"schema": {"$ref": "#/components/schemas/Node"}}}},
        "components": {
            "schemas": {"Node": {"type": "object", "properties": {"next": {"$ref": "#/components/schemas/Node"}}}}
        },
    }
    with pytest.raises(ValueError, match="Recursive"):
        flatten.flatten_spec(spec)
