#!/usr/bin/env bash
# Create or update the AgentCore Gateway target that turns the API's OpenAPI
# document into MCP tools.
#
# Run it after `terraform apply`, and again whenever the API changes. A target
# reads the document from S3 only when it is created or updated, so uploading a
# new document is not enough: the target must be updated to pick it up.
#
# Afterwards, remove and re-add the MCP tool in Copilot Studio. Copilot Studio
# caches a tool's definition, for up to 24 hours.
#
# Usage:
#   ./scripts/publish_gateway_target.sh
#
# Environment:
#   TF            terraform or tofu (default: terraform)
#   TARGET_NAME   name of the gateway target (default: jit-access)
#   AWS_REGION    region of the gateway (default: the region of your AWS profile)

set -euo pipefail

TF="${TF:-terraform}"
TARGET_NAME="${TARGET_NAME:-jit-access}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TF_DIR="$SCRIPT_DIR/../infra/terraform"

command -v aws >/dev/null || { echo "error: the AWS CLI is not installed." >&2; exit 1; }
command -v "$TF" >/dev/null || { echo "error: '$TF' is not installed. Set TF=tofu to use OpenTofu." >&2; exit 1; }

GATEWAY_ID="$("$TF" -chdir="$TF_DIR" output -raw gateway_id)"
SCHEMA_URI="$("$TF" -chdir="$TF_DIR" output -raw openapi_s3_uri)"
# ${REGION_ARGS[@]+...} below: an empty array is "unbound" under set -u in bash 3.2 (macOS).
REGION_ARGS=()
[[ -n "${AWS_REGION:-}" ]] && REGION_ARGS=(--region "$AWS_REGION")

TARGET_CONFIG="$(printf '{"mcp":{"openApiSchema":{"s3":{"uri":"%s"}}}}' "$SCHEMA_URI")"

echo "Gateway:  $GATEWAY_ID"
echo "Target:   $TARGET_NAME"
echo "Schema:   $SCHEMA_URI"

TARGET_ID="$(aws bedrock-agentcore-control list-gateway-targets \
  --gateway-identifier "$GATEWAY_ID" ${REGION_ARGS[@]+"${REGION_ARGS[@]}"} \
  --query "items[?name=='${TARGET_NAME}'].targetId | [0]" --output text)"

if [[ -z "$TARGET_ID" || "$TARGET_ID" == "None" ]]; then
  echo "Creating the target..."
  TARGET_ID="$(aws bedrock-agentcore-control create-gateway-target \
    --gateway-identifier "$GATEWAY_ID" ${REGION_ARGS[@]+"${REGION_ARGS[@]}"} \
    --name "$TARGET_NAME" \
    --description "Just-in-time access tools" \
    --target-configuration "$TARGET_CONFIG" \
    --query targetId --output text)"
else
  echo "Updating the target $TARGET_ID..."
  aws bedrock-agentcore-control update-gateway-target \
    --gateway-identifier "$GATEWAY_ID" ${REGION_ARGS[@]+"${REGION_ARGS[@]}"} \
    --target-id "$TARGET_ID" \
    --name "$TARGET_NAME" \
    --description "Just-in-time access tools" \
    --target-configuration "$TARGET_CONFIG" >/dev/null
fi

echo -n "Waiting for the target to be ready"
STATUS=""
for _ in $(seq 1 40); do
  STATUS="$(aws bedrock-agentcore-control get-gateway-target \
    --gateway-identifier "$GATEWAY_ID" ${REGION_ARGS[@]+"${REGION_ARGS[@]}"} \
    --target-id "$TARGET_ID" --query status --output text)"
  case "$STATUS" in
    READY) echo " ... ready."; break ;;
    *FAILED*) echo; echo "error: the target ended in status $STATUS." >&2; exit 1 ;;
  esac
  echo -n "."
  sleep 5
done

[[ "$STATUS" == "READY" ]] || { echo; echo "error: the target is not ready (status $STATUS)." >&2; exit 1; }

echo
echo "Done. In Copilot Studio, remove the MCP tool and add it again to load the new definition."
