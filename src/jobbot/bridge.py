from __future__ import annotations

import json
import logging
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .ai import AIClient
from .config import Config, Profile
from .greeting import build_greeting, tail_defect
from .matcher import merge_ai_score, score_job
from .models import Job
from .storage import Storage, now_local


def verdict_row(stage: str, job: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """流水里的一行判定。stage 区分 score / ai_score / greeting：
    过去只有招呼语那一腿写盘，被规则闸或精排拦下的岗位在文件里查不到，只能翻终端日志。
    """
    row: dict[str, Any] = {
        "time": now_local().isoformat(timespec="seconds"),
        "stage": stage,
        "job_id": result.get("job_id", ""),
        "title": str(job.get("title", ""))[:80],
        "company": str(job.get("company", ""))[:60],
        "area": str(job.get("area", ""))[:40],
        "score": result.get("score"),
        "min_score": result.get("min_score"),
        "passed": result.get("passed"),
        "risks": result.get("risks") or [],
        "source": result.get("source", ""),
        "error": result.get("error", ""),
    }
    if result.get("greeting") is not None:
        row["greeting_chars"] = len(result.get("greeting") or "")
        # 天生 120 字与被砍到 120 字，在磁盘上原本长得一模一样：这里补上"模型写了多少"与"是否被截"。
        row["greeting_raw_chars"] = result.get("raw_chars", 0)
        row["greeting_truncated"] = bool(result.get("truncated"))
        # 这一枚决定"这条流水是不是一次独立生成"：缓存命中的行与上一条逐字相同，
        # 数覆盖率/相似度时若不摘掉，同一次生成会被数两次（11-06 的重注入实测就是把双问当成两条产出过）。
        row["cached"] = bool(result.get("cached"))
        # 正文前 80 字也要进流水：只有字数的话，事后想数「招呼语到底提了档案里的哪几条」就只能去
        # 翻台账＋存储碎片＋快照帧（11-06 数覆盖率时能凑到正文的只有 18 条，就是这个原因），
        # 而 `.ldb` 一 compaction 那些正文再也取不回来。流水是纯追加的，留头 80 字最便宜。
        row["greeting_head"] = str(result.get("greeting") or "")[:80]
        # 尾 40 字也要留：`head` 只有前 80 字，而 11-06 实测那条以「错误率控制在。」收尾的正文是 117 字，
        # 缺陷正好落在 80 字之外 ⇒ 只有 head 的流水对这类残句是**盲的**（数出来永远是 0，那种 0 不算读数）。
        row["greeting_tail"] = str(result.get("greeting") or "")[-40:]
        # 只报不拦：把缺陷写进流水，平台真跑一场就能数出残句率；要拦投递得先裁"拦下之后走模板还是重问"。
        row["greeting_tail_defect"] = tail_defect(str(result.get("greeting") or ""))
        # 回退的那一次：原因＋被作废的原文头 120 字。没有这两格，磁盘上只剩「回退了」，
        # 想知道「模型到底写成了什么」只能重跑一遍去赌同一句话（11-06 就是这么卡的）。
        row["greeting_reject_reason"] = str(result.get("reject_reason") or "")
        row["greeting_reject_head"] = str(result.get("reject_head") or "")[:120]
    if result.get("rule_score") is not None:
        row["rule_score"] = result.get("rule_score")
    if result.get("ai_fit") is not None:
        row["ai_fit"] = result.get("ai_fit")
        row["ai_applied"] = result.get("ai_applied")
        row["scoring_provider"] = result.get("scoring_provider")
    if result.get("ai_error"):
        row["ai_error"] = result.get("ai_error")
    if "duplicate" in result:
        # 台账去重拦下的那条也得在磁盘上留名，否则事后只看得到一个「分数没过闸」的假象
        row["duplicate"] = result.get("duplicate")
        row["duplicate_reason"] = result.get("duplicate_reason", "")
        if result.get("duplicate_check_error"):
            row["duplicate_check_error"] = result["duplicate_check_error"]
    if "skipped" in result:
        row["skipped"] = result.get("skipped")
    if result.get("reasons"):
        row["reasons"] = result.get("reasons")
    if result.get("elapsed") is not None:
        row["elapsed"] = result.get("elapsed")
    return row


def record_verdict(cfg: Config, stage: str, job: dict[str, Any], result: dict[str, Any], logger=None) -> None:
    try:
        append_verdict(cfg.storage.verdicts, verdict_row(stage, job, result))
    except OSError as exc:
        if logger:
            logger.warning("判定写入 %s 失败：%s", cfg.storage.verdicts, exc)


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
        salary_raw=str(raw.get("salaryRaw") or "").strip(),
        location=str(raw.get("area") or "大连").strip(),
        url=str(raw.get("url") or "").strip(),
        description=str(raw.get("description") or "").strip()[:2000],
        tags=[str(tag).strip() for tag in tags if str(tag).strip()],
    )


