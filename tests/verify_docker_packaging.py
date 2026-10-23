#!/usr/bin/env python3
"""Verify Docker context and image isolation with synthetic credentials only.

Run: python3 tests/verify_docker_packaging.py
Requires Python 3, Docker with BuildKit, and a running Docker daemon.
The temporary context contains only reviewed application inputs and fixtures.
No local .env is read, and the Shopify server is never started.
"""

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid


ROOT = Path(__file__).resolve().parents[1]
APP_FILES = {"server.py", "requirements.txt"}
FIXTURES = (
    ".env", ".env.local", ".env.production", ".env.example", "env.example",
    "config/.env", "config/.env.production", "shopify.env",
    "credentials.json", "secrets/shopify.json", ".aws/credentials",
    ".ssh/id_rsa", "keys/private.pem", "keys/private.key",
    ".git/config", ".venv/lib/cache.py", "venv/lib/cache.py",
    "__pycache__/server.pyc", ".pytest_cache/cache", ".mypy_cache/cache",
    ".ruff_cache/cache", "node_modules/cache", "build/output",
    "dist/output.whl", "logs/server.log", ".coverage", "coverage.xml",
    "backup.zip", "unknown-local-file.txt",
)


def run(*args, **kwargs):
    return subprocess.run(args, check=True, **kwargs)


def contains_marker(path, marker):
    """Scan saved image config and every layer, including deleted files."""
    with path.open("rb") as stream:
        tail = b""
        while chunk := stream.read(1024 * 1024):
            data = tail + chunk
            if marker in data:
                return True
            tail = data[-(len(marker) - 1):]
    return False


def main():
    if shutil.which("docker") is None:
        raise SystemExit("FAIL: Docker is required; no image verification performed.")
    run("docker", "info", stdout=subprocess.DEVNULL)
    marker = ("DUMMY_SHOPIFY_PACKAGING_" + uuid.uuid4().hex).encode()
    tag = "shopify-packaging-test:" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="shopify-packaging-") as temp:
        work = Path(temp)
        context = work / "context"
        context.mkdir()
        for name in sorted(APP_FILES | {"Dockerfile", ".dockerignore"}):
            source = ROOT / name
            if source.is_symlink():
                raise AssertionError(f"Build input must not be a symlink: {name}")
            shutil.copyfile(source, context / name)
        for name in FIXTURES:
            target = context / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"SHOPIFY_ACCESS_TOKEN=" + marker + b"\n")

        # Let Docker apply its own ignore rules; do not emulate pattern matching.
        exported = work / "filtered-context"
        run(
            "docker", "build", "--no-cache", "--output",
            f"type=local,dest={exported}", "-f", "-", str(context),
            input="FROM scratch\nCOPY . /\n", text=True,
        )
        actual = {
            p.relative_to(exported).as_posix()
            for p in exported.rglob("*") if p.is_file() or p.is_symlink()
        }
        if actual != APP_FILES:
            raise AssertionError(f"Unexpected context contents: {sorted(actual)}")
        print(f"PASS: Docker context contains only {sorted(APP_FILES)}", flush=True)

        try:
            run("docker", "build", "--no-cache", "-t", tag, str(context))
            archive = work / "image.tar"
            run("docker", "image", "save", "-o", str(archive), tag)
            if contains_marker(archive, marker):
                raise AssertionError("Dummy credential found in saved image layers/config")
            print("PASS: dummy credentials absent from all saved image layers/config", flush=True)

            # Check final app contents and installed dependencies without importing
            # server.py, starting its entrypoint, or permitting network traffic.
            check = (
                "import ast, pathlib, httpx, pydantic, uvicorn; "
                "from mcp.server.fastmcp import FastMCP; "
                "p=pathlib.Path('/app'); "
                f"assert {{x.name for x in p.iterdir()}} == {APP_FILES!r}; "
                "ast.parse((p/'server.py').read_text()); "
                "print('PASS: final app files, Python syntax and dependencies')"
            )
            run("docker", "run", "--rm", "--network", "none",
                "--entrypoint", "python", tag, "-B", "-c", check)
            metadata = json.loads(subprocess.check_output(
                ["docker", "image", "inspect", tag], text=True
            ))[0]["Config"]
            if metadata["Cmd"] != ["python", "server.py"] or metadata["WorkingDir"] != "/app":
                raise AssertionError("Application startup configuration changed")
            print("PASS: image startup configuration preserved", flush=True)
        finally:
            subprocess.run(
                ["docker", "image", "rm", "-f", tag],
                stdout=subprocess.DEVNULL, check=False,
            )


if __name__ == "__main__":
    main()
