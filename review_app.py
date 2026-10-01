"""The downloadable, loopback-only reviewer companion."""
from datetime import datetime, timezone
import atexit
import hashlib
import json
import os
from pathlib import Path
import threading
from uuid import uuid4

import httpx
import streamlit as st

from tracebridge.local_runner import ControlClient, GatewayError, LocalRunner, pair_runner
from tracebridge.project_connection_ui import render_connection
from tracebridge.project_health import inspect_project, connection_checks
from tracebridge.project_profile import load_project_profile
from tracebridge.project_registry import save_profile
from tracebridge.project_repair import prepare_project_change, apply_project_change
from tracebridge.project_repair_ui import candidate_diff
from tracebridge.project_recovery import verify_project_recovery
from tracebridge.project_setup_ui import save_simple_project
from tracebridge.report_agent import investigate_submission
from tracebridge.report_contract import ReportContext
from tracebridge.reviewer_demo import create_demo, start_demo, DemoPatch

ROOT = Path(__file__).resolve().parent
RUNTIME = ROOT / "output"
REGISTRY = RUNTIME / "project-profiles"
RUNTIME.mkdir(exist_ok=True)
os.environ["TRACEBRIDGE_PROJECT_REGISTRY"] = str(REGISTRY)
os.environ["TRACEBRIDGE_ALLOW_PROJECT_REGISTRATION"] = "1"
CONFIG = json.loads((ROOT / "review-connection.json").read_text(encoding="utf-8"))
PROJECT = CONFIG["project_id"]
api = ControlClient(CONFIG["url"], CONFIG["token"])
DB = RUNTIME / "local-incidents.sqlite3"
st.set_page_config(page_title="TraceBridge 심사 체험", page_icon="🔎", layout="wide")
st.title("내 PC에서 실제 버그 해결 체험")
st.caption("예제 앱 실행 → 실제 실패 요청 → 조사 → 테스트·diff 확인 → 수정 적용 → API 회복 확인")


@st.cache_resource
def runner_resource(config_path):
    worker = LocalRunner.from_config(Path(config_path))
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()
    return worker


def connect():
    path = RUNTIME / "runner/config.json"
    if not path.is_file():
        code = api.request("POST", "/review/pair")["code"]
        pair_runner(CONFIG["url"], code, path)
    return runner_resource(str(path))


def local_status(profile):
    health = inspect_project(profile)
    return {"project_id": PROJECT, "online": False, "service_ids": [profile.service], "default_service": profile.service,
        "profile_sha256": hashlib.sha256(profile.config_path.read_bytes()).hexdigest(),
        "service_health": {row["id"]: row["service_status"] for row in health["services"]},
        "connection_checks": connection_checks(health), "health_checked_at": datetime.now(timezone.utc).isoformat()}


