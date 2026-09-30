"""Simple owner setup and a scoped companion runner on the web host PC."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4
from urllib.parse import urlsplit
import httpx

from .project_profile import load_project_profile, select_project_service
from .project_registry import ROOT, profile_data, registry_directory, save_profile


def save_simple_project(name, root, url="", *, profile=None, expected_sha256=None, reserved_ids=()):
    name, root, url = name.strip(), root.strip(), url.strip()
    if url:
        parsed = urlsplit(url)
        if not parsed.path:
            url = parsed._replace(path="/").geturl()
    if not name or len(name) > 80:
        raise ValueError("프로젝트 이름을 1~80자로 입력해 주세요")
    if not root or "://" in root or root.startswith(("\\\\", "//")):
        raise ValueError("이 컴퓨터에 있는 프로젝트 폴더를 입력해 주세요")
    if not Path(root).is_absolute():
        raise ValueError("프로젝트 폴더의 전체 경로를 입력해 주세요")
    data = profile_data(profile) if profile else {}
    if profile is None:
        identifier = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-")[:48] or "project-" + uuid4().hex[:8]
        if identifier in reserved_ids or (registry_directory() / (identifier + ".json")).exists():
            raise ValueError("같은 이름의 프로젝트가 있습니다. 목록에서 선택해 주세요")
        data.update(project_id=identifier, service="app", environment="dev", code_roots=["."], log_sources=[], policy_refs=[])
    elif root != str(profile.root):
        data["code_roots"] = [str(Path(root) / path.relative_to(profile.root)) for path in profile.code_roots]
    data.update(root=root, display_name=name)
    if data.get("services"):
        entry = next(item for item in data["services"] if item["id"] == data["service"])
    else:
        entry = data
    entry.pop("health_url", None)
    if url:
        entry["health_url"] = url
    return load_project_profile(save_profile(data, expected_sha256=expected_sha256))


def _running(pid):
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False


def connect_local_project(api, profile, *, directory=None):
    """Registration authorizes only this project; existing credentials stay intact."""
    from .local_runner import pair_runner
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile.project_id):
        raise ValueError("프로젝트 ID를 확인해 주세요")
    directory = Path(directory or ROOT / "output/project-runners") / profile.project_id
    directory.mkdir(parents=True, exist_ok=True)
    config = directory / "config.json"
    bound = next((item for item in api.request("GET", "/v1/projects") if item["project_id"] == profile.project_id), None)
    if not config.exists() and bound:
        for candidate in [ROOT / "output/local-service/runner/config.json", ROOT / "output/local-runner/config.json"]:
            if candidate.is_file():
                previous = json.loads(candidate.read_bytes())
                if previous.get("runner_id") == bound["runner_id"] and previous.get("project_ids") == [profile.project_id] and previous.get("url") == api.url:
                    config, directory = candidate, candidate.parent
                    break
        if not config.exists():
            raise ValueError("이미 연결된 프로젝트입니다. 기존 컴퓨터에서 연결을 확인해 주세요")
    if not config.exists():
        code = api.request("POST", "/v1/pairings", {"project_ids": [profile.project_id]})["code"]
        pair_runner(api.url, code, config, client=api.client)
    saved = json.loads(config.read_bytes())
    if saved["url"] != api.url or saved["project_ids"] != [profile.project_id]:
        raise ValueError("저장된 연결이 다른 프로젝트에 속합니다")
    if bound and bound["runner_id"] != saved["runner_id"]:
        raise ValueError("다른 컴퓨터의 프로젝트 연결을 바꿀 수 없습니다")
    if bound and bound["online"]:
        return
    digest = hashlib.sha256(config.read_bytes()).hexdigest()
    pid_file = directory / "process.json"
    if pid_file.exists():
        recorded = json.loads(pid_file.read_bytes())
        if recorded.get("config_sha256") == digest and _running(recorded.get("pid")):
            return
    env = {key: value for key, value in os.environ.items() if not (key.startswith(("NVIDIA_", "NIM_"))
        or key.startswith("TRACEBRIDGE_OPERATOR_TOKEN") or key in {"OPENAI_API_KEY", "TRACEBRIDGE_NVIDIA_API_KEY", "TRACEBRIDGE_EMBEDDING_API_KEY",
            "TRACEBRIDGE_OCR_API_KEY", "TRACEBRIDGE_DATABASE_URL", "TRACEBRIDGE_DATABASE_URL_FILE"})}
    env["TRACEBRIDGE_PROJECT_REGISTRY"] = str(profile.config_path.parent)
    with (directory / "runner.log").open("ab") as log:
        process = subprocess.Popen([sys.executable, "-m", "scripts.local_runner", "--config", str(config.resolve()), "run"],
            cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    pid_file.write_text(json.dumps({"pid": process.pid, "config_sha256": digest}), encoding="utf-8")


def render_simple_setup(st, api=None, profile=None, *, namespace="simple-project"):
    if os.getenv("TRACEBRIDGE_ALLOW_PROJECT_REGISTRATION") != "1":
        return
    expected = hashlib.sha256(profile.config_path.read_bytes()).hexdigest() if profile else None
    with st.expander("프로젝트 정보 수정" if profile else "프로젝트 등록", expanded=profile is None):
        st.caption("이 컴퓨터에 있는 프로젝트 폴더를 연결합니다.")
        with st.form(namespace + ":form"):
            name = st.text_input("프로젝트 이름", value=(profile.display_name or profile.project_id) if profile else "", key=namespace + ":name")
            root = st.text_input("프로젝트 폴더", value=str(profile.root) if profile else "", placeholder="C:/work/my-project", key=namespace + ":root")
            url = st.text_input("실행 주소 (선택)", value=(select_project_service(profile).health_url or "") if profile else "",
                placeholder="http://127.0.0.1:3000", key=namespace + ":url")
            submitted = st.form_submit_button("프로젝트 정보 저장" if profile else "프로젝트 등록", type="primary")
        if submitted:
            try:
                reserved = [item["project_id"] for item in api.request("GET", "/v1/projects")] if api and profile is None else []
                saved = save_simple_project(name, root, url, profile=profile, expected_sha256=expected, reserved_ids=reserved)
                st.session_state["pending_project_selection"] = saved.project_id
                if api and profile is None:
                    try:
                        connect_local_project(api, saved)
                    except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
                        st.session_state["project_connect_error:" + saved.project_id] = type(exc).__name__
                st.session_state["local_connection_saved"] = saved.project_id
                st.session_state.pop("project_add", None)
                st.rerun()
            except (OSError, ValueError, RuntimeError, httpx.HTTPError) as exc:
                st.error(str(exc))
