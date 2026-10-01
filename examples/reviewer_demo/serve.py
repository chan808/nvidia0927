"""Loopback-only sample app with real requests and source snapshot observation."""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from uuid import uuid4

from tracebridge.project_repair import _snapshot, _snapshot_hash

ROOT = Path(__file__).resolve().parent
LOCK = threading.Lock()
PAGE = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TraceBridge 버그 예제</title><style>body{font:18px system-ui;background:#f4f7f8;max-width:680px;margin:80px auto;padding:24px}button{padding:14px;margin:6px;border-radius:9px;border:1px solid #aaa;cursor:pointer}pre{white-space:pre-wrap;background:white;padding:20px;border-radius:12px}</style>
<h1>최대 5개까지 신청할 수 있어요</h1><p>4개와 6개는 예상대로 동작합니다. 5개를 눌러 버그를 확인하세요.</p>
<button onclick="check(4)">4개 신청</button><button onclick="check(5)">5개 신청</button><button onclick="check(6)">6개 신청</button><pre id="result">버튼을 눌러 실제 API 응답을 확인하세요.</pre>
<script>async function check(count){let r=await fetch('/quotas',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({count})});let d=await r.json();document.querySelector('#result').textContent=(d.accepted?'신청 가능':'신청 거절')+' · HTTP '+r.status+'\\n'+JSON.stringify(d,null,2)}</script></html>'''


def load_runtime(server):
    with LOCK:
        raw = (ROOT / "app.py").read_bytes()
        if raw != getattr(server, "source", None):
            namespace = {}
            exec(compile(raw, str(ROOT / "app.py"), "exec"), namespace)
            server.accepted = namespace["accepted"]
            server.source = raw
            server.snapshot = _snapshot_hash(_snapshot(ROOT, deadline=time.monotonic() + 10))
            (ROOT / "output").mkdir(exist_ok=True)
            (ROOT / "output/version.json").write_text(json.dumps({"snapshot_sha256": server.snapshot}), encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, status, body, *, html=False):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json")
        self.send_header("X-TraceBridge-Snapshot-SHA256", self.server.snapshot)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body.encode() if html else json.dumps(body).encode())

    def do_GET(self):
        load_runtime(self.server)
        if self.path == "/":
            self.respond(200, PAGE, html=True)
        elif self.path == "/health":
            self.respond(200, {"status": "UP", "demo": "tracebridge-quota"})
        elif self.path == "/limits":
            fn = self.server.accepted
            self.respond(200, {"ok": fn(0) and fn(4) and not fn(-1) and not fn(6)})
        else:
            self.respond(404, {"error": "not found"})

    def do_POST(self):
        load_runtime(self.server)
        if self.path != "/quotas":
            self.respond(404, {"error": "not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 2000:
                raise ValueError()
            count = json.loads(self.rfile.read(size))["count"]
            if type(count) is not int:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            self.respond(400, {"error": "integer count required"})
            return
        result = self.server.accepted(count)
        status = 500 if count == 5 and not result else 200
        request_id = "quota-" + uuid4().hex[:12]
        occurred = datetime.now(timezone.utc).isoformat()
        message = "QUOTA_BOUNDARY: five items must be accepted" if status == 500 else "quota request completed"
        event = {"trace": {"trace_id": request_id, "service": "api", "environment": "dev", "occurred_at": occurred,
                           "response_status": status, "method": "POST", "path": "/quotas"}, "message": message}
        with LOCK, (ROOT / "output/events.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(event) + "\n")
        self.respond(status, {"accepted": result, "request_id": request_id, "occurred_at": occurred, "message": message})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    load_runtime(server)
    server.serve_forever()