def city_for_payload(cfg: Config, payload: dict[str, Any]) -> str:
    """按候选所属城市评分；扩展没带 searchCity（旧版/单城手工）时退回配置里那一个城市。"""
    raw = payload.get("job") if isinstance(payload.get("job"), dict) else payload
    return str((raw or {}).get("searchCity") or "").strip() or cfg.platform("boss").city


def ledger_duplicate(cfg: Config, job: Job, logger: Any = None) -> tuple[bool, str, str]:
    """去重以台账为准（你的口径）：扩展 `chrome.storage` 只存它自己发过的那几条，台账 `sent` 才是全集。

    返回 `(是否重复, 原因, 读取错误)`。台账读不到（被另一个 `jobbot` 进程占着、路径不对）时**放行**：
    这条闸只做「拦重复」，既不许反过来变成「读不到就不投」，也不许让一次读盘失败把整场跑挂。
    """
    try:
        duplicate, reason = Storage(cfg.storage.db).is_duplicate(
            "boss",
            job.job_id,
            job.company,
            job.title,
            cfg.defaults.dedupe_window_days,
        )
        return duplicate, reason, ""
    except (sqlite3.Error, OSError) as exc:
        if logger:
            logger.warning("台账去重读取失败（按放行处理）：%s", exc)
        return False, "", str(exc)[:200]


def make_mark_sent(cfg: Config, payload: dict[str, Any], logger=None) -> dict[str, Any]:
    """发送成功后的**当场补账**（2026-11-05 裁的「乙」）。

    此前桥对台账只有两个动作：查重复（`is_duplicate`）与起场时数行数——**从不写**。
    `mark_sent` 的调用点只有 `cli.py`（Playwright 那条被 BOSS 挡着的路）和 `extension-import`（手工导入），
    于是浏览器里 `auto_send` 刚发出去的岗位，在有人跑一次导入之前，去重闸根本看不见
    ⇒ 这段时差里同公司同职位会被再发一次。这个端点就是把"发过"这件事当场钉进 `applications`。
    失败**不许抛**：消息已经发出去了，补账不能反过来把发送挂住——所以只回 `ok=false`＋原因，让扩展记日志。
    """
    job = job_from_payload(payload)
    if not job.job_id or not job.title or not job.company:
        return {
            "ok": False,
            "error": f"缺少必要字段（job_id={job.job_id!r} 公司={job.company!r} 职位={job.title!r}），未写台账",
            "job_id": job.job_id,
        }
    row: dict[str, Any] = {
        "ok": True, "job_id": job.job_id, "company": job.company, "title": job.title, "status": "sent",
    }
    try:
        store = Storage(cfg.storage.db)
        # `mark_sent` 是 UPDATE，行不存在就静默写不进去 ⇒ 先确保有这一行（`record_planned` 走 upsert）
        store.record_planned(
            job,
            score=int(payload.get("score") or 0),
            greeting=str(payload.get("greeting") or ""),
            run_id=str(payload.get("run") or "extension"),
            # 台账里的地区只用扩展真传的那一枚；`job.location` 在没传时是评分兜底的「大连」，
            # 写进台账就成了"把北京记成大连"（日报的地区列正是从这里取的）。
            area=str((payload.get("job") or {}).get("area") or payload.get("area") or "").strip(),
        )
        store.mark_sent("boss", job.job_id, str(payload.get("evidence") or "extension:auto_send")[:500])
        counts = store.status_counts("boss")
        row.update({"ledger_rows": sum(counts.values()), "by_status": counts, "db": str(cfg.storage.db)})
        if logger:
            logger.info(
                "bridge mark_sent: job_id=%s %s/%s → sent｜db=%s rows=%s by_status=%s",
                job.job_id, job.company, job.title, row["db"], row["ledger_rows"], counts or "{}",
            )
    except (sqlite3.Error, OSError, ValueError, TypeError) as exc:
        row = {"ok": False, "error": f"写台账失败：{type(exc).__name__}: {exc}", "job_id": job.job_id}
        if logger:
            logger.warning("bridge mark_sent failed: job_id=%s %s", job.job_id, row["error"])
    return row


