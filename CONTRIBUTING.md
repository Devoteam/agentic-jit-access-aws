# Contributing

Thanks for helping. This project controls access to AWS accounts, so changes are reviewed with care and
every behavior change comes with a test.

## Set up

You need Python 3.12 and, to work on the infrastructure, Terraform 1.6+ or OpenTofu.

```bash
make venv     # virtual environment with the dev dependencies
make test     # unit tests
make lint     # ruff
make build    # Lambda packages and the OpenAPI document
```

Infrastructure tests plan the whole configuration against a mocked AWS provider, so they need no credentials:

```bash
make build
cd infra/terraform
tofu init -backend=false     # or: terraform init -backend=false
tofu test
```

## Guidelines

- **Keep the rules in the API.** Who may approve, whether self-approval is allowed, how long access lasts:
  these belong in `src/jit_access/api` and are tested there. Never enforce a rule only in the agent's
  instructions.
- **Keep the agent's tools flat.** Microsoft Copilot Studio drops tools whose schema has `$ref` and fills
  nested objects unreliably. `tests/test_openapi.py` guards this.
- **Keep workflow tasks idempotent.** Step Functions retries them. Save every created resource to the request
  record immediately, so cleanup always knows what exists.
- **Never log a token.** Do not log whole events in functions that receive an `Authorization` header.
- **Do not add `from __future__ import annotations` to `src/jit_access/api/app.py`.** Powertools resolves the
  route types at runtime and breaks.

## Pull requests

1. Add or update tests. A bug fix starts with a failing test.
2. Run `make lint test`, and the infrastructure tests if you touched `infra/`.
3. Describe what changed and why, and what you did not verify. Changes that touch the workflow or the
   Identity Center permissions should say how they were tested against a real account.
