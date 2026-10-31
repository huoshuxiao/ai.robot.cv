from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .adapters.base import AdapterError, BaseAdapter
from .ai import AIClient
from .config import Config, Profile
from .greeting import build_greeting
from .matcher import merge_ai_score, score_job
from .models import Plan, PlanItem
from .storage import Storage, now_local


class Planner:
    def __init__(
        self,
        config: Config,
        profile: Profile,
        storage: Storage,
        adapter: BaseAdapter,
        ai_client: AIClient | None = None,
        logger: logging.Logger | None = None,
        clock: Callable[[], datetime] = now_local,
    ):
        self.config = config
        self.profile = profile
        self.storage = storage
        self.adapter = adapter
        self.ai = ai_client
        self.log = logger or logging.getLogger(__name__)
        self.clock = clock

    def build_plan(self, platform_name: str | None = None, limit: int | None = None) -> tuple[Plan, Path]:
        pc = self.config.platform(platform_name)
        now = self.clock()
        run_id = f"{pc.name}-{now:%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
        sent_today = self.storage.count_sent_today(pc.name)
        day_remaining = max(0, pc.limit_per_day - sent_today)
        requested = pc.limit_per_run if limit is None else max(0, int(limit))
        effective = min(requested, day_remaining)

        city_names = "、".join(city.name for city in pc.search_cities) or pc.city
        plan = Plan(
            run_id=run_id,
            platform=pc.name,
            city=city_names,
            created_at=now.isoformat(timespec="seconds"),
            min_score=self.config.defaults.min_score,
            limit=effective,
            items=[],
            skipped=[],
            meta={
                "sent_today": sent_today,
                "day_remaining": day_remaining,
                "limit_requested": requested,
                "ai_enabled": bool(self.ai and self.ai.enabled),
            },
        )
        if effective <= 0:
            plan.meta["reason"] = "今日投递配额已用完"
            self.log.info("今日配额已用完（已投 %d / 上限 %d）", sent_today, pc.limit_per_day)
            return plan, self._write_plan(plan)

        jobs = self.adapter.search_jobs()
        plan.meta["total_found"] = len(jobs)
        candidates: list[PlanItem] = []
        skipped: list[dict] = []
        seen: set[str] = set()

        for job in jobs:
            if job.job_id in seen:
                skipped.append(self._skip(job, "本轮重复职位", 0))
                continue
            seen.add(job.job_id)

            duplicate, reason = self.storage.is_duplicate(pc.name, job.job_id)
            if duplicate:
                skipped.append(self._skip(job, reason, 0))
                continue
            if job.raw.get("already_chat"):
                skipped.append(self._skip(job, "平台显示已沟通", 0))
                continue

            if len(job.description) < 50:
                try:
                    job = self.adapter.fetch_detail(job)
                except AdapterError as exc:
                    self.log.warning("获取详情失败 %s：%s", job.job_id, exc)
                    skipped.append(self._skip(job, f"详情获取失败：{exc}", 0))
                    continue

            if job.raw.get("already_chat"):
                skipped.append(self._skip(job, "平台显示已沟通", 0))
                continue

            search_city = str(job.raw.get("search_city") or pc.city)
            match = score_job(job, self.profile, self.config.search, search_city)
            if match.skipped or match.score < self.config.defaults.min_score:
                if match.skipped:
                    reason = match.reasons[0] if match.reasons else "命中排除规则"
                else:
                    reason = f"得分 {match.score} 低于阈值 {self.config.defaults.min_score}"
                    if match.risks:
                        reason += f"（{match.risks[0]}）"
                skipped.append(self._skip(job, reason, match.score))
                continue
            candidates.append(PlanItem(job=job, match=match))

        candidates.sort(key=lambda item: (-item.match.score, item.job.company))
        shortlist = candidates[: max(effective * 2, effective)]
        rest = candidates[len(shortlist) :]

        if self.ai is not None and self.ai.enabled and self.ai.config.use_for_scoring:
            for item in shortlist:
                result = self.ai.score_job(item.job, item.match)
                if result is None:
                    continue
                fit, reasons, risks = result
                item.match.ai_score = fit
                item.match.score = merge_ai_score(item.match.rule_score, fit)
                item.match.reasons.extend(f"AI：{r}" for r in reasons)
                item.match.risks.extend(f"AI：{r}" for r in risks)

        shortlist.sort(key=lambda item: (-item.match.score, item.job.company))
        selected: list[PlanItem] = []
        for item in shortlist:
            if len(selected) >= effective:
                skipped.append(self._skip(item.job, "超出本次限额", item.match.score))
            elif item.match.score < self.config.defaults.min_score:
                skipped.append(self._skip(item.job, "AI/规则复核后低于阈值", item.match.score))
            else:
                selected.append(item)
        for item in rest:
            skipped.append(self._skip(item.job, "排名靠后，超出本次限额", item.match.score))

        max_greeting = self.config.ai.max_greeting_chars
        for item in selected:
            greeting = build_greeting(
                self.ai, self.profile, item.job, item.match, max_greeting, self.log
            )
            item.greeting = greeting.text
            item.greeting_source = greeting.source

        plan.items = selected
        plan.skipped = skipped
        plan.meta["candidates"] = len(candidates)
        plan.meta["skipped"] = len(skipped)

        path = self._write_plan(plan)
        self.storage.add_run(run_id, pc.name, len(selected))
        for item in selected:
            self.storage.record_planned(item.job, item.match.score, item.greeting, run_id)
        return plan, path

    @staticmethod
    def _skip(job, reason: str, score: int) -> dict:
        return {
            "job_id": job.job_id,
            "title": job.title,
            "company": job.company,
            "url": job.url,
            "score": score,
            "reason": reason,
        }

    def _write_plan(self, plan: Plan) -> Path:
        plans_dir = self.config.storage.plans_dir
        plans_dir.mkdir(parents=True, exist_ok=True)
        path = plans_dir / f"{plan.run_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        return path


def load_plan_file(path: str | Path) -> Plan:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return Plan.from_dict(data)
