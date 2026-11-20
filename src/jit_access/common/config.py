"""Runtime configuration, read from environment variables.

Values are read when ``Settings.from_env()`` is called, not at import time, so
modules can be imported (for example to generate the OpenAPI document) without
a deployed environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _csv(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """All tunable behaviour of the solution."""

    requests_table: str
    accounts_table: str
    users_table: str
    identity_store_id: str
    instance_arn: str
    identity_center_role_arn: str
    state_machine_arn: str
    session_duration: str
    max_duration_hours: int
    approval_window_hours: int
    allow_self_approval: bool
    allowed_managed_policy_arns: tuple[str, ...]
    request_retention_days: int
    strip_username_domains: tuple[str, ...]
    debug: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            requests_table=os.environ.get("REQUESTS_TABLE", ""),
            accounts_table=os.environ.get("ACCOUNTS_TABLE", ""),
            users_table=os.environ.get("USERS_TABLE", ""),
            identity_store_id=os.environ.get("IDENTITY_STORE_ID", ""),
            instance_arn=os.environ.get("IDENTITY_CENTER_INSTANCE_ARN", ""),
            identity_center_role_arn=os.environ.get("IDENTITY_CENTER_ROLE_ARN", "").strip(),
            state_machine_arn=os.environ.get("STATE_MACHINE_ARN", ""),
            session_duration=os.environ.get("PERMISSION_SET_SESSION_DURATION", "PT1H"),
            max_duration_hours=_int("MAX_DURATION_HOURS", 8),
            approval_window_hours=_int("APPROVAL_WINDOW_HOURS", 24),
            allow_self_approval=_bool("ALLOW_SELF_APPROVAL", False),
            allowed_managed_policy_arns=_csv("ALLOWED_MANAGED_POLICY_ARNS"),
            request_retention_days=_int("REQUEST_RETENTION_DAYS", 365),
            strip_username_domains=tuple(d.lower() for d in _csv("STRIP_USERNAME_DOMAINS")),
            debug=_bool("JIT_DEBUG", False),
        )
