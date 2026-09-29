"""Prepare private server files without logging or replacing durable credentials."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

ROOT = Path("/srv/tracebridge")
PRIVATE = ROOT / "private"
DATA = ROOT / "data"


def secret_file(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("x", encoding="utf-8") as stream:
            stream.write(value)
    os.chmod(path, 0o400)
    if os.name != "nt":
        os.chown(path, 10001, 10001)
    return path.read_text(encoding="utf-8").strip()


def prepare(settings, release, image, directory):
    if not re.fullmatch(r"[a-f0-9]{40}", release):
        raise ValueError("Invalid release revision")
    if not re.fullmatch(r"[0-9]{12}\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-z0-9/_-]+@sha256:[a-f0-9]{64}", image):
        raise ValueError("An immutable ECR image digest is required")
    domain = settings["domain"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]+\.[a-z]{2,}", domain) or ".." in domain:
        raise ValueError("Invalid DNS hostname")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,32}", settings["username"]):
        raise ValueError("Invalid operator username")
    PRIVATE.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    os.chmod(PRIVATE, 0o700)
    os.chmod(DATA, 0o700)
    if os.name != "nt":
        os.chown(DATA, 10001, 10001)
    password = secret_file(PRIVATE / "postgres-password", secrets.token_hex(32))
    secret_file(PRIVATE / "database-url", "postgresql+psycopg://tracebridge:" + password + "@postgres:5432/tracebridge")
    secret_file(DATA / "control-plane/operator-token", secrets.token_urlsafe(48))
    if os.name != "nt":
        os.chown(DATA / "control-plane", 10001, 10001)
        os.chmod(DATA / "control-plane", 0o700)
    hash_path = PRIVATE / "operator-password-hash"
    if not hash_path.exists():
        hashed = subprocess.run(["docker", "run", "--rm", 'caddy:2-alpine@sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b', "caddy", "hash-password",
            "--plaintext", settings["password"]], capture_output=True, text=True, timeout=60)
        if hashed.returncode or not re.fullmatch(r"\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}", hashed.stdout.strip()):
            raise RuntimeError("Password hash creation failed")
        secret_file(hash_path, hashed.stdout.strip())
    password_hash = hash_path.read_text(encoding="utf-8").strip()
    cookie = secret_file(PRIVATE / "cookie-secret", secrets.token_hex(32))
    env = {
        "TRACEBRIDGE_DOMAIN": domain,
        "CADDY_IMAGE": 'caddy:2-alpine@sha256:6aeddd44c3078b0f9a35206472a11420648a79c184603ef95957d0a20044cb2b',
        "ACME_EMAIL": "unused@example.invalid",
        "CADDY_USERNAME": settings["username"],
        "CADDY_PASSWORD_HASH": "'" + password_hash + "'",
        "STREAMLIT_SERVER_COOKIE_SECRET": cookie,
        "TRACEBRIDGE_DATA_DIR": str(DATA),
        "TRACEBRIDGE_IMAGE": image,
        "TRACEBRIDGE_REVISION": release,
        "TRACEBRIDGE_APP_ENV_FILE": str(PRIVATE / "app.env"),
        "TRACEBRIDGE_POSTGRES_PASSWORD_FILE": str(PRIVATE / "postgres-password"),
        "TRACEBRIDGE_DATABASE_URL_SECRET_FILE": str(PRIVATE / "database-url"),
    }
    destination = Path(directory) / ".env"
    destination.write_text("".join(k + "=" + v + "\n" for k, v in env.items()), encoding="utf-8")
    os.chmod(destination, 0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    if not args.settings.exists():
        args.settings.parent.mkdir(parents=True, exist_ok=True)
        with args.settings.open("x", encoding="utf-8") as stream:
            json.dump({"domain": "tracebridge.ckswhd.shop", "username": "owner", "password": secrets.token_urlsafe(32)}, stream)
        os.chmod(args.settings, 0o600)
    prepare(json.loads(args.settings.read_text(encoding="utf-8")), args.revision, args.image, args.directory)
    print(json.dumps({"private_runtime_ready": True, "revision": args.revision}))


if __name__ == "__main__":
    main()
