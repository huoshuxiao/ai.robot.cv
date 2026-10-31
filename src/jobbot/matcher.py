from __future__ import annotations

import re
from collections.abc import Iterable

from .config import Profile, SearchConfig
from .models import Job, MatchResult

CORE_WEIGHT = 8
FAMILIAR_WEIGHT = 4
SKILL_CAP = 40

_SALARY_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*([万千kK])?\s*[-~—–至]\s*(\d+(?:\.\d+)?)\s*([万千kK])?"
)
_SINGLE_SALARY_RE = re.compile(r"(\d+(?:\.\d+)?)\s*([万千kK])\s*以上")


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold()


def contains_term(text: str, term: str) -> bool:
    squashed_term = _squash(term)
    if not squashed_term:
        return False
    squashed_text = _squash(text)
    if re.fullmatch(r"[a-z0-9+#.]+", squashed_term):
        return re.search(
            rf"(?<![a-z0-9]){re.escape(squashed_term)}(?![a-z0-9])", squashed_text
        ) is not None
    return squashed_term in squashed_text


def _to_k(value: float, unit: str | None) -> int:
    if unit == "万":
        return int(round(value * 10))
    if unit == "千":
        return int(round(value))
    if unit in ("k", "K"):
        return int(round(value))
    if value >= 1000:
        return int(round(value / 1000))
    return int(round(value))


def parse_salary(text: str) -> tuple[int, int] | None:
    if not text or "面议" in text:
        return None
    match = _SALARY_RE.search(text)
    if match:
        low_raw, low_unit, high_raw, high_unit = match.groups()
        unit = high_unit or low_unit
        low = _to_k(float(low_raw), low_unit or unit)
        high = _to_k(float(high_raw), high_unit or unit)
        if low > high:
            low, high = high, low
        return low, high
    single = _SINGLE_SALARY_RE.search(text)
    if single:
        value = _to_k(float(single.group(1)), single.group(2))
        return value, value
    return None


def parse_experience_requirement(tags: Iterable[str], description: str) -> int | None:
    text = " ".join(t for t in tags if t)
    if not text:
        text = description[:300]
    if "经验不限" in text or "应届" in text:
        return 0
    range_match = re.search(r"(\d+)\s*[-~—–至]\s*(\d+)\s*年", text)
    if range_match:
        return int(range_match.group(2))
    plus_match = re.search(r"(\d+)\s*年以上", text)
    if plus_match:
        return int(plus_match.group(1))
    return None


def _skill_hits(job: Job, skills: Iterable[str]) -> list[str]:
    text = " ".join([job.title, *job.tags, job.description])
    hits: list[str] = []
    for skill in skills:
        if contains_term(text, skill):
            hits.append(skill)
    return hits


def score_job(job: Job, profile: Profile, search: SearchConfig, city: str) -> MatchResult:
    haystack = " ".join([job.title, job.company, *job.tags, job.description])
    for bad in search.exclude_keywords:
        if contains_term(haystack, bad):
            return MatchResult(
                score=0,
                reasons=[f"命中排除词「{bad}」，主动跳过"],
                risks=[],
                skipped=True,
            )

    reasons: list[str] = []
    risks: list[str] = []
    score = 0

    core_hits = _skill_hits(job, profile.skills_core)
    familiar_hits = [s for s in _skill_hits(job, profile.skills_familiar) if s not in core_hits]
    matched = [*core_hits, *familiar_hits]
    skill_score = min(SKILL_CAP, len(core_hits) * CORE_WEIGHT + len(familiar_hits) * FAMILIAR_WEIGHT)
    if skill_score:
        score += skill_score
        shown = "、".join(matched[:4])
        reasons.append(f"技能命中 {shown}（+{skill_score}）")
    else:
        risks.append("简历技能与岗位描述重合度低")

    title_lower = job.title.casefold()
    if any(k in title_lower for k in ("java", "后端", "服务端", "开发工程师")):
        score += 15
        reasons.append("岗位标题匹配 Java 后端方向（+15）")
    elif "工程师" in title_lower:
        score += 8
        reasons.append("岗位为工程师序列（+8）")
    else:
        risks.append(f"岗位标题「{job.title}」与目标方向偏差较大")
    if any(k in title_lower for k in ("架构师", "技术经理", "项目经理", "负责人", "组长")):
        risks.append("标题偏管理/架构，可能要求更高或偏管理职责")

    salary = parse_salary(job.salary)
    want_low, want_high = profile.expected_salary_k
    if salary is None:
        score += 5
        risks.append("薪资未明确（面议），需进一步确认")
    else:
        job_low, job_high = salary
        if job_high < want_low:
            risks.append(f"薪资上限 {job_high}K 低于期望下限 {want_low}K")
        elif job_low > want_high:
            score += 12
            reasons.append(f"薪资 {job_low}-{job_high}K 高于期望上限（+12）")
            risks.append("薪资明显高于期望，岗位要求可能更高")
        else:
            score += 20
            reasons.append(f"薪资 {job_low}-{job_high}K 与期望 {want_low}-{want_high}K 有交集（+20）")

    required_years = parse_experience_requirement(job.tags, job.description)
    if required_years is None:
        pass
    elif required_years > profile.years_experience:
        score -= 8
        risks.append(f"要求 {required_years} 年经验，超过候选人 {profile.years_experience} 年")
    else:
        score += 10
        reasons.append(f"经验要求 {required_years} 年，候选人 {profile.years_experience} 年可覆盖（+10）")

    location = job.location.strip()
    if not location:
        score += 4
        reasons.append("工作地点未标注（+4）")
    elif city and city in location:
        score += 8
        reasons.append(f"工作地点在{city}（+8）")
    elif location:
        risks.append(f"工作地点「{location}」与目标城市「{city}」不一致")

    if len(job.description) >= 50:
        score += 3
        reasons.append("已获取职位详情（+3）")

    score = max(0, min(100, score))
    return MatchResult(
        score=score,
        reasons=reasons,
        risks=risks,
        matched_skills=matched,
        rule_score=score,
    )


def merge_ai_score(rule_score: int, ai_score: int) -> int:
    merged = round(rule_score * 0.7 + max(0, min(100, ai_score)) * 0.3)
    return max(0, min(100, merged))
