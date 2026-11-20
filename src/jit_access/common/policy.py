"""Validation and fingerprinting of the policy attached to an access request.

The API checks the structure of a policy when a request is submitted. AWS IAM
Identity Center validates the policy again when it is attached to the
permission set, so a malformed policy fails before any assignment exists.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# IAM Identity Center accepts AWS managed policies for permission sets through
# AttachManagedPolicyToPermissionSet. Customer managed policies use a different
# API and are not supported by this solution.
AWS_MANAGED_POLICY_ARN = re.compile(r"^arn:aws:iam::aws:policy/[\w+=,.@/-]+$")

# Conservative limit for an inline policy attached to a permission set.
MAX_INLINE_POLICY_CHARS = 10_240

# 32 hex characters is a 128-bit prefix of SHA-256.
FINGERPRINT_HEX_LENGTH = 32


def validate_managed_policy_arn(arn: str) -> None:
    if not AWS_MANAGED_POLICY_ARN.match(arn):
        raise ValueError("PolicyArn must be an AWS managed policy ARN such as arn:aws:iam::aws:policy/ReadOnlyAccess.")


def validate_inline_policy(document: Any) -> None:
    """Check the structure of an inline IAM policy document.

    This catches mistakes early, so the request fails at submission instead of
    in the middle of provisioning. It is not a replacement for IAM's own
    validation, which runs again when the policy is attached.
    """
    if not isinstance(document, dict):
        raise ValueError("PolicyDocument must be a JSON object.")
    if document.get("Version") != "2012-10-17":
        raise ValueError("PolicyDocument.Version must be '2012-10-17'.")

    statements = document.get("Statement")
    if not isinstance(statements, list) or not statements:
        raise ValueError("PolicyDocument.Statement must be a non-empty list.")

    for index, statement in enumerate(statements):
        if not isinstance(statement, dict):
            raise ValueError(f"PolicyDocument.Statement[{index}] must be an object.")
        if statement.get("Effect") not in ("Allow", "Deny"):
            raise ValueError(f"PolicyDocument.Statement[{index}].Effect must be 'Allow' or 'Deny'.")
        if "Action" not in statement and "NotAction" not in statement:
            raise ValueError(f"PolicyDocument.Statement[{index}] must include Action or NotAction.")
        if "Resource" not in statement and "NotResource" not in statement:
            raise ValueError(f"PolicyDocument.Statement[{index}] must include Resource or NotResource.")

    if len(json.dumps(document, separators=(",", ":"))) > MAX_INLINE_POLICY_CHARS:
        raise ValueError(f"PolicyDocument is larger than {MAX_INLINE_POLICY_CHARS} characters.")


def policy_fingerprint(policy_arn: str | None, policy_document: dict[str, Any] | None) -> str:
    """Return a stable fingerprint of the requested policy.

    The document is serialized canonically (sorted keys, no whitespace), so the
    same policy always produces the same value regardless of key order or
    formatting, and any change produces a different one. The approver reviews a
    policy together with its fingerprint and sends the fingerprint back with the
    approval. The API approves only if the stored policy still has that exact
    fingerprint.
    """
    if policy_arn:
        payload = f"arn:{policy_arn}"
    else:
        payload = "doc:" + json.dumps(policy_document or {}, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return "sha256:" + digest[:FINGERPRINT_HEX_LENGTH]
