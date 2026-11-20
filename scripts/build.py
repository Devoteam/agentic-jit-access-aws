#!/usr/bin/env python3
"""Build everything Terraform deploys.

Produces, in ``build/``:

* ``layer.zip``           the dependencies, as Linux arm64 wheels for Python 3.12
* ``function.zip``        the ``jit_access`` package, used by all five functions
* ``openapi.json``        the API's OpenAPI document, generated from the code
* ``openapi.flat.json``   the same, flattened for Microsoft Copilot Studio

The zip files are deterministic (fixed timestamps, sorted entries), so a rebuild
without changes gives the same hash and Terraform does not redeploy anything.

Usage:
    python scripts/build.py
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
SRC = ROOT / "src"
REQUIREMENTS = ROOT / "requirements.txt"

FIXED_TIME = (1980, 1, 1, 0, 0, 0)
IGNORED_DIRECTORIES = {"__pycache__"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}


def deterministic_zip(source: Path, target: Path, folder: str | None = None) -> None:
    """Zip ``source`` into ``target``. Entries are placed under ``folder`` if given."""
    target.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(
        path
        for path in source.rglob("*")
        if path.is_file()
        and not (set(path.relative_to(source).parts) & IGNORED_DIRECTORIES)
        and path.suffix not in IGNORED_SUFFIXES
    )
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            name = path.relative_to(source).as_posix()
            if folder:
                name = f"{folder}/{name}"
            info = zipfile.ZipInfo(name, FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            executable = path.stat().st_mode & 0o111
            info.external_attr = (0o755 if executable else 0o644) << 16
            archive.writestr(info, path.read_bytes())
    print(f"built {target.relative_to(ROOT)} ({len(files)} files, {target.stat().st_size // 1024} KB)")


def build_layer() -> None:
    layer_dir = BUILD / "layer"
    packages = layer_dir / "python"
    stamp = layer_dir / ".requirements.sha256"
    digest = hashlib.sha256(REQUIREMENTS.read_bytes()).hexdigest()

    if stamp.exists() and stamp.read_text() == digest and (BUILD / "layer.zip").exists():
        print("layer is up to date")
        return

    shutil.rmtree(layer_dir, ignore_errors=True)
    packages.mkdir(parents=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--quiet",
            "--requirement",
            str(REQUIREMENTS),
            "--target",
            str(packages),
            "--platform",
            "manylinux2014_aarch64",
            "--platform",
            "manylinux_2_28_aarch64",
            "--only-binary=:all:",
            "--python-version",
            "3.12",
            "--implementation",
            "cp",
            "--upgrade",
        ],
        check=True,
    )
    deterministic_zip(layer_dir, BUILD / "layer.zip")
    stamp.write_text(digest)


def build_function() -> None:
    deterministic_zip(SRC, BUILD / "function.zip")


def build_openapi() -> None:
    scripts = ROOT / "scripts"
    environment = {**os.environ, "PYTHONWARNINGS": "ignore"}
    for command in (
        [sys.executable, str(scripts / "generate_openapi.py"), str(BUILD / "openapi.json")],
        [
            sys.executable,
            str(scripts / "flatten_openapi.py"),
            str(BUILD / "openapi.json"),
            str(BUILD / "openapi.flat.json"),
        ],
    ):
        subprocess.run(command, check=True, env=environment)


def main() -> int:
    BUILD.mkdir(exist_ok=True)
    build_layer()
    build_function()
    build_openapi()
    return 0


if __name__ == "__main__":
    sys.exit(main())
