"""Run a real, isolated Linux Compose release check with generated credentials."""
import argparse
import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def run(args, *, capture=False):
    p = subprocess.run(args, cwd=ROOT, check=False, capture_output=capture, text=True)
    if p.returncode:
        raise RuntimeError("Deployment check command failed: " + args[0])
    return p.stdout.strip() if capture else None


def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    suffix = secrets.token_hex(6)
    project = "tracebridge-ci-" + suffix
    folder = (ROOT / "output" / project).resolve()
    assert folder.is_relative_to(ROOT / "output")
    folder.mkdir(parents=True, exist_ok=False)
    spec = importlib.util.spec_from_file_location("runtime_setup", ROOT / "deploy/aws/prepare_runtime.py")
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
    setup.ROOT, setup.PRIVATE, setup.DATA = folder, folder / "private", folder / "data"
    setup.PRIVATE.mkdir(mode=0o700)
    settings = {"domain": "localhost.test", "username": "check", "password": secrets.token_urlsafe(32)}
    # The runtime setup requires a DNS name; local Caddy is overridden below.
    verified_ref = "123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/check@sha256:" + "0" * 64
    setup.prepare(settings, args.revision, verified_ref, folder)
    setup.PRIVATE.joinpath("app.env").write_text("NVIDIA_API_KEY=\nTRACEBRIDGE_EMBEDDINGS_ENABLED=0\n")
    env = folder / ".env"
    text = env.read_text().replace("TRACEBRIDGE_IMAGE=" + verified_ref, "TRACEBRIDGE_IMAGE=" + args.image)
    text = text.replace("TRACEBRIDGE_DOMAIN=localhost.test", "TRACEBRIDGE_DOMAIN=localhost")
    env.write_text(text)
    tls_port = port()
    override = folder / "check.yaml"
    override.write_text("services:\n  proxy:\n    ports: !override\n      - '127.0.0.1:" + str(tls_port) + ":443'\n")
    command = ["docker", "compose", "--project-name", project, "--env-file", str(env)]
    for name in ["compose.yaml", "control-plane.compose.yaml", "postgres.compose.yaml", "compose.pilot.yaml"]:
        command += ["-f", str(ROOT / "deploy" / name)]
    command += ["-f", str(override)]
    def dc(*parts, capture=False):
        return run([*command, *parts], capture=capture)
    result = {"revision": args.revision, "image": args.image, "external_model_calls": 0}
    try:
        dc("config", "--quiet")
        dc("up", "-d", "--no-build", "--wait", "--wait-timeout", "240")
        dc("exec", "-T", "web", "python", "-c",
            "from streamlit.testing.v1 import AppTest; page=AppTest.from_file('/app/app.py',default_timeout=60).run(); assert not page.exception")
        first = dc("exec", "-T", "control", "python", "deploy/service_smoke.py", capture=True)
        result["http_smoke"] = json.loads(first)
        dc("restart", "control")
        for _ in range(45):
            try:
                dc("exec", "-T", "control", "python", "-c",
                    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=3).read()", capture=True)
                break
            except RuntimeError:
                time.sleep(2)
        else:
            raise RuntimeError("Control plane did not recover after restart")
        result["reopen"] = json.loads(dc("exec", "-T", "control", "python", "deploy/service_smoke.py", "--resume", capture=True))
        # Verify local HTTPS using Caddy's private test CA.
        root_cert = folder / "caddy-root.crt"
        for _ in range(30):
            try:
                dc("cp", "proxy:/data/caddy/pki/authorities/local/root.crt", str(root_cert))
                break
            except RuntimeError:
                time.sleep(1)
        context = ssl.create_default_context(cafile=str(root_cert))
        url = "https://localhost:" + str(tls_port)
        with urllib.request.urlopen(url, context=context, timeout=10) as response:
            assert response.status == 200 and b"TraceBridge" in response.read()
        with urllib.request.urlopen(urllib.request.Request(url + "/review/preview", data=b"", method="POST"), context=context, timeout=45) as response:
            demo = json.loads(response.read())
            assert demo["before"]["http_status"] == 500 and demo["after"]["http_status"] == 200
            assert demo["recovery"] == "PASSED" and demo["external_model_calls"] == 0
            result["reviewer_demo"] = demo
        try:
            urllib.request.urlopen(url + "/owner/", context=context, timeout=10)
            raise RuntimeError("Owner UI did not require authentication")
        except urllib.error.HTTPError as error:
            assert error.code == 401
        auth = base64.b64encode(("check:" + settings["password"]).encode()).decode()
        with urllib.request.urlopen(urllib.request.Request(url + "/owner/", headers={"Authorization": "Basic " + auth}), context=context, timeout=10) as response:
            assert response.status == 200
        try:
            urllib.request.urlopen(url + "/v1/projects", context=context, timeout=10)
            raise RuntimeError("Owner API did not require authentication")
        except urllib.error.HTTPError as error:
            assert error.code == 401, "Public proxy path failed: " + str(error.code)
        # Even malformed API traffic must be bounded before Python handles it.
        def malformed(_):
            try:
                with urllib.request.urlopen(url + "/v1/not-a-route", context=context, timeout=10) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                return error.code
        with ThreadPoolExecutor(max_workers=24) as pool:
            statuses = list(pool.map(malformed, range(120)))
        result["request_status_counts"] = dict(Counter(statuses))
        assert 429 in statuses, "API guard did not limit malformed traffic: " + str(Counter(statuses))
        ids = dc("ps", "-q", capture=True).splitlines()
        states = json.loads(run(["docker", "inspect", "--format", "{{json .State}}", ids[0]], capture=True))
        assert not states["OOMKilled"]
        result["https_auth"] = "PASSED"
        result["malformed_request_limit"] = "PASSED"
        result["containers"] = []
        for cid in ids:
            record = json.loads(run(["docker", "inspect", "--format",
                '{"name":{{json .Name}},"oom":{{json .State.OOMKilled}},"restarts":{{json .RestartCount}}}', cid], capture=True))
            assert not record["oom"] and record["restarts"] == 0
            result["containers"].append(record)
        stats = run(["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids], capture=True)
        result["memory_at_completion"] = [json.loads(line) for line in stats.splitlines()]
        (ROOT / "output/deployment-check.json").write_text(json.dumps(result, indent=2))
        print(json.dumps({"deployment_check": "PASSED", "external_model_calls": 0}))
    except Exception as error:
        result["deployment_check"] = "FAILED"
        result["error"] = str(error)
        (ROOT / "output/deployment-check.json").write_text(json.dumps(result, indent=2))
        # Synthetic CI stack only; never dump runtime environment or credentials.
        dc("logs", "--no-color", "--tail", "60", "guard")
        raise
    finally:
        dc("down", "--volumes", "--remove-orphans")
        assert folder.is_relative_to(ROOT / "output") and folder.name == project
        shutil.rmtree(folder)


if __name__ == "__main__":
    main()
