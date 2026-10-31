from __future__ import annotations

import os
import re
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin

import yaml
from bs4 import BeautifulSoup

from ..browser import find_system_chromium
from ..models import Job
from .base import (
    AdapterError,
    BaseAdapter,
    LoginRequired,
    RiskControlDetected,
    SelectorError,
    SendResult,
)

LOGIN_COOKIES = ("zp_token", "geek_zp_token")


def load_selectors(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise AdapterError(f"选择器配置非法：{path}")
    return data


def _text(element) -> str:
    if element is None:
        return ""
    return re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip()


def _select_text(root, selector: str | None) -> str:
    if not selector:
        return ""
    return _text(root.select_one(selector))


def parse_job_cards(
    html: str,
    platform: str,
    selectors: dict[str, Any],
    base_url: str = "https://www.zhipin.com",
) -> list[Job]:
    soup = BeautifulSoup(html, "html.parser")
    sel = selectors.get("search", {})
    jobs: list[Job] = []
    seen: set[str] = set()
    for card in soup.select(sel.get("card", ".job-card-wrapper")):
        link = card.select_one(sel["card_link"]) if sel.get("card_link") else None
        if link is None:
            link = card.find("a", href=re.compile(r"/job_detail/"))
        href = link.get("href") if link else ""
        if not href:
            continue
        url = urljoin(base_url, href)
        match = re.search(r"/job_detail/([^/?#]+)\.html", url)
        if not match:
            continue
        job_id = match.group(1)
        if job_id in seen:
            continue
        title = _select_text(card, sel.get("title"))
        if not title:
            continue
        tags = []
        if sel.get("tags"):
            tags = [t for t in (_text(li) for li in card.select(sel["tags"])) if t]
        card_text = _text(card)
        jobs.append(
            Job(
                platform=platform,
                job_id=job_id,
                title=title,
                company=_select_text(card, sel.get("company")),
                salary=_select_text(card, sel.get("salary")),
                location=_select_text(card, sel.get("area")),
                url=url,
                tags=tags,
                recruiter=_select_text(card, sel.get("recruiter")),
                raw={"already_chat": "已沟通" in card_text},
            )
        )
        seen.add(job_id)
    return jobs


def parse_job_detail(html: str, job: Job, selectors: dict[str, Any]) -> Job:
    soup = BeautifulSoup(html, "html.parser")
    sel = selectors.get("detail", {})
    desc_el = soup.select_one(sel["description"]) if sel.get("description") else None
    description = desc_el.get_text("\n", strip=True) if desc_el else ""
    tags = list(job.tags)
    if sel.get("tags"):
        for li in soup.select(sel["tags"]):
            tag = _text(li)
            if tag and tag not in tags:
                tags.append(tag)
    already_text = sel.get("already_chat_text", "")
    raw = dict(job.raw)
    raw["already_chat"] = bool(already_text and already_text in soup.get_text())
    return replace(
        job,
        company=_select_text(soup, sel.get("company")) or job.company,
        salary=_select_text(soup, sel.get("salary")) or job.salary,
        location=_select_text(soup, sel.get("location")) or job.location,
        description=description[:6000] if description else job.description,
        tags=tags,
        recruiter=_select_text(soup, sel.get("recruiter")) or job.recruiter,
        raw=raw,
    )


def detect_guard(html: str, url: str, selectors: dict[str, Any]) -> str | None:
    guard = selectors.get("guard", {})
    for marker in guard.get("login_url_markers", []):
        if marker in url:
            return "login"
    for marker in guard.get("risk_url_markers", []):
        if marker in url:
            return "captcha"
    soup = BeautifulSoup(html, "html.parser")
    for selector in guard.get("login_wall_selectors", []):
        if soup.select_one(selector):
            return "login"
    for selector in guard.get("captcha_selectors", []):
        if soup.select_one(selector):
            return "captcha"
    for selector in guard.get("risk_selectors", []):
        if soup.select_one(selector):
            return "risk"
    text = soup.get_text(" ", strip=True)
    for marker in guard.get("captcha_texts", []):
        if marker in text:
            return "captcha"
    return None


def detect_login_state(html: str, url: str, cookies: set[str], selectors: dict[str, Any]) -> bool:
    state = detect_guard(html, url, selectors)
    if state in ("login", "captcha", "risk"):
        return False
    if any(name in cookies for name in LOGIN_COOKIES):
        return True
    login_cfg = selectors.get("login", {})
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    if any(marker in text for marker in login_cfg.get("logged_out_texts", [])):
        return False
    return any(marker in text for marker in login_cfg.get("logged_in_texts", []))


class BossAdapter(BaseAdapter):
    name = "boss"

    def __init__(
        self,
        *args,
        headless: bool | None = None,
        debug_dir: Path | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if not self.platform.selectors:
            raise AdapterError("boss 平台缺少 selectors 配置")
        self.selectors = load_selectors(self.platform.selectors)
        self._headless = self.defaults.headless if headless is None else headless
        self._debug_dir = debug_dir or Path("data/debug")
        self._pw = None
        self._ctx = None
        self._page = None

    def _start(self) -> None:
        if self._ctx is not None:
            return
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise AdapterError(
                "缺少 playwright，请运行：pip install playwright && playwright install chromium"
            ) from exc
        self._pw = sync_playwright().start()
        if not self.platform.user_data_dir:
            raise AdapterError("boss 平台缺少 user_data_dir 配置")
        self.platform.user_data_dir.mkdir(parents=True, exist_ok=True)
        kwargs: dict[str, Any] = {
            "user_data_dir": str(self.platform.user_data_dir),
            "headless": self._headless,
            "locale": self.platform.locale,
            "viewport": {"width": 1366, "height": 900},
        }
        executable = os.environ.get("JOBBOT_CHROMIUM", "").strip()
        if executable:
            if not Path(executable).is_file():
                self._stop_driver()
                raise AdapterError(f"JOBBOT_CHROMIUM 指向的文件不存在：{executable}")
            kwargs["executable_path"] = executable
        else:
            bundled = self._pw.chromium.executable_path
            if not Path(bundled).exists():
                system = find_system_chromium()
                if system is None:
                    self._stop_driver()
                    raise AdapterError(
                        "未找到可用的 Chromium。请运行 `playwright install chromium`，"
                        "或设置 JOBBOT_CHROMIUM 指向本机浏览器"
                    )
                kwargs["executable_path"] = system
                self.log.info("Playwright 自带浏览器缺失，改用系统浏览器：%s", system)
        try:
            self._ctx = self._pw.chromium.launch_persistent_context(**kwargs)
        except Exception as exc:
            self._stop_driver()
            raise AdapterError(
                f"浏览器启动失败：{exc}\n可尝试：playwright install chromium，"
                f"或设置 JOBBOT_CHROMIUM 指向本机浏览器"
            ) from exc
        self._ctx.set_default_timeout(int(self.defaults.page_timeout_seconds * 1000))
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()

    def _stop_driver(self) -> None:
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._pw = None

    def _content(self, attempts: int = 5, delay_ms: int = 600) -> str:
        if self._page is None:
            raise AdapterError("浏览器尚未启动")
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                return self._page.content()
            except Exception as exc:
                last_error = exc
                try:
                    self._page.wait_for_timeout(delay_ms)
                except Exception:
                    break
        try:
            return self._page.evaluate("document.documentElement.outerHTML")
        except Exception as exc:
            raise AdapterError(f"页面仍在跳转，无法读取内容：{last_error or exc}") from exc

    def _guard(self) -> None:
        if self._page is None:
            raise AdapterError("浏览器尚未启动")
        state = detect_guard(self._content(), self._page.url, self.selectors)
        if state == "login":
            raise LoginRequired("登录态失效，请先运行：jobbot login")
        if state in ("captcha", "risk"):
            self._dump(f"{state}")
            raise RiskControlDetected("触发验证码/风控，已停机，请人工处理后再运行")

    def _goto(self, url: str) -> None:
        if self._page is None:
            raise AdapterError("浏览器尚未启动")
        self._page.goto(url, wait_until="domcontentloaded")
        self._page.wait_for_timeout(800)
        self._guard()

    def _dump(self, tag: str) -> str:
        if self._page is None:
            return ""
        debug_dir = Path(os.environ.get("JOBBOT_DEBUG_DIR", str(self._debug_dir)))
        debug_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        base = debug_dir / f"{stamp}-boss-{tag}"
        try:
            self._page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
            base.with_suffix(".html").write_text(self._content(), encoding="utf-8")
            self.log.error("调试现场已保存：%s.png / .html", base)
        except Exception as exc:
            self.log.warning("保存调试现场失败：%s", exc)
        return str(base)

    def check_login(self) -> bool:
        self._start()
        assert self._page is not None and self._ctx is not None
        try:
            self._page.goto(
                self.selectors.get("home_url", "https://www.zhipin.com/"),
                wait_until="domcontentloaded",
            )
            self._page.wait_for_timeout(1200)
        except Exception as exc:
            self.log.warning("打开首页失败：%s", exc)
        cookies = {c["name"] for c in self._ctx.cookies("https://www.zhipin.com")}
        content = self._content()
        state = detect_guard(content, self._page.url, self.selectors)
        if state in ("captcha", "risk"):
            self.log.warning(
                "BOSS 返回安全验证页（%s），请运行 jobbot login 并在浏览器中人工完成验证",
                self._page.url,
            )
        logged_in = detect_login_state(content, self._page.url, cookies, self.selectors)
        self.log.debug("登录检查：cookies=%s state=%s url=%s", sorted(cookies), state, self._page.url)
        return logged_in

    def login(self, timeout_s: int = 180) -> bool:
        self._start()
        assert self._page is not None
        self._page.goto(self.selectors["login_url"], wait_until="domcontentloaded")
        print("请在弹出的浏览器中扫码登录 BOSS 直聘，登录成功后会自动继续……")
        deadline = datetime.now().timestamp() + timeout_s
        while datetime.now().timestamp() < deadline:
            self._page.wait_for_timeout(2000)
            if self._page.is_closed():
                raise AdapterError("浏览器已关闭，登录未完成")
            cookies = {c["name"] for c in self._ctx.cookies("https://www.zhipin.com")}
            if any(name in cookies for name in LOGIN_COOKIES) and "/web/user/" not in self._page.url:
                print("登录成功，登录态已保存到本地用户目录。")
                return True
        raise AdapterError(f"登录超时（{timeout_s}s），请重试")

    def _wait_for_cards(self) -> bool:
        assert self._page is not None
        sel = self.selectors.get("search", {})
        try:
            self._page.wait_for_selector(sel.get("card", ".job-card-wrapper"))
        except Exception as exc:
            html = self._content()
            if sel.get("empty") and BeautifulSoup(html, "html.parser").select_one(sel["empty"]):
                return False
            self._dump("no-cards")
            raise SelectorError("搜索页未出现职位卡，选择器可能需要更新（现场已保存）") from exc
        for _ in range(2):
            self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self._page.wait_for_timeout(600)
        return True

    def _current_ids(self) -> set[str]:
        assert self._page is not None
        jobs = parse_job_cards(self._content(), self.platform.name, self.selectors)
        return {job.job_id for job in jobs}

    def _goto_next_page(self) -> bool:
        assert self._page is not None
        selector = self.selectors.get("search", {}).get("next_page")
        if not selector:
            return False
        locator = self._page.locator(selector).first
        if locator.count() == 0:
            return False
        classes = locator.get_attribute("class") or ""
        if "disabled" in classes or locator.get_attribute("aria-disabled") == "true":
            return False
        before = self._current_ids()
        try:
            locator.click()
        except Exception as exc:
            self.log.warning("翻页失败：%s", exc)
            return False
        for _ in range(12):
            self._page.wait_for_timeout(700)
            if self._current_ids() != before:
                self._guard()
                return True
        return False

    def search_jobs(self) -> list[Job]:
        self._start()
        assert self._page is not None
        template = self.selectors["search_url"]
        jobs: list[Job] = []
        seen: set[str] = set()
        for keyword in self.search.keywords:
            url = template.format(query=quote(keyword), city_code=self.platform.city_code)
            self.log.info("搜索：%s（%s）", keyword, url)
            self._goto(url)
            if not self._wait_for_cards():
                self.log.info("关键词「%s」无结果", keyword)
                continue
            for page_no in range(1, self.search.max_pages + 1):
                for job in parse_job_cards(self._content(), self.platform.name, self.selectors):
                    if job.job_id in seen:
                        continue
                    seen.add(job.job_id)
                    job.raw["keyword"] = keyword
                    jobs.append(job)
                self.log.info("「%s」第 %d 页累计 %d 个职位", keyword, page_no, len(jobs))
                if page_no >= self.search.max_pages or not self._goto_next_page():
                    break
        return jobs

    def fetch_detail(self, job: Job) -> Job:
        self._start()
        assert self._page is not None
        self._goto(job.url)
        selector = self.selectors.get("detail", {}).get("description")
        try:
            if selector:
                self._page.wait_for_selector(selector)
        except Exception as exc:
            self._dump(f"detail-{job.job_id}")
            raise SelectorError(f"职位详情缺少描述区域：{job.url}（现场已保存）") from exc
        self._guard()
        return parse_job_detail(self._content(), job, self.selectors)

    def _await_bubble(self, page, greeting: str, timeout_ms: int = 15000) -> bool:
        selector = self.selectors.get("chat", {}).get("message_item")
        if not selector:
            return False
        snippet = greeting[:12]
        try:
            page.locator(selector).filter(has_text=snippet).first.wait_for(
                state="visible", timeout=timeout_ms
            )
            return True
        except Exception:
            return False

    def send_greeting(self, job: Job, greeting: str) -> SendResult:
        self._start()
        assert self._page is not None and self._ctx is not None
        page = self._page
        self._goto(job.url)
        detail_sel = self.selectors.get("detail", {})
        already_text = detail_sel.get("already_chat_text", "")
        if already_text and already_text in page.inner_text("body"):
            return SendResult(ok=False, error="平台显示已沟通")

        chat_sel = self.selectors.get("chat", {})
        button = page.locator(detail_sel.get("chat_button", ".btn-startchat")).first
        if button.count() == 0:
            self._dump(f"no-chat-button-{job.job_id}")
            return SendResult(ok=False, error="未找到「立即沟通」按钮")

        pages_before = list(self._ctx.pages)
        clicked = False
        try:
            button.click()
            clicked = True
            new_pages = [p for p in self._ctx.pages if p not in pages_before]
            if new_pages:
                page = new_pages[-1]
                self._page = page
            input_box = page.locator(chat_sel.get("input", "#chat-input")).first
            input_box.wait_for(state="visible", timeout=15000)
            self._guard()

            try:
                input_box.fill(greeting)
            except Exception:
                input_box.click()
                page.keyboard.type(greeting, delay=30)
            page.keyboard.press("Enter")

            if self._await_bubble(page, greeting):
                return SendResult(ok=True, evidence=f"消息气泡已出现：{greeting[:20]}")

            send_button = page.locator(chat_sel.get("send_button", ".send-message")).first
            if send_button.count():
                try:
                    send_button.click()
                except Exception:
                    pass
                if self._await_bubble(page, greeting):
                    return SendResult(ok=True, evidence=f"消息气泡已出现：{greeting[:20]}")

            self._dump(f"unverified-{job.job_id}")
            return SendResult(ok=False, error="已尝试发送但未确认到消息气泡", uncertain=True)
        except RiskControlDetected as exc:
            self._dump(f"risk-{job.job_id}")
            return SendResult(ok=False, error=str(exc), requires_action=True)
        except LoginRequired:
            raise
        except Exception as exc:
            self._dump(f"send-error-{job.job_id}")
            return SendResult(ok=False, error=f"发送异常：{exc}", uncertain=clicked)

    def close(self) -> None:
        try:
            if self._ctx is not None:
                self._ctx.close()
        except Exception:
            pass
        finally:
            self._ctx = None
            self._page = None
            self._stop_driver()
