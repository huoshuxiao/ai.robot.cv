from __future__ import annotations

import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml

from jobbot.adapters.boss import BossAdapter, load_selectors
from jobbot.browser import find_system_chromium
from jobbot.config import load_config, load_profile

ROOT = Path(__file__).resolve().parents[1]

SEARCH_PAGE = """
<html><body>
<li class="job-card-wrapper">
  <a class="job-card-left" href="{base}/job_detail/local001.html">
    <div class="job-name">Java后端开发工程师</div>
    <div class="company-name">本地示例科技有限公司</div>
    <div class="salary">15-25K</div>
    <div class="job-area">大连·高新园区</div>
    <ul class="tag-list"><li>Java</li><li>Spring Boot</li><li>5-10年</li></ul>
  </a>
  <div class="info-public">王女士</div>
</li>
</body></html>
"""

DETAIL_PAGE = """
<html><body>
  <div class="company-info"><span class="company-name">本地示例科技有限公司</span></div>
  <div class="job-banner">
    <span class="salary">15-25K</span>
    <span class="text-city">大连·高新园区</span>
  </div>
  <div class="job-sec-text">负责 Java 后端开发，使用 Spring Boot 与 PostgreSQL，
    参与微服务架构设计与数据库性能优化。</div>
  <ul class="job-keyword-list"><li>Java</li><li>PostgreSQL</li></ul>
  <div class="job-boss-info"><span class="name">李经理</span></div>
  <a class="btn-startchat" href="/chat.html">立即沟通</a>
</body></html>
"""

CHAT_PAGE = """
<html><body>
  <div id="chat-input" contenteditable="true"></div>
  <div class="message-list"></div>
  <script>
    const input = document.getElementById('chat-input');
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Enter') {
        event.preventDefault();
        const item = document.createElement('div');
        item.className = 'message-item';
        item.textContent = input.textContent;
        document.querySelector('.message-list').appendChild(item);
        input.textContent = '';
      }
    });
  </script>
</body></html>
"""

HOME_PAGE = "<html><body><div class='user-nav'>我的简历</div></body></html>"


def _browser_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    if find_system_chromium():
        return True
    pw = None
    try:
        pw = sync_playwright().start()
        return Path(pw.chromium.executable_path).exists()
    except Exception:
        return False
    finally:
        if pw is not None:
            try:
                pw.stop()
            except Exception:
                pass


class _Handler(BaseHTTPRequestHandler):
    routes: dict[str, str] = {}

    def do_GET(self):
        body = self.routes.get(self.path.split("?")[0])
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: Any) -> None:
        return None


@pytest.fixture
def local_site():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    base = f"http://127.0.0.1:{httpd.server_port}"
    _Handler.routes = {
        "/": HOME_PAGE,
        "/jobs": SEARCH_PAGE.format(base=base),
        "/job_detail/local001.html": DETAIL_PAGE,
        "/chat.html": CHAT_PAGE,
    }
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield base
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.skipif(not _browser_ready(), reason="需要可用的 Chromium")
def test_boss_adapter_playwright_end_to_end(local_site, tmp_path):
    cfg = load_config(ROOT / "config" / "config.yaml")
    profile = load_profile(cfg.profile_path)

    selectors = load_selectors(ROOT / "config" / "selectors.boss.yaml")
    selectors["home_url"] = f"{local_site}/"
    selectors["login_url"] = f"{local_site}/login"
    selectors["search_url"] = f"{local_site}/jobs?query={{query}}&city={{city_code}}"
    selectors_path = tmp_path / "selectors.yaml"
    selectors_path.write_text(
        yaml.safe_dump(selectors, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    platform = replace(
        cfg.platform("boss"),
        selectors=selectors_path,
        user_data_dir=tmp_path / "profile",
        city_code="local",
    )
    search = replace(cfg.search, keywords=("Java",), max_pages=1)
    defaults = replace(
        cfg.defaults,
        headless=True,
        page_timeout_seconds=15,
        min_interval_seconds=0,
        max_interval_seconds=0,
    )
    adapter = BossAdapter(platform, search, defaults, profile, headless=True, debug_dir=tmp_path / "debug")
    try:
        assert adapter.check_login() is True

        jobs = adapter.search_jobs()
        assert len(jobs) == 1
        assert jobs[0].job_id == "local001"
        assert jobs[0].title == "Java后端开发工程师"
        assert jobs[0].salary == "15-25K"

        detail = adapter.fetch_detail(jobs[0])
        assert "PostgreSQL" in detail.description
        assert detail.recruiter == "李经理"

        greeting = "您好，我是孙文昆，21年Java后端经验，方便聊聊吗？"
        result = adapter.send_greeting(detail, greeting)
        assert result.ok, result.error
        assert "消息气泡" in result.evidence
    finally:
        adapter.close()
