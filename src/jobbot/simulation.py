from __future__ import annotations

import re
from dataclasses import asdict
from datetime import datetime
from typing import Any

from .config import Config, Profile
from .greeting import template_greeting
from .matcher import score_job
from .models import Job
from .storage import Storage, iso, now_local


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def simulate_jobs(
    cfg: Config,
    profile: Profile,
    storage: Storage,
    jobs: list[Job],
    limit: int | None = None,
    window_days: int | None = None,
) -> dict[str, Any]:
    pc = cfg.platform("boss")
    window = window_days if window_days is not None else cfg.defaults.dedupe_window_days
    run_limit = pc.limit_per_run if limit is None else max(0, int(limit))
    candidates: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen: set[str] = set()
    batch_keys: set[tuple[str, str]] = set()

    def skip(job: Job, reason: str, score: int = 0, risks: list[str] | None = None) -> None:
        skipped.append(
            {
                "job_id": job.job_id,
                "title": job.title,
                "company": job.company,
                "score": score,
                "reason": reason,
                "risks": list(risks or []),
            }
        )

    for job in jobs:
        if job.job_id in seen:
            skip(job, "本轮重复职位")
            continue
        seen.add(job.job_id)

        duplicate, reason = storage.is_duplicate(
            pc.name, job.job_id, job.company, job.title, window
        )
        if duplicate:
            skip(job, reason)
            continue

        key = (_norm(job.company), _norm(job.title))
        if key in batch_keys:
            skip(job, "本轮同公司同职位重复")
            continue

        city = str(job.raw.get("search_city") or pc.city)
        match = score_job(job, profile, cfg.search, city)
        if match.skipped or match.score < cfg.defaults.min_score:
            if match.skipped:
                reason = match.reasons[0] if match.reasons else "命中排除条件"
            else:
                reason = f"得分 {match.score} 低于阈值 {cfg.defaults.min_score}"
            skip(job, reason, match.score, match.risks)
            continue

        batch_keys.add(key)
        candidates.append({"job": job, "match": match})

    candidates.sort(key=lambda item: (-item["match"].score, item["job"].company))
    selected_items = candidates[:run_limit]
    for item in candidates[run_limit:]:
        skip(item["job"], "超出模拟限额", item["match"].score)

    selected = []
    for item in selected_items:
        greeting = template_greeting(
            profile,
            item["job"],
            item["match"].matched_skills,
            cfg.ai.max_greeting_chars,
        )
        selected.append(
            {
                "job": asdict(item["job"]),
                "score": item["match"].score,
                "reasons": item["match"].reasons,
                "risks": item["match"].risks,
                "greeting": greeting,
                "greeting_source": "template",
            }
        )

    sent_today = storage.count_sent_today(pc.name)
    day_remaining = max(0, pc.limit_per_day - sent_today)
    return {
        "generated_at": iso(now_local()),
        "platform": pc.name,
        "cities": [city.name for city in pc.search_cities],
        "window_days": window,
        "limit": run_limit,
        "sent_today": sent_today,
        "day_remaining": day_remaining,
        "would_send_now": min(len(selected), day_remaining),
        "selected": selected,
        "skipped": skipped,
        "simulated": True,
    }


def write_simulation(cfg: Config, result: dict[str, Any]) -> str:
    out_dir = cfg.storage.reports_dir.parent / "simulations"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = out_dir / f"sim-{stamp}.json"
    import json

    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)