def make_score(cfg: Config, profile: Profile, payload: dict[str, Any], logger: Any = None) -> dict[str, Any]:
    """只做规则评分，不生成招呼语；自动搜索先用它把整池候选过一遍。"""
    job = job_from_payload(payload)
    if not job.title:
        return {"ok": False, "error": "缺少职位标题"}
    match = score_job(job, profile, cfg.search, city_for_payload(cfg, payload))
    duplicate, duplicate_reason, duplicate_error = ledger_duplicate(cfg, job, logger)
    return {
        "ok": True,
        "error": "",
        "job_id": job.job_id,
        "url": job.url,
        "score": match.score,
        "min_score": cfg.defaults.min_score,
        # 台账判了重复就不算过闸：`auto_send` 只放行 passed=true，这条因此发不出去
        "passed": match.passed(cfg.defaults.min_score) and not duplicate,
        "matched_skills": match.matched_skills,
        "risks": match.risks,
        # 拦下的**种类**要能查：硬拦与字体对表都叫 skipped，光看分数与 passed 分不出是哪条指令拦的
        "skipped": match.skipped,
        "reasons": [str(r)[:120] for r in (match.reasons or [])[:4]],
        "duplicate": duplicate,
        "duplicate_reason": duplicate_reason,
        "duplicate_check_error": duplicate_error,
        "dedupe_window_days": cfg.defaults.dedupe_window_days,
    }


def make_ai_score(
    cfg: Config,
    profile: Profile,
    ai_client: AIClient | None,
    payload: dict[str, Any],
    logger=None,
) -> dict[str, Any]:
    """规则分过线后再做 AI 精排；AI 失败时如实回退规则分，不冒充已精排。"""
    job = job_from_payload(payload)
    if not job.title:
        return {"ok": False, "error": "缺少职位标题", "ai_applied": False}
    match = score_job(job, profile, cfg.search, city_for_payload(cfg, payload))
    base = {
        "ok": True,
        "error": "",
        "ai_applied": False,
        "ai_error": "",
        "ai_fit": None,
        "scoring_provider": cfg.ai.scoring_provider_name,
        "rule_score": match.rule_score,
        "score": match.score,
        "min_score": cfg.defaults.min_score,
        "passed": match.passed(cfg.defaults.min_score),
        "skipped": match.skipped,
        "matched_skills": match.matched_skills,
        "risks": list(match.risks),
        "reasons": list(match.reasons),
        "job_id": job.job_id,
        "url": job.url,
    }
    if not base["passed"]:
        return base
    if not (cfg.ai.use_for_scoring and ai_client is not None and getattr(ai_client, "enabled", False)):
        base["ai_error"] = "AI 精排未启用"
        return base
    try:
        result = ai_client.score_job(job, match)
    except Exception as exc:
        if logger:
            logger.warning("AI 精排失败：%s", exc)
        message = f"AI 精排失败：{exc}"
        base.update({"ok": False, "error": message, "ai_error": message, "passed": False})
        return base
    if result is None:
        message = "AI 精排未返回有效结果"
        base.update({"ok": False, "error": message, "ai_error": message, "passed": False})
        return base
    fit, reasons, risks = result
    merged = merge_ai_score(match.rule_score, fit)
    base.update(
        {
            "ai_applied": True,
            "ai_fit": fit,
            "score": merged,
            # 硬拦（`match.skipped`）必须在合并这一层仍然算没过闸：这一行只按合并分重算过一次，
            # 于是「薪资/技能/标题硬拦不被精排洗白」全指望上面那句早退——把早退挪走（将来为排序而改
            # 「所有岗位都精排一遍」是完全可能的改动）就会静默放行。两道各守各的，不互相依赖。
            "passed": merged >= cfg.defaults.min_score and not match.skipped,
            "risks": [*match.risks, *[f"AI：{item}" for item in risks]],
            "reasons": [*match.reasons, *[f"AI：{item}" for item in reasons]],
        }
    )
    return base


