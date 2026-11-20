"""Request models of the access API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jit_access.common.policy import validate_inline_policy, validate_managed_policy_arn


class RequestAccessBody(BaseModel):
    """Body of ``POST /jit/request-access``.

    The body is flat on purpose. Agent platforms such as Microsoft Copilot
    Studio do not reliably fill nested object inputs, so every field is top
    level. There is deliberately no field for the requester: the API takes it
    from the verified token, so nobody can request access for someone else.
    """

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "AccountId": "111122223333",
                    "Reason": "Read dev secrets to debug incident 4711",
                    "DurationHours": 2,
                    "PolicyDocument": {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue"],
                                "Resource": "arn:aws:secretsmanager:eu-central-1:111122223333:secret:dev/*",
                            }
                        ],
                    },
                },
                {
                    "AccountId": "111122223333",
                    "Reason": "Production release",
                    "DurationHours": 1,
                    "PolicyArn": "arn:aws:iam::aws:policy/ReadOnlyAccess",
                },
            ]
        },
    )

    AccountId: str = Field(
        ...,
        pattern=r"^\d{12}$",
        description="The 12-digit AWS account ID to get access to.",
        examples=["111122223333"],
    )
    Reason: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Business justification for the access, shown to the approver.",
        examples=["Debug incident 4711"],
    )
    DurationHours: int = Field(
        1,
        ge=1,
        le=72,
        description="How long the access lasts, in whole hours. The default is 1.",
        examples=[2],
    )
    PolicyDocument: dict | None = Field(
        None,
        description=(
            "Preferred. A custom inline IAM policy limited to the actions and resources the task needs. "
            "Provide exactly one of PolicyDocument or PolicyArn."
        ),
    )
    PolicyArn: str | None = Field(
        None,
        description=(
            "An AWS managed policy ARN such as arn:aws:iam::aws:policy/ReadOnlyAccess. "
            "Use only when a managed policy is really needed. Provide exactly one of PolicyDocument or PolicyArn."
        ),
        examples=["arn:aws:iam::aws:policy/ReadOnlyAccess"],
    )

    @model_validator(mode="after")
    def _exactly_one_policy(self) -> RequestAccessBody:
        has_document = bool(self.PolicyDocument)
        has_arn = bool(self.PolicyArn)
        if has_document == has_arn:
            raise ValueError("Provide exactly one of PolicyDocument or PolicyArn.")
        if has_document:
            validate_inline_policy(self.PolicyDocument)
        else:
            validate_managed_policy_arn(self.PolicyArn or "")
        return self
