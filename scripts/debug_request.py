"""Run request_access locally against the deployed tables and print the real error.

Use it when the API returns "Internal server error". It calls the same handler
code as the Lambda, with your own AWS credentials, so any exception shows up
here with a full traceback.

    .venv/bin/python scripts/debug_request.py --user admin --account 111122223333

Note: if it succeeds, it creates a real Pending request. Reject it afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

import boto3

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--user", required=True, help="User name as the authorizer passes it, e.g. admin")
    parser.add_argument("--account", required=True, help="12-digit account ID to request")
    parser.add_argument("--region", default="eu-central-1")
    parser.add_argument("--prefix", default="jit-access", help="Terraform name_prefix")
    args = parser.parse_args()

    instance = boto3.client("sso-admin", region_name=args.region).list_instances()["Instances"][0]
    os.environ.update(
        {
            "AWS_DEFAULT_REGION": args.region,
            "REQUESTS_TABLE": f"{args.prefix}-requests",
            "ACCOUNTS_TABLE": f"{args.prefix}-accounts",
            "IDENTITY_STORE_ID": instance["IdentityStoreId"],
            "IDENTITY_CENTER_INSTANCE_ARN": instance["InstanceArn"],
            "JIT_DEBUG": "true",
            "POWERTOOLS_LOG_LEVEL": "DEBUG",
        }
    )

    from jit_access.api import app as api

    body = {
        "AccountId": args.account,
        "Reason": "Local debug of request_access",
        "DurationHours": 1,
        "PolicyArn": "arn:aws:iam::aws:policy/ReadOnlyAccess",
    }
    event = {
        "resource": "/jit/request-access",
        "path": "/jit/request-access",
        "httpMethod": "POST",
        "headers": {"Content-Type": "application/json"},
        "queryStringParameters": None,
        "body": json.dumps(body),
        "isBase64Encoded": False,
        "requestContext": {"authorizer": {"username": args.user}, "httpMethod": "POST", "stage": "local"},
    }

    # Call the route directly so exceptions are raised here instead of turned into a 500.
    api.app.exception_handler(Exception)(_reraise)
    try:
        response = api.app.resolve(event, None)
        print(json.dumps(response, indent=2))
    except Exception:
        traceback.print_exc()
        sys.exit(1)


def _reraise(error: Exception):
    raise error


if __name__ == "__main__":
    main()
