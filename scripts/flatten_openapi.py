#!/usr/bin/env python3
"""Flatten an OpenAPI document so Microsoft Copilot Studio can use the generated MCP tools.

Copilot Studio drops MCP tools whose input schema contains reference types
(``$ref``) and handles ``anyOf`` / ``oneOf`` unions poorly. AgentCore Gateway
builds each tool's input schema from the OpenAPI operation, so the document you
give it must be self-contained. This script:

1. Inlines every local ``$ref`` (``#/components/...``).
2. Collapses nullable unions to the single non-null schema:
   ``anyOf: [{type: string}, {type: null}]`` becomes ``{type: string}``, and
   ``type: ["string", "null"]`` becomes ``type: string``. Optional fields stay
   optional, because they are not listed under ``required``.
3. Collapses a union of plain scalars (for example ``string | integer``) to
   ``string``. These only appear in error responses, never in tool inputs.
4. Removes ``components.schemas``, which nothing references any more.

The API contract does not change. Only the description sent to the gateway is
simplified.

Usage:
    python scripts/flatten_openapi.py openapi.json openapi.flat.json
"""

from __future__ import annotations

import copy
import json
import sys
from typing import Any

_UNION_KEYS = ("anyOf", "oneOf")
_KEEP_FROM_WRAPPER = ("title", "description", "default", "examples", "example")
_SCALAR_TYPES = {"string", "integer", "number", "boolean"}


def _resolve(spec: dict[str, Any], ref: str) -> Any:
    if not ref.startswith("#/"):
        raise ValueError(f"Only local $ref values are supported, got: {ref}")
    node: Any = spec
    for part in ref[2:].split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node


def _is_null(schema: Any) -> bool:
    return isinstance(schema, dict) and schema == {"type": "null"}


def _is_scalar(schema: Any) -> bool:
    return isinstance(schema, dict) and set(schema) == {"type"} and schema["type"] in _SCALAR_TYPES


def _collapse_union(node: dict[str, Any]) -> dict[str, Any]:
    for key in _UNION_KEYS:
        branches = node.get(key)
        if not isinstance(branches, list):
            continue
        non_null = [branch for branch in branches if not _is_null(branch)]
        if len(non_null) > 1 and all(_is_scalar(branch) for branch in non_null):
            non_null = [{"type": "string"}]
        if len(non_null) != 1:
            print(f"warning: kept {key} with {len(non_null)} non-null branches", file=sys.stderr)
            continue
        merged = copy.deepcopy(non_null[0])
        for keep in _KEEP_FROM_WRAPPER:
            if keep in node and keep not in merged:
                merged[keep] = node[keep]
        for name, value in node.items():
            if name not in _UNION_KEYS and name not in merged:
                merged[name] = value
        return merged
    return node


def _collapse_type_array(node: dict[str, Any]) -> None:
    value = node.get("type")
    if isinstance(value, list):
        non_null = [item for item in value if item != "null"]
        if len(non_null) == 1:
            node["type"] = non_null[0]
        else:
            print(f"warning: kept multi-type {value}", file=sys.stderr)


def _flatten(node: Any, spec: dict[str, Any], stack: tuple[str, ...]) -> Any:
    if isinstance(node, list):
        return [_flatten(item, spec, stack) for item in node]
    if not isinstance(node, dict):
        return node

    if "$ref" in node:
        ref = node["$ref"]
        if ref in stack:
            raise ValueError(f"Recursive $ref cannot be inlined: {' -> '.join((*stack, ref))}")
        target = copy.deepcopy(_resolve(spec, ref))
        target.update({key: value for key, value in node.items() if key != "$ref"})
        return _flatten(target, spec, (*stack, ref))

    flattened = {key: _flatten(value, spec, stack) for key, value in node.items()}
    flattened = _collapse_union(flattened)
    _collapse_type_array(flattened)
    return flattened


def flatten_spec(spec: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(spec)
    result["paths"] = _flatten(spec.get("paths", {}), spec, ())
    components = result.get("components")
    if isinstance(components, dict):
        components.pop("schemas", None)
        if not components:
            result.pop("components")
    return result


def remaining_problems(spec: dict[str, Any]) -> list[str]:
    """Names of constructs Copilot Studio cannot handle that are still in the document."""
    text = json.dumps(spec)
    return [key for key in ("$ref", "anyOf", "oneOf") if f'"{key}"' in text]


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as source:
        flat = flatten_spec(json.load(source))
    problems = remaining_problems(flat)
    if problems:
        print(f"error: the flattened document still contains {problems}", file=sys.stderr)
        return 1
    with open(argv[2], "w", encoding="utf-8") as target:
        json.dump(flat, target, indent=2, ensure_ascii=False)
        target.write("\n")
    print(f"wrote {argv[2]} ({len(flat.get('paths', {}))} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