def make_greeting(
    cfg: Config, profile: Profile, ai_client: AIClient, payload: dict[str, Any], logger=None
) -> dict[str, Any]:
    job = job_from_payload(payload)
    if not job.title:
        return {"ok": False, "error": "缺少职位标题", "greeting": "", "source": ""}
    match = score_job(job, profile, cfg.search, city_for_payload(cfg, payload))
    greeting = build_greeting(
        ai_client,
        profile,
        job,
        match,
        cfg.ai.max_greeting_chars,
        logger=logger,
        min_chars=cfg.ai.min_greeting_chars,
    )
    return {
        "ok": greeting.source == "ai",
        "error": "" if greeting.source == "ai" else "AI 未生成，已回退模板",
        "greeting": greeting.text,
        "source": greeting.source,
        "raw_chars": greeting.raw_chars,
        "truncated": greeting.truncated,
        # 回退不是终点，是**下一次要改哪一句**的线索：把被作废的原文一并带出去，流水里才数得出形状
        "reject_reason": greeting.reject_reason,
        "reject_head": greeting.reject_head,
        "job_id": job.job_id,
        "url": job.url,
        "score": match.score,
        "min_score": cfg.defaults.min_score,
        "passed": match.passed(cfg.defaults.min_score),
        # 招呼语这一腿以前只带 `passed`，于是「分数不够」与「被硬闸拦下」在磁盘上是同一种形状——
        # 与第廿七批在 `/score` 那侧修掉的是同一个毛病：拦下的**种类**要能查。
        # 这里不改行为：硬拦岗的招呼语照样生成（面板手工发送本来就不受分数闸管），只是让它看得见。
        "skipped": match.skipped,
        "reasons": [str(item)[:120] for item in match.reasons[:4]],
        "matched_skills": match.matched_skills,
        "risks": match.risks,
    }


def cache_key(job: dict[str, Any]) -> str:
    """岗位指纹：页面掉落后续跑会重复请求同一条岗位，用它命中缓存，不再白等 3-5 分钟。"""
    return "|".join(
        [
            str(job.get("id") or ""),
            str(job.get("title") or ""),
            str(job.get("company") or ""),
            str(len(str(job.get("description") or ""))),
        ]
    )


_RESULT_CACHE: dict[str, dict[str, Any]] = {}
_RESULT_CACHE_MAX = 64


