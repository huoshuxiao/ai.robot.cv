from __future__ import annotations

import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .ai import AIClient
from .config import Config, Profile
from .greeting import build_greeting
from .matcher import score_job
from .models import Job


def job_from_payload(payload: dict[str, Any]) -> Job:
    raw = payload.get("job") if isinstance(payload.get("job"), dict) else payload
    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        tags = [str(tags)]
    return Job(
        platform="boss",
        job_id=str(raw.get("id") or "extension"),
        title=str(raw.get("title") or "Java后端开发工程师").strip(),
        company=str(raw.get("company") or "").strip(),
        salary=str(raw.get("salary") or "").strip(),
        location=str(raw.get("area") or "大连").strip(),
        url=str(raw.get("url") or "").strip(),
        description=str(raw.get("description") or "").strip()[:2000],
        tags=[str(tag).strip() for tag in tags if str(tag).strip()],
    )


def make_greeting(
    cfg: Config, profile: Profile, ai_client: AIClient, payload: dict[str, Any]
) -> dict[str, Any]:
    job = job_from_payload(payload)
    if not job.title:
        return {"ok": False, "error": "缺少职位标题", "greeting": "", "source": ""}
    match = score_job(job, profile, cfg.search, cfg.platform("boss").city)
    greeting = build_greeting(
        ai_client, profile, job, match, cfg.ai.max_greeting_chars
    )
    return {
        "ok": greeting.source == "ai",
        "error": "" if greeting.source == "ai" else "AI 未生成，已回退模板",
        "greeting": greeting.text,
        "source": greeting.source,
        "score": match.score,
        "matched_skills": match.matched_skills,
    }


def serve_bridge(cfg: Config, profile: Profile, port: int = 8765) -> None:
    logger = logging.getLogger("jobbot.bridge")
    ai_client = AIClient(cfg.ai, profile, logger)
    status = ai_client.prepare()
    print(
        f"bridge: AI provider={status.provider} ok={status.ok} detail={status.detail}",
        flush=True,
    )

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, data: dict[str, Any]) -> None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()
            try:
                self.wfile.write(body)
            except BrokenPipeError:
                pass

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Access-Control-Max-Age", "600")
            self.end_headers()

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send(
                    200,
                    {
                        "ok": status.ok,
                        "provider": status.provider,
                        "detail": status.detail,
                        "model": status.model,
                    },
                )
                return
            self._send(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:
            logger.info("bridge POST %s", self.path)
            if self.path != "/ai_greeting":
                self._send(404, {"ok": False, "error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(400, {"ok": False, "error": f"bad json: {exc}"})
                return
            if not status.ok:
                self._send(503, {"ok": False, "error": status.detail})
                return
            job = payload.get("job") if isinstance(payload.get("job"), dict) else payload
            logger.info(
                "bridge ai_greeting: %s / %s",
                str(job.get("title", ""))[:40],
                str(job.get("company", ""))[:40],
            )
            started = time.monotonic()
            try:
                result = make_greeting(cfg, profile, ai_client, payload)
            except Exception as exc:
                logger.warning("bridge greeting failed: %s", exc)
                self._send(500, {"ok": False, "error": str(exc)})
                return
            result["elapsed"] = round(time.monotonic() - started, 1)
            logger.info(
                "bridge greeting: ok=%s source=%s len=%s elapsed=%ss",
                result.get("ok"),
                result.get("source"),
                len(result.get("greeting") or ""),
                result["elapsed"],
            )
            self._send(200, result)

        def log_message(self, *args: Any) -> None:
            return None

    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"bridge: listening http://127.0.0.1:{port}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        ai_client.unload_all_local()
