#!/usr/bin/env python3
"""Generate the OpenAPI document of the access API straight from the code.

The document defines the agent's tools, so it must always match the deployed
code. Generating it here, at build time, means there is no running API to
download it from and no way for the two to drift apart.

The server URL is a placeholder. Terraform replaces it with the real API
Gateway URL when it uploads the document for the gateway to read.

Usage:
    python scripts/generate_openapi.py build/openapi.json
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from jit_access.api.app import openapi_document  # noqa: E402

PLACEHOLDER_SERVER = "https://api.example.com"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    target = Path(argv[1])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(openapi_document(PLACEHOLDER_SERVER) + "\n", encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
