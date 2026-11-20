.DEFAULT_GOAL := help

PYTHON ?= .venv/bin/python
# Use OpenTofu instead with: make TF=tofu plan
TF     ?= terraform
TF_DIR := infra/terraform

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

.PHONY: venv
venv: ## Create a Python 3.12 virtual environment with the dev dependencies
	python3.12 -m venv .venv
	$(PYTHON) -m pip install --quiet --upgrade pip
	$(PYTHON) -m pip install --quiet -r requirements-dev.txt

.PHONY: test
test: ## Run the tests
	$(PYTHON) -m pytest

.PHONY: lint
lint: ## Check style
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .

.PHONY: fmt
fmt: ## Fix style
	.venv/bin/ruff check --fix .
	.venv/bin/ruff format .

.PHONY: build
build: ## Build the Lambda packages and the OpenAPI document
	$(PYTHON) scripts/build.py

.PHONY: init
init: ## terraform init
	$(TF) -chdir=$(TF_DIR) init

.PHONY: plan
plan: build ## Show what would change
	$(TF) -chdir=$(TF_DIR) plan

.PHONY: apply
apply: build ## Deploy
	$(TF) -chdir=$(TF_DIR) apply

.PHONY: publish
publish: ## Create or update the AgentCore Gateway target (run after apply, and after every API change)
	./scripts/publish_gateway_target.sh

.PHONY: clean
clean: ## Remove build output
	rm -rf build
