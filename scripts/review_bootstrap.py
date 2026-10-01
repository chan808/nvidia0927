"""Install the downloaded companion in a new Desktop folder and open local UI."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser


def desktop():
    if os.name == "nt":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            return Path(os.path.expandvars(winreg.QueryValueEx(key, "Desktop")[0])).resolve()
    return Path.home() / "Desktop"


def main():
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("Python 3.12가 필요합니다. https://www.python.org/downloads/windows/ 에서 설치 후 START.cmd를 다시 실행하세요.")
    source = Path(__file__).resolve().parents[1]
    config = json.loads((source / "review-connection.json").read_text(encoding="utf-8"))
    project = config["project_id"]
    import re
    if not re.fullmatch(r"review-[a-f0-9]{24}", project):
        raise ValueError("다운로드한 체험 연결 정보를 확인해 주세요.")
    destination = desktop() / "TraceBridge-Review" / project
    if source != destination:
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".venv", "output", "__pycache__"))
        elif (destination / "review-connection.json").read_bytes() != (source / "review-connection.json").read_bytes():
            raise RuntimeError("다른 체험의 설치 폴더입니다. 사이트에서 새 실행기를 내려받아 주세요.")
    env = {key: value for key, value in os.environ.items() if not key.startswith(("NVIDIA_", "NIM_", "TRACEBRIDGE_")) and key != "OPENAI_API_KEY"}
    env.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1", TRACEBRIDGE_PROJECT_REGISTRY=str(destination / "output/project-profiles"),
               TRACEBRIDGE_DB_PATH=str(destination / "output/local-incidents.sqlite3"))
    python = destination / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    if not python.is_file():
        print("Creating a private Python environment in:", destination, flush=True)
        subprocess.run([sys.executable, "-m", "venv", str(destination / ".venv")], check=True, env=env)
    required = destination / "requirements-review.txt"
    fingerprint = hashlib.sha256(required.read_bytes()).hexdigest()
    installed = destination / ".dependencies-ready"
    if not installed.is_file() or installed.read_text() != fingerprint:
        print("Installing companion dependencies. This may take a few minutes on first launch.", flush=True)
        subprocess.run([str(python), "-m", "pip", "install", "-r", str(required)], check=True, env=env)
        subprocess.run([str(python), "-m", "pip", "check"], check=True, env=env)
        installed.write_text(fingerprint)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    print("Opening TraceBridge:", url, flush=True)
    process = subprocess.Popen([str(python), "-m", "streamlit", "run", str(destination / "review_app.py"),
        "--server.address=127.0.0.1", "--server.port=" + str(port), "--server.headless=true", "--browser.gatherUsageStats=false"],
        cwd=destination, env=env)
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError("로컬 화면을 시작하지 못했습니다. 위 오류를 확인해 주세요.")
        try:
            with urllib.request.urlopen(url + "/_stcore/health", timeout=1) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    break
        except OSError:
            pass
        time.sleep(0.5)
    else:
        process.terminate()
        raise RuntimeError("로컬 화면 시작 시간이 초과됐습니다.")
    print("Keep this window open while reviewing. Closing it stops the companion.", flush=True)
    try:
        process.wait()
    except KeyboardInterrupt:
        process.terminate()
        process.wait(timeout=10)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)
