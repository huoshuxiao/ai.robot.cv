from __future__ import annotations

import pytest

from jobbot.config import SearchConfig
from jobbot.matcher import (
    contains_term,
    merge_ai_score,
    parse_experience_requirement,
    parse_salary,
    score_job,
)
from jobbot.models import Job


@pytest.fixture
def search():
    return SearchConfig(
        keywords=("Java",),
        exclude_keywords=("销售", "保险", "实习"),
        salary_range_k=(13, 26),
    )


def make_job(**kwargs) -> Job:
    data = {
        "platform": "boss",
        "job_id": "j1",
        "title": "Java后端开发工程师",
        "company": "示例科技",
        "salary": "15-25K",
        "location": "大连·高新园区",
        "tags": ["Java", "Spring Boot", "PostgreSQL", "5-10年"],
        "description": "负责 Java 后端开发，使用 Spring Boot 与 PostgreSQL，参与微服务架构设计。",
    }
    data.update(kwargs)
    return Job(**data)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("15-25K", (15, 25)),
        ("15-25K·13薪", (15, 25)),
        ("8千-1.2万", (8, 12)),
        ("8000-12000", (8, 12)),
        ("1.5-2万", (15, 20)),
        ("20K以上", (20, 20)),
        ("面议", None),
        ("", None),
    ],
)
def test_parse_salary(text, expected):
    assert parse_salary(text) == expected


@pytest.mark.parametrize(
    "tags, expected",
    [
        (["3-5年"], 5),
        (["5-10年"], 10),
        (["10年以上"], 10),
        (["经验不限"], 0),
        (["应届"], 0),
        ([], None),
    ],
)
def test_parse_experience(tags, expected):
    assert parse_experience_requirement(tags, "无") == expected


def test_short_ascii_terms_use_word_boundaries():
    assert contains_term("谷歌 Google 后端", "Go") is False
    assert contains_term("Go 语言后端", "Go") is True
    assert contains_term("JavaScript 前端", "Java") is False
    assert contains_term("Java 后端", "Java") is True


def test_strong_job_scores_high(profile, search):
    result = score_job(make_job(), profile, search, "大连")
    assert result.score >= 85
    assert not result.skipped
    assert "Java" in result.matched_skills
    assert "PostgreSQL" in result.matched_skills
    assert any("薪资" in reason for reason in result.reasons)


def test_excluded_keyword_is_skipped(profile, search):
    result = score_job(make_job(title="销售代表", description="销售岗位"), profile, search, "大连")
    assert result.skipped
    assert result.score == 0
    assert "排除词" in result.reasons[0]


def test_low_salary_adds_risk(profile, search):
    result = score_job(make_job(salary="5-8K"), profile, search, "大连")
    assert any("低于期望" in risk for risk in result.risks)


def test_experience_over_candidate_adds_risk(profile, search):
    result = score_job(make_job(tags=["25年以上"]), profile, search, "大连")
    assert any("超过候选人" in risk for risk in result.risks)


def test_other_city_is_a_risk(profile, search):
    result = score_job(make_job(location="北京·朝阳区"), profile, search, "大连")
    assert any("目标城市" in risk for risk in result.risks)


def test_merge_ai_score():
    assert merge_ai_score(80, 100) == 86
    assert merge_ai_score(0, 100) == 30
    assert merge_ai_score(100, 0) == 70
