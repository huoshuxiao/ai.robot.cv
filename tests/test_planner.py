from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

from jobbot.adapters.fixture import FixtureAdapter
from jobbot.planner import Planner
from jobbot.storage import Storage

FIXED = datetime(2026, 10, 31, 12, 0, 0).astimezone()


def make_planner(cfg, profile, ai=None):
    storage = Storage(cfg.storage.db)
    pc = cfg.platform("boss")
    adapter = FixtureAdapter(
        pc,
        cfg.search,
        cfg.defaults,
        profile,
        jobs_path=cfg.root / "examples" / "sample_jobs.json",
    )
    planner = Planner(cfg, profile, storage, adapter, ai, clock=lambda: FIXED)
    return planner, storage, adapter


def test_plan_selects_five_and_records_skips(tmp_cfg, tmp_profile):
    planner, storage, _ = make_planner(tmp_cfg, tmp_profile)
    plan, path = planner.build_plan("boss")

    assert len(plan.items) == 5
    assert plan.limit == 5
    assert path.exists()
    scores = [item.match.score for item in plan.items]
    assert scores == sorted(scores, reverse=True)
    assert all(item.match.score >= 60 for item in plan.items)
    assert all(item.greeting and item.greeting_source == "template" for item in plan.items)
    assert all(item.job.job_id not in ("demo-sales-001", "demo-java-006", "demo-java-007") for item in plan.items)

    reasons = " ".join(item["reason"] for item in plan.skipped)
    assert "排除词" in reasons
    assert "已沟通" in reasons
    assert "本轮重复职位" in reasons
    assert "得分" in reasons
    assert storage.status_counts("boss") == {"planned": 5}


def test_limit_override(tmp_cfg, tmp_profile):
    planner, _, _ = make_planner(tmp_cfg, tmp_profile)
    plan, _ = planner.build_plan("boss", limit=2)
    assert len(plan.items) == 2
    assert plan.limit == 2


def test_existing_plan_blocks_duplicates(tmp_cfg, tmp_profile):
    planner, _, _ = make_planner(tmp_cfg, tmp_profile)
    first, _ = planner.build_plan("boss")
    assert len(first.items) == 5
    second, _ = planner.build_plan("boss")
    assert second.items == []
    assert any("计划" in item["reason"] for item in second.skipped)


def test_daily_quota_stops_planning(tmp_cfg, tmp_profile):
    planner, storage, _ = make_planner(tmp_cfg, tmp_profile)
    plan, _ = planner.build_plan("boss")
    for item in plan.items:
        storage.mark_sent("boss", item.job.job_id, "test")
    assert storage.count_sent_today("boss") == 5

    blocked, _ = planner.build_plan("boss")
    assert blocked.items == []
    assert "配额" in blocked.meta.get("reason", "")


def test_platform_day_limit_is_respected(project, tmp_profile, mutate_cfg):
    cfg = mutate_cfg(lambda data: data["platforms"]["boss"].__setitem__("limit_per_day", 2))
    planner, _, _ = make_planner(cfg, tmp_profile)
    plan, _ = planner.build_plan("boss")
    assert len(plan.items) == 2


class FakeAI:
    enabled = True
    config = SimpleNamespace(use_for_scoring=True)

    def score_job(self, job, match):
        return 100, ["AI 认为高度匹配"], []

    def write_greeting(self, job, match):
        return "您好，我是孙文昆，21年Java后端经验，熟悉Spring Boot，看到岗位很匹配，方便聊聊吗？"


def test_ai_scores_and_greetings_are_used(tmp_cfg, tmp_profile):
    planner, _, _ = make_planner(tmp_cfg, tmp_profile, ai=FakeAI())
    plan, _ = planner.build_plan("boss")
    assert len(plan.items) == 5
    assert all(item.match.ai_score == 100 for item in plan.items)
    assert all(item.greeting_source == "ai" for item in plan.items)
    assert all(item.match.score >= item.match.rule_score for item in plan.items)
    assert any("AI：" in reason for item in plan.items for reason in item.match.reasons)


class CountingAdapter(FixtureAdapter):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fetch_calls = 0

    def fetch_detail(self, job):
        self.fetch_calls += 1
        return job


def test_short_description_triggers_detail_fetch(tmp_cfg, tmp_profile, tmp_path):
    jobs = {
        "jobs": [
            {
                "job_id": "short-1",
                "title": "Java后端开发工程师",
                "company": "示例公司",
                "salary": "15-25K",
                "location": "大连",
                "tags": ["Java", "Spring Boot", "3-5年"],
                "description": "Java",
                "url": "https://example.com/short-1",
            }
        ]
    }
    path = tmp_path / "jobs.json"
    path.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
    storage = Storage(tmp_cfg.storage.db)
    pc = tmp_cfg.platform("boss")
    adapter = CountingAdapter(pc, tmp_cfg.search, tmp_cfg.defaults, tmp_profile, jobs_path=path)
    planner = Planner(tmp_cfg, tmp_profile, storage, adapter, clock=lambda: FIXED)
    plan, _ = planner.build_plan("boss")
    assert adapter.fetch_calls == 1
    assert len(plan.items) == 1
