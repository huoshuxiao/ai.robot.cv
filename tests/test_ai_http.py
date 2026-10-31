from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jobbot.ai import AIClient
from jobbot.config import AIConfig, AIProviderConfig
from jobbot.models import Job, MatchResult

KEY = "JOBBOT_HTTP_KEY"


class _Handler(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        _Handler.requests.append(
            {
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "body": body,
            }
        )
        content = json.dumps({"fit": 90, "reasons": ["HTTP 匹配"], "risks": []})
        payload = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        return None


def test_online_provider_real_http_round_trip(profile, monkeypatch):
    _Handler.requests = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv(KEY, "secret")
        provider = AIProviderConfig(
            name="online",
            type="openai",
            base_url=f"http://127.0.0.1:{httpd.server_port}/v1",
            model="mock-model",
            api_key_env=KEY,
            timeout_seconds=5,
        )
        cfg = AIConfig(
            enabled=True,
            provider="online",
            fallback=None,
            providers={"online": provider},
            use_for_scoring=True,
        )
        job = Job(
            platform="boss",
            job_id="j1",
            title="Java后端开发工程师",
            company="示例科技",
            salary="15-25K",
            location="大连",
            tags=["Java"],
            description="负责 Java 后端开发。",
        )
        result = AIClient(cfg, profile).score_job(job, MatchResult(score=80))
        assert result == (90, ["HTTP 匹配"], [])
        request = _Handler.requests[-1]
        assert request["path"] == "/v1/chat/completions"
        assert request["auth"] == "Bearer secret"
        assert request["body"]["model"] == "mock-model"
        assert "忽略" in request["body"]["messages"][0]["content"]
    finally:
        httpd.shutdown()
        httpd.server_close()
