from __future__ import annotations

from types import SimpleNamespace

from jobbot.greeting import build_greeting, template_greeting, validate_greeting
from jobbot.models import Job, MatchResult


def make_job(title="Java后端开发工程师") -> Job:
    return Job(platform="boss", job_id="j1", title=title, company="示例科技", salary="15-25K")


def make_match() -> MatchResult:
    return MatchResult(score=85, matched_skills=["Java", "PostgreSQL"])


class FakeAI:
    enabled = True
    config = SimpleNamespace(use_for_scoring=False)

    def __init__(self, text):
        self.text = text

    def write_greeting(self, job, match):
        return self.text


def test_template_contains_facts_and_fits_limit(profile):
    text = template_greeting(profile, make_job(), ["Java", "PostgreSQL"], max_chars=120)
    assert profile.name in text
    assert str(profile.years_experience) in text
    assert "Java" in text
    assert len(text) <= 120


def test_template_truncates_long_title(profile):
    text = template_greeting(profile, make_job("超长标题" * 30), [], max_chars=60)
    assert len(text) <= 60


def test_validate_rejects_placeholder(profile):
    ok, _, reason = validate_greeting(
        "您好，我是孙文昆，21年Java经验，看到{company}岗位很感兴趣", profile, make_job()
    )
    assert not ok
    assert "占位符" in reason


def test_validate_rejects_missing_name(profile):
    ok, _, reason = validate_greeting("您好，21年Java后端经验，方便聊聊吗？", profile, make_job())
    assert not ok
    assert "姓名" in reason


def test_validate_rejects_missing_skill(profile):
    ok, _, reason = validate_greeting("您好，我是孙文昆，21年开发经验，方便聊聊吗？", profile, make_job())
    assert not ok
    assert "技能" in reason


def test_validate_truncates_long_text(profile):
    text = "您好，我是孙文昆，21年Java后端经验，" + "非常匹配。" * 40
    ok, cleaned, _ = validate_greeting(text, profile, make_job(), max_chars=60)
    assert ok
    assert len(cleaned) <= 60


def test_build_greeting_uses_valid_ai_text(profile):
    ai = FakeAI("您好，我是孙文昆，21年Java后端经验，熟悉PostgreSQL，方便聊聊吗？")
    greeting = build_greeting(ai, profile, make_job(), make_match())
    assert greeting.source == "ai"
    assert greeting.text.startswith("您好，我是孙文昆")


def test_build_greeting_falls_back_on_invalid_ai(profile):
    ai = FakeAI("{placeholder}")
    greeting = build_greeting(ai, profile, make_job(), make_match())
    assert greeting.source == "template"
    assert profile.name in greeting.text
