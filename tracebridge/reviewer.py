"""Public welcome and expiring reviewer capabilities, separate from owner access."""
import base64
from collections import deque
from functools import lru_cache
import hashlib
import hmac
from io import BytesIO
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from .storage import schema as tables

ROOT = Path(__file__).resolve().parents[1]


def sign_review(project_id, expires, secret):
    payload = base64.urlsafe_b64encode(json.dumps([project_id, expires]).encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), ("review:" + payload).encode(), hashlib.sha256).hexdigest()
    return payload + "." + signature


def read_review(token, secret):
    try:
        payload, signature = token.split(".")
        expected = hmac.new(secret.encode(), ("review:" + payload).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError()
        project, expires = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if not isinstance(project, str) or not project.startswith("review-") or len(project) != 31 or type(expires) is not int or expires <= time.time():
            raise ValueError()
        return project
    except (ValueError, TypeError, KeyError):
        raise HTTPException(401, "체험 연결이 만료됐습니다. 사이트에서 새 실행기를 내려받아 주세요.") from None


@lru_cache(maxsize=1)
def companion_sources():
    """Explicit source allowlist; never package local configuration or output."""
    paths = [ROOT / "review_app.py", ROOT / "scripts/review_bootstrap.py",
             ROOT / "requirements-review.txt"]
    paths += sorted((ROOT / "tracebridge").rglob("*.py"))
    paths += sorted((ROOT / "examples/reviewer_demo").glob("*"))
    return {p.relative_to(ROOT).as_posix(): p.read_bytes() for p in paths
            if p.is_file() and not p.is_symlink() and "__pycache__" not in p.parts}


def companion_zip(url, project_id, token):
    stream = BytesIO()
    with ZipFile(stream, "w", ZIP_DEFLATED) as archive:
        for name, data in companion_sources().items():
            archive.writestr("TraceBridge-Review/" + name, data)
        archive.writestr("TraceBridge-Review/review-connection.json", json.dumps({"url": url, "project_id": project_id, "token": token}))
        archive.writestr("TraceBridge-Review/START.cmd", '@echo off\r\ncd /d "%~dp0"\r\nwhere py >nul 2>nul\r\nif errorlevel 1 (\r\n  python scripts\\review_bootstrap.py\r\n) else (\r\n  py -3.12 scripts\\review_bootstrap.py\r\n)\r\nif errorlevel 1 pause\r\n')
        archive.writestr("TraceBridge-Review/READ-ME.txt", "Windows / Python 3.12\nZIP을 풀고 START.cmd를 실행하세요.\n처음 한 번 필요한 패키지를 설치하고 로컬 웹 화면을 엽니다.\n바탕화면의 새 TraceBridge-Review 폴더에 설치하며 기존 프로젝트를 덮어쓰지 않습니다.\n화면의 예제 시작 버튼으로 실제 버그가 있는 앱을 실행합니다.\n키 없는 기본 체험은 고정 예제 패치와 실제 테스트를 사용합니다. AI 성능 평가가 아닙니다.\n")
    return stream.getvalue()


def install_review_routes(app, *, secret, connect, projects, report, changes, job_status, recovery, report_type, recovery_type):
    issued = deque()
    previews = deque()
    preview_slots = threading.BoundedSemaphore(2)
    lock = threading.Lock()

    def identity(authorization):
        if not authorization or not authorization.startswith("Bearer ") or len(authorization) > 512:
            raise HTTPException(401, "체험 연결 정보가 필요합니다.")
        return read_review(authorization[7:], secret)

    def authorize_job(project, job_id):
        with connect() as db:
            if not db.one(tables.jobs, id=job_id, project_id=project):
                raise HTTPException(404, "체험 작업을 찾지 못했습니다.")

    def quota(project):
        with connect() as db:
            if sum(db.counts(project).values()) >= 12:
                raise HTTPException(429, "체험당 12회까지 조사할 수 있습니다. 새 체험을 시작해 주세요.")

    @app.get("/welcome", response_class=HTMLResponse)
    def welcome():
        return HTMLResponse((ROOT / "deploy/welcome.html").read_text(encoding="utf-8"), headers={"Cache-Control": "no-store"})

    @app.post("/review/preview")
    def preview(request: Request):
        address = request.client.host if request.client else "unknown"
        now = time.time()
        with lock:
            while previews and previews[0][0] < now - 3600:
                previews.popleft()
            if len(previews) >= 100 or sum(ip == address for _, ip in previews) >= 12:
                raise HTTPException(429, "체험 요청이 많습니다. 잠시 후 다시 실행해 주세요.")
            previews.append((now, address))
        if not preview_slots.acquire(blocking=False):
            raise HTTPException(429, "다른 체험을 실행 중입니다. 잠시 후 다시 실행해 주세요.")
        try:
            env = {k: v for k, v in os.environ.items() if k.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "APPDATA", "LOCALAPPDATA", "USERPROFILE"}}
            env.update(PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            result = subprocess.run([sys.executable, "-m", "scripts.run_reviewer_demo"], cwd=ROOT, env=env,
                capture_output=True, text=True, encoding="utf-8", timeout=45,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            if result.returncode:
                raise HTTPException(503, "예제 실행을 완료하지 못했습니다. 잠시 후 재시도하거나 PC 체험을 이용하세요.")
            return json.loads(result.stdout)
        except (subprocess.TimeoutExpired, ValueError, TypeError):
            raise HTTPException(503, "예제 실행 시간이 초과됐습니다. 다시 시도해 주세요.") from None
        finally:
            preview_slots.release()

    @app.post("/review/download")
    def download(request: Request):
        address = request.client.host if request.client else "unknown"
        now = time.time()
        with lock:
            while issued and issued[0][0] < now - 3600:
                issued.popleft()
            if len(issued) >= 100 or sum(ip == address for _, ip in issued) >= 8:
                raise HTTPException(429, "실행기 다운로드가 많습니다. 잠시 후 다시 시도해 주세요.")
            issued.append((now, address))
        project = "review-" + secrets.token_hex(12)
        token = sign_review(project, int(now + 86400), secret)
        raw = companion_zip(str(request.base_url).rstrip("/"), project, token)
        return Response(raw, media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="TraceBridge-Review.zip"', "Cache-Control": "no-store"})

    @app.post("/review/pair")
    def pair(authorization: str | None = Header(default=None)):
        project = identity(authorization)
        with connect() as db:
            bound = db.one(tables.bindings, project_id=project)
            if bound:
                raise HTTPException(409, "이미 연결된 체험입니다. 기존 실행기를 다시 실행하거나 새 체험을 내려받아 주세요.")
            code = secrets.token_urlsafe(32)
            db.delete(tables.pairings, projects=json.dumps([project]))
            db.insert(tables.pairings, digest=hashlib.sha256(code.encode()).hexdigest(), projects=json.dumps([project]), expires=time.time() + 300)
        return {"code": code, "expires_in_seconds": 300}

    @app.get("/review/project")
    def project_status(authorization: str | None = Header(default=None)):
        project = identity(authorization)
        return next((item for item in projects() if item["project_id"] == project), None)

    @app.get("/review/jobs/{job_id}")
    def status(job_id: str, authorization: str | None = Header(default=None)):
        project = identity(authorization)
        authorize_job(project, job_id)
        return job_status(job_id)

    @app.post("/review/reports", status_code=202)
    def review_report(body: dict, authorization: str | None = Header(default=None), idempotency_key: str | None = Header(default=None)):
        project = identity(authorization)
        quota(project)
        try:
            parsed = report_type.model_validate(body)
        except ValueError:
            raise HTTPException(422, "증상과 발생 시각을 확인해 주세요.") from None
        if parsed.previous_job_id:
            authorize_job(project, parsed.previous_job_id)
        return report(project, parsed, idempotency_key)

    @app.post("/review/changes", status_code=202)
    def review_changes(body: dict, authorization: str | None = Header(default=None), idempotency_key: str | None = Header(default=None)):
        project = identity(authorization)
        quota(project)
        try:
            parsed = report_type.model_validate(body)
        except ValueError:
            raise HTTPException(422, "수정 요청을 확인해 주세요.") from None
        # Anonymous model-generated code must use the existing Docker boundary.
        binding = next((item for item in projects() if item["project_id"] == project), None)
        if not binding or binding.get("repair_execution_modes", {}).get(parsed.policy_id) != "DOCKER":
            raise HTTPException(422, "AI 수정은 Docker 검사 정책을 등록한 프로젝트에서 사용할 수 있습니다. 기본 예제는 로컬 검증 체험을 이용하세요.")
        if not parsed.previous_job_id:
            raise HTTPException(422, "조사를 먼저 실행해 주세요.")
        authorize_job(project, parsed.previous_job_id)
        return changes(project, parsed, idempotency_key)

    @app.post("/review/recovery-checks", status_code=202)
    def review_recovery(body: dict, authorization: str | None = Header(default=None), idempotency_key: str | None = Header(default=None)):
        project = identity(authorization)
        quota(project)
        try:
            parsed = recovery_type.model_validate(body)
        except ValueError:
            raise HTTPException(422, "회복 검사 요청을 확인해 주세요.") from None
        authorize_job(project, parsed.candidate_job_id)
        return recovery(project, parsed, idempotency_key)
