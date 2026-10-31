from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Profile
from .models import Job, MatchResult

_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}|\[[^\]]*\]|XX+|xx+|公司名称|岗位名称|职位名称")
_SENTENCE_ENDS = ("。", "！", "？", "；")
_CLAUSE_ENDS = ("，", "、", ",", " ")


def truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    window = text[:max_chars]
    threshold = max(1, max_chars // 4)
    sentence = max(window.rfind(p) for p in _SENTENCE_ENDS)
    if sentence >= threshold:
        return window[: sentence + 1]
    clause = max(window.rfind(p) for p in _CLAUSE_ENDS)
    if clause >= threshold:
        return window[:clause].rstrip("，,；;、 ") + "。"
    return window[: max_chars - 1].rstrip("，,；;、 ") + "。"


@dataclass
class Greeting:
    text: str
    source: str


def clean_greeting(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    return text.strip("\"'“”‘’「」")


def template_greeting(
    profile: Profile,
    job: Job,
    matched_skills: list[str],
    max_chars: int = 120,
) -> str:
    target = profile.target_title.replace("工程师", "").strip() or "后端开发"
    skills = list(dict.fromkeys([*matched_skills, *profile.skills_core]))
    title = job.title.strip() or "相关岗位"

    def build(skill_count: int, title_text: str) -> str:
        chosen = "、".join(skills[:skill_count]) if skill_count else ""
        skill_part = f"，擅长{chosen}" if chosen else ""
        return (
            f"您好，我是{profile.name}，{profile.years_experience}年{target}经验{skill_part}。"
            f"看到「{title_text}」岗位与我的经历匹配，方便聊聊吗？"
        )

    for count in (2, 1, 0):
        text = build(count, title[:24])
        if len(text) <= max_chars:
            return text
    text = build(0, title[:16])
    return truncate_text(text, max_chars)


def validate_greeting(
    text: str,
    profile: Profile,
    job: Job,
    max_chars: int = 120,
) -> tuple[bool, str, str]:
    cleaned = clean_greeting(text)
    if not cleaned:
        return False, "", "AI 返回空文本"
    if _PLACEHOLDER_RE.search(cleaned):
        return False, cleaned, "含占位符"
    if profile.name not in cleaned:
        return False, cleaned, "未包含姓名"
    if not any(s.casefold() in cleaned.casefold() for s in profile.all_skills):
        return False, cleaned, "未包含任何简历技能"
    if len(cleaned) > max_chars:
        cleaned = truncate_text(cleaned, max_chars)
    return True, cleaned, ""


def build_greeting(
    ai_client,
    profile: Profile,
    job: Job,
    match: MatchResult,
    max_chars: int = 120,
    logger=None,
) -> Greeting:
    if ai_client is not None and getattr(ai_client, "enabled", False):
        candidate = ai_client.write_greeting(job, match)
        if candidate:
            ok, cleaned, reason = validate_greeting(candidate, profile, job, max_chars)
            if ok:
                return Greeting(cleaned, "ai")
            if logger:
                logger.warning("AI 招呼语不可用（%s），回退模板", reason)
    return Greeting(template_greeting(profile, job, match.matched_skills, max_chars), "template")