def append_verdict(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def serve_bridge(cfg: Config, profile: Profile, port: int = 8765) -> None:
    logger = logging.getLogger("jobbot.bridge")
    ai_client = AIClient(cfg.ai, profile, logger)
    status = ai_client.prepare()
    print(
        f"bridge: AI provider={status.provider} ok={status.ok} detail={status.detail}",
        flush=True,
    )
    # 台账去重是按「查不到就放行」写的（2026-11-04 你的口径），而 `Storage()` 会把不存在的库连手建出来：
    # 路径写错 ⇒ 得到一份空台账 ⇒ 去重永远放行，且和「这家没投过」给出完全一样的读数，不报任何错。
    # 所以起场先把「解析到哪个文件、它原来在不在、里面几行」念出来，这件事只在事后靠猜是猜不出来的。
    ledger_path = Path(cfg.storage.db)
    ledger_existed = ledger_path.exists()
    try:
        counts = Storage(ledger_path).status_counts("boss")
        ledger_note = f"rows={sum(counts.values())} by_status={counts or '{}'}"
    except (sqlite3.Error, OSError) as error:
        ledger_note = f"rows=? 读不到（{type(error).__name__}: {error}）"
    print(
        f"bridge: ledger db={ledger_path} existed_before_init={ledger_existed} {ledger_note}",
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
            if self.path not in ("/ai_greeting", "/score", "/ai_score", "/mark_sent"):
                self._send(404, {"ok": False, "error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except (ValueError, json.JSONDecodeError) as exc:
                self._send(400, {"ok": False, "error": f"bad json: {exc}"})
                return
            job = payload.get("job") if isinstance(payload.get("job"), dict) else payload
            if self.path == "/mark_sent":
                result = make_mark_sent(cfg, payload, logger)
                self._send(200 if result.get("ok") else 400, result)
                return
            if self.path == "/score":
                try:
                    result = make_score(cfg, profile, payload, logger)
                except Exception as exc:
                    logger.warning("bridge score failed: %s", exc)
                    self._send(500, {"ok": False, "error": str(exc)})
                    return
                logger.info(
                    "bridge score: job_id=%s score=%s/%s passed=%s dup=%s risks=%s",
                    result.get("job_id"),
                    result.get("score"),
                    result.get("min_score"),
                    result.get("passed"),
                    result.get("duplicate"),
                    len(result.get("risks") or []),
                )
                record_verdict(cfg, "score", job, result, logger)
                self._send(200, result)
                return
            if self.path == "/ai_score":
                started = time.monotonic()
                try:
                    result = make_ai_score(cfg, profile, ai_client, payload, logger)
                except Exception as exc:
                    logger.warning("bridge ai_score failed: %s", exc)
                    self._send(500, {"ok": False, "error": str(exc), "ai_applied": False})
                    return
                elapsed = round(time.monotonic() - started, 1)
                result["elapsed"] = elapsed
                logger.info(
                    "bridge ai_score: job_id=%s provider=%s applied=%s rule=%s fit=%s "
                    "score=%s/%s passed=%s risks=%s error=%s elapsed=%ss",
                    result.get("job_id"),
                    result.get("scoring_provider"),
                    result.get("ai_applied"),
                    result.get("rule_score"),
                    result.get("ai_fit"),
                    result.get("score"),
                    result.get("min_score"),
                    result.get("passed"),
                    len(result.get("risks") or []),
                    result.get("ai_error") or "-",
                    elapsed,
                )
                record_verdict(cfg, "ai_score", job, result, logger)
                self._send(200, result)
                return
            if not status.ok:
                self._send(503, {"ok": False, "error": status.detail})
                return
            logger.info(
                "bridge ai_greeting: %s / %s",
                str(job.get("title", ""))[:40],
                str(job.get("company", ""))[:40],
            )
            started = time.monotonic()
            key = cache_key(job)
            cached = _RESULT_CACHE.get(key)
            if cached is not None:
                result = dict(cached)
                result["elapsed"] = 0.0
                result["cached"] = True
                logger.info(
                    "bridge greeting: job_id=%s 命中缓存 score=%s/%s "
                    "passed=%s skipped=%s risks=%s",
                    result.get("job_id"),
                    result.get("score"),
                    result.get("min_score"),
                    result.get("passed"),
                    result.get("skipped"),
                    len(result.get("risks") or []),
                )
                # 缓存命中也要落一行流水：否则那几条招呼语在 `verdicts.jsonl` 里彻底隐身，
                # H 格会把「三条都生成过」读成「只生成过一条」（15:xx 那场 results=3 只 1 行）。
                record_verdict(cfg, "greeting", job, result, logger)
                self._send(200, result)
                return
            try:
                result = make_greeting(cfg, profile, ai_client, payload, logger)
            except Exception as exc:
                logger.warning("bridge greeting failed: %s", exc)
                self._send(500, {"ok": False, "error": str(exc)})
                return
            result["elapsed"] = round(time.monotonic() - started, 1)
            if len(_RESULT_CACHE) >= _RESULT_CACHE_MAX:
                _RESULT_CACHE.pop(next(iter(_RESULT_CACHE)), None)
            _RESULT_CACHE[key] = dict(result)
            record_verdict(cfg, "greeting", job, result, logger)
            logger.info(
                "bridge greeting: job_id=%s ok=%s source=%s len=%s score=%s/%s "
                "passed=%s skipped=%s risks=%s elapsed=%ss",
                result.get("job_id"),
                result.get("ok"),
                result.get("source"),
                len(result.get("greeting") or ""),
                result.get("score"),
                result.get("min_score"),
                result.get("passed"),
                result.get("skipped"),
                len(result.get("risks") or []),
                result["elapsed"],
            )
            for risk in (result.get("risks") or [])[:3]:
                logger.info("bridge risk: %s", str(risk)[:90])
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