demo_state = RUNTIME / "demo.json"
demo = json.loads(demo_state.read_bytes()) if demo_state.is_file() else None
profile_path = REGISTRY / (PROJECT + ".json")
profile = load_project_profile(profile_path) if profile_path.is_file() else None
is_demo = bool(profile and demo and str(profile.root) == demo["root"])
left, right = st.columns([1.8, 1], gap="large")
with left:
    st.subheader("1. 프로젝트 연결")
    demo_tab, project_tab = st.tabs(["버그 예제 체험", "내 프로젝트 등록"])
    with demo_tab:
        st.write("**최대 5개 신청** 앱에는 경계값 버그가 하나 있습니다. 바탕화면의 새 폴더에 생성하고 실행합니다.")
        if st.button("버그 예제 다운로드·실행" if not demo else "예제 앱 다시 연결·실행", type="primary", key="start-demo"):
            try:
                with st.spinner("예제 프로젝트와 앱을 준비하고 있습니다."):
                    if demo is None:
                        created, url = create_demo(PROJECT, registry=REGISTRY)
                        demo = {"root": str(created.root), "url": url, "profile": str(created.config_path)}
                        demo_state.write_text(json.dumps(demo), encoding="utf-8")
                    elif not is_demo:
                        raise ValueError("현재 다른 프로젝트가 등록돼 있습니다. 새 실행기를 내려받아 별도 체험을 시작해 주세요.")
                    created = load_project_profile(Path(demo["profile"]))
                    process = start_demo(created, demo["url"], RUNTIME)
                    if process:
                        st.session_state.demo_process = process
                        atexit.register(process.terminate)
                    try:
                        connect()
                        st.session_state.pop("connection_error", None)
                    except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
                        st.session_state.connection_error = str(exc)
                    st.rerun()
            except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
                st.error(str(exc))
        if is_demo:
            st.caption("예제 프로젝트: " + str(profile.root))
            st.link_button("실행 중인 예제 앱 열기", demo["url"])
            if st.session_state.get("demo_process") and st.button("예제 앱 종료", key="stop-demo"):
                process = st.session_state.demo_process
                process.terminate()
                process.wait(timeout=10)
                del st.session_state.demo_process
                st.rerun()
        st.caption("기본 체험: 준비된 예제 패치 + 실제 검사. AI 모델을 호출하지 않습니다.")
    with project_tab:
        st.caption("폴더 경로는 이 PC에서 읽습니다. 파일 수정·명령 실행 권한은 예제에만 등록됩니다.")
        with st.form("register-project"):
            name = st.text_input("프로젝트 이름", value=profile.display_name if profile and not is_demo else "")
            root = st.text_input("로컬 프로젝트 폴더 전체 경로", value=str(profile.root) if profile and not is_demo else "", placeholder="C:/work/my-project")
            url = st.text_input("서비스 실행 주소 (선택)", placeholder="http://127.0.0.1:3000/")
            log = st.text_input("요청 로그 파일 (선택, 프로젝트 안 상대 경로)", placeholder="logs/api.jsonl")
            save = st.form_submit_button("프로젝트 등록·PC 연결", disabled=is_demo)
        if save:
            try:
                saved = save_simple_project(name, root, url, profile=profile)
                # A reviewer capability is scoped to the unique ID from this download.
                data = json.loads(saved.config_path.read_bytes())
                if saved.project_id != PROJECT:
                    saved.config_path.unlink()
                data["project_id"] = PROJECT
                if log.strip():
                    data["log_sources"] = [{"id": "requests", "path": log.strip(), "format": "jsonl"}]
                save_profile(data, REGISTRY)
                st.session_state.pop("source", None)
                st.session_state.pop("candidate", None)
                st.session_state.pop("application", None)
                st.session_state.pop("recovery", None)
                connect()
                st.rerun()
            except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
                st.error(str(exc))
        if is_demo:
            st.caption("내 프로젝트를 연결하려면 사이트에서 새 실행기를 내려받아 별도 체험을 시작하세요.")
    if profile:
        st.subheader("2. 문제 확인·조사")
        if is_demo and st.button("5개 신청 버그 재현·조사", key="reproduce"):
            try:
                response = httpx.post(demo["url"] + "/quotas", json={"count": 5}, timeout=3)
                observed = response.json()
                if response.status_code != 500:
                    st.info("현재 5개 신청은 이미 성공합니다. 수정 후 확인 버튼으로 결과를 확인하세요.")
                else:
                    text = "5개까지 신청 가능하다고 되어 있는데 5개 신청이 거절돼요. QUOTA_BOUNDARY requestId=" + observed["request_id"]
                    with st.spinner("실제 HTTP 실패와 같은 요청 로그·코드를 대조하고 있습니다."):
                        source = investigate_submission(text, context=ReportContext(occurred_at=observed["occurred_at"]), project_profile=profile, db_path=DB,
                            use_nvidia=False)
                    st.session_state.source = source
                    for key in ("candidate", "application", "recovery"):
                        st.session_state.pop(key, None)
                    st.session_state.observed = {"http_status": response.status_code, **observed}
                    st.rerun()
            except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
                st.error(str(exc))
        if observed := st.session_state.get("observed"):
            st.error("수정 전 실제 요청: HTTP " + str(observed["http_status"]) + " · " + observed["message"])
        if source := st.session_state.get("source"):
            st.write(source["summary"])
            st.caption("같은 요청 연결: " + source["correlation"] + " · 외부 모델 호출: " + str(source["model_calls"]))
            with st.expander("요청·코드 근거"):
                st.json(source)
        with st.expander("증상 글로 조사하기 / 선택적 NVIDIA 분석", expanded=not is_demo):
            report_text = st.text_area("어떤 동작에서 어떤 문제가 발생했나요?", max_chars=4000)
            occurred = st.text_input("발생 시각 (선택)", placeholder="2026-10-01T10:30:00+09:00")
            nvidia = st.checkbox("제보와 필요한 코드·로그를 서버 및 NVIDIA 분석 서비스에 보내 AI로 조사", value=False)
            st.caption("AI 조사는 서버에 모델 키가 설정된 경우 사용할 수 있습니다. 체크하지 않으면 이 PC에서 조사합니다.")
            if st.button("증상 조사", disabled=not report_text.strip()):
                try:
                    context = ReportContext(occurred_at=occurred or None)
                    if nvidia:
                        connect()
                        job = api.request("POST", "/review/reports", {"text": report_text, "context": context.model_dump(mode="json", exclude_none=True), "use_nvidia": True}, idempotency_key=uuid4().hex)
                        st.session_state.cloud_job = job["job_id"]
                    else:
                        st.session_state.source = investigate_submission(report_text, context=context, project_profile=profile, db_path=DB)
                    st.rerun()
                except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
                    st.error(str(exc))
            if job_id := st.session_state.get("cloud_job"):
                if st.button("AI 조사 진행 상태 확인"):
                    try:
                        status = api.request("GET", "/review/jobs/" + job_id)
                        st.write(status["state"])
                        if status.get("result"):
                            st.json(status["result"])
                    except (RuntimeError, httpx.HTTPError) as exc:
                        st.error(str(exc))
        if is_demo and st.session_state.get("source"):
            st.subheader("3. 수정 전후 검사·diff 확인")
            if st.button("예제 수정안 검증", disabled=bool(st.session_state.get("candidate")), key="prepare-demo"):
                try:
                    with st.spinner("별도 사본에서 실패 재현·수정 후 검사·회귀 검사를 실행합니다."):
                        st.session_state.candidate = prepare_project_change(st.session_state.source, profile, db_path=DB, proposer=DemoPatch())
                    st.rerun()
                except (OSError, ValueError, RuntimeError) as exc:
                    st.error(str(exc))
            if candidate := st.session_state.get("candidate"):
                job = candidate["job"]
                st.write("검증 결과: " + job["status"])
                st.table([{"검사": {"before": "수정 전 재현", "after": "수정 후 같은 검사", "regression": "별도 회귀 검사"}.get(row["phase"], row["phase"]),
                    "결과": row["status"], "종료 코드": row["exit_code"]} for row in job["checks"]])
                st.caption("수정안: 준비된 예제 패치 · 외부 모델 호출 0회 · 검사 파일 수정 불가")
                if job.get("diff"):
                    st.code(candidate_diff(job), language="diff")
                st.subheader("4. 예제에 적용·실제 API 재확인")
                reviewed = st.checkbox("diff와 검사 결과를 확인했고, 예제 폴더의 app.py에 적용합니다", key="review-diff")
                if st.button("예제에 수정 적용", disabled=not reviewed or not job["candidate_fix_verified"] or bool(st.session_state.get("application")), key="apply-demo"):
                    try:
                        st.session_state.application = apply_project_change(PROJECT, job["work_id"], profile, db_path=DB, expected_diff_sha256=job["diff"]["sha256"])
                        st.rerun()
                    except (OSError, ValueError, RuntimeError) as exc:
                        st.error(str(exc))
                if st.session_state.get("application"):
                    st.success("예제 원본에 수정이 적용됐습니다. 실제 API 동작을 확인해 주세요.")
                    if st.button("5개 신청·회귀 API 회복 확인", key="verify-demo"):
                        try:
                            with st.spinner("적용한 소스 버전의 실제 API 응답을 두 차례 확인합니다."):
                                st.session_state.recovery = verify_project_recovery(profile, job["work_id"], db_path=DB)
                            st.rerun()
                        except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
                            st.error(str(exc))
                if recovery := st.session_state.get("recovery"):
                    verification = recovery["verification"]
                    if verification["status"] == "PASSED":
                        st.success("버그 해결 확인: 5개 신청 성공 · 회귀 API 통과 · 적용한 소스 버전 일치")
                        st.balloons()
                    else:
                        st.warning("API 회복 확인: " + verification["status"])
                    st.table(verification["checks"])
with right:
    st.subheader("PC 연결·서비스 상태")
    st.caption("PC 연결과 앱 실행, 버그 해결은 각각 확인합니다.")
    if profile:
        if st.button("상태 다시 확인", key="review-refresh"):
            st.rerun()
        try:
            remote = api.request("GET", "/review/project")
        except (RuntimeError, httpx.HTTPError) as exc:
            remote = None
            st.warning("배포 서버 연결을 확인하지 못했습니다. 로컬 예제 체험은 계속할 수 있습니다.")
        snapshot = local_status(profile)
        snapshot["online"] = bool(remote and remote["online"] and remote["profile_sha256"] == snapshot["profile_sha256"])
        render_connection(st, snapshot, compact=True)
        if not snapshot["online"] and st.button("PC 실행기 연결·재시도"):
            try:
                connect()
                st.rerun()
            except (RuntimeError, ValueError, OSError, httpx.HTTPError) as exc:
                st.error(str(exc))
    else:
        st.info("왼쪽에서 예제를 실행하거나 내 프로젝트를 등록하세요.")
    if error := st.session_state.get("connection_error"):
        st.warning("서버 연결: " + error)
    st.write("**체험에서 확인할 것**")
    st.write("1. 예제 앱 실행과 HTTP 500 실패\n2. 실제 로그·코드와 요청 연결\n3. 수정 전 실패 → 수정 후·회귀 성공\n4. diff 검토 후 원본 적용\n5. 실제 API와 실행 소스 버전 재확인")
    st.caption("Windows / Python 3.12. 기본 체험은 AI 성능 평가가 아닙니다. 로컬 화면과 앱은 127.0.0.1에서만 실행합니다.")
