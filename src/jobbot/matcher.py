from __future__ import annotations

import re
from collections.abc import Iterable

from .config import Profile, SearchConfig
from .models import Job, MatchResult

CORE_WEIGHT = 8
FAMILIAR_WEIGHT = 4
SKILL_CAP = 40
# 「JD 到手了没有」的门槛：低于这个字数就没资格判技能重合度（详见 score_job 里那两条硬闸的注释）。
MIN_JD_CHARS = 40
AGE_PENALTY = 20
EDUCATION_PENALTY = 20
LANGUAGE_PENALTY = 15

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
    text = decode_salary_font(text)
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


_AGE_RANGE_RE = re.compile(r"(\d{2})\s*[-~—–至]\s*(\d{2})\s*岁")
_AGE_CAP_RES = (
    re.compile(r"(\d{2})\s*岁(?:及)?(?:以下|以内)"),
    re.compile(r"(?:不超过|不高于|需在|限)\s*(\d{2})\s*岁"),
)

# 「年龄／学历／外语」三档劣势各由这些类别词开启；简历补充.md 删掉一档即不再检查。
_WEAKNESS_AXIS_TERMS = {
    "age": ("年龄", "年纪"),
    "education": ("学历", "学位"),
    "language": ("外语", "英语", "日语"),
}


def weakness_axes(weaknesses: Iterable[str]) -> set[str]:
    axes: set[str] = set()
    for item in weaknesses:
        squashed = _squash(item)
        for axis, terms in _WEAKNESS_AXIS_TERMS.items():
            if any(term in squashed for term in terms):
                axes.add(axis)
    return axes


def parse_age_cap(tags: Iterable[str], description: str) -> int | None:
    """岗位写明的年龄上限；「25-35岁」取上沿 35，「35岁以下」取 35；未写则 None。"""
    text = " ".join([*(t for t in tags if t), description[:800]])
    range_match = _AGE_RANGE_RE.search(text)
    if range_match:
        return int(range_match.group(2))
    for pattern in _AGE_CAP_RES:
        match = pattern.search(text)
        if match:
            return int(match.group(1))
    return None


# 命中即说明岗位硬性要求候选人给不出的东西；「优先／不限／可接受」同一小句内不算硬要求。
_SOFTENING_RE = re.compile(r"优先|可接受|不限|放宽|无学位")
EDUCATION_CONFLICT_RES = (
    re.compile(r"(?<!非)全日制"),
    re.compile(r"统招"),
    re.compile(r"学位"),
)
LANGUAGE_CONFLICT_RES = (
    re.compile(r"(?:英语|日语|外语|口语)[^。；;]{0,12}流利|流利[^。；;]{0,12}(?:英语|日语|外语|口语|听说)"),
    re.compile(r"(?:英语|日语|外语)[^。；;]{0,10}(?:口语|会话|听说|工作语言|电话会议|邮件沟通)"),
    re.compile(r"(?:口语|听说)[^。；;]{0,8}(?:流利|熟练|无障碍|作为工作语言)"),
    re.compile(r"(?:英语|日语)(?:n1|n2|一级|二级|专八|雅思|托福)"),
)


def find_hard_requirement(text: str, patterns: Iterable[re.Pattern]) -> str:
    """返回第一条「硬要求」命中的原文小片段；没有命中返回空串。"""
    for pattern in patterns:
        for match in pattern.finditer(text):
            window = text[max(0, match.start() - 12) : match.end() + 12]
            if _SOFTENING_RE.search(window):
                continue
            return match.group(0)
    return ""


def _skill_hits(job: Job, skills: Iterable[str]) -> list[str]:
    text = " ".join([job.title, *job.tags, job.description])
    hits: list[str] = []
    for skill in skills:
        if contains_term(text, skill):
            hits.append(skill)
    return hits


_PUA_RE = re.compile("[\\ue000-\\uf8ff]")
_DIGITS_RE = re.compile(r"\d+")
# BOSS 的 kanzhun-mix 字体把 0-9 画成 U+E031-U+E03A；映射已用该字体逐字渲染核对。
_BOSS_SALARY_FONT = str.maketrans({chr(0xE031 + i): str(i) for i in range(10)})


def decode_salary_font(text: str) -> str:
    """把 BOSS 薪资字体里的私用区数字还原成 ASCII 数字；其他字符原样保留。"""
    return (text or "").translate(_BOSS_SALARY_FONT)


def salary_is_obfuscated(text: str) -> bool:
    """BOSS 用私有字体画薪资数字：抓到的是 U+E000–F8FF 私用区码位，不是「面议」。"""
    return bool(_PUA_RE.search(text or ""))


def salary_font_conflict(job: Job) -> str:
    """两套解码对表：扩展按页面真字形形状现解，本机按写死的基码表解。

    只有「扩展真的解成了 ASCII 数字」才谈得上不一致；比对只看**数字序列**——
    `K`、`·13薪`、空白这些非数字部分两侧本来就可能不一样，按整串比会误报
    （2026-11-04 本地集成夹具就是被重复的一段「·13薪」顶出一条假冲突）。
    表解不出全 10 个数字（基码移出 e031-e03a）时也不报，交给「本机未取到」那一档。
    """
    raw = job.salary_raw or ""
    if not _PUA_RE.search(raw):
        return ""
    table = decode_salary_font(raw)
    sent = (job.salary or "").strip()
    if _PUA_RE.search(sent):
        return ""
    table_digits = _DIGITS_RE.findall(table)
    sent_digits = _DIGITS_RE.findall(sent)
    if not table_digits or not sent_digits or table_digits == sent_digits:
        return ""
    return f"薪资字体两套解码不一致：页面原串本机表解出数字 {table_digits}，扩展按字形解出数字 {sent_digits}"


def score_job(job: Job, profile: Profile, search: SearchConfig, city: str) -> MatchResult:
    # 排除词只看岗位身份三要素（标题/公司名/标签），不看职位正文：
    # 11-04 平台实跑有两条纯 Java 岗被正文里的福利条款打死——富智「厚生年金、健康保险（日本法定社保）」、
    # 连英弘「保险齐全」，判 0 分跳过；抹掉正文它们分别是 61、77（都过 60 闸）。
    # 扩展侧 `whyRejected()` 本来就只比公司名与标题/标签，两层口径在这里对齐。
    # 排除词分两档范围（2026-11-04 裁定）：所有词都看岗位身份三要素（标题/公司名/标签）；
    # 只有 `exclude_keywords_in_jd` 里那几个正文中出现即无歧义的词（工作地、身份、岗位，如「长期驻外」「只招应届」）
    # 才连职位正文一起拦。反例来自当天实跑：正文里的福利条款「保险齐全／健康保险（日本法定社保）」
    # 把富智（12-24K）与大连连英弘（18-22K）两条纯 Java 岗判成 0 分。
    haystack = " ".join([job.title, job.company, *job.tags])
    for bad in search.exclude_keywords:
        if contains_term(haystack, bad):
            return MatchResult(
                score=0,
                reasons=[f"命中排除词「{bad}」，主动跳过"],
                risks=[],
                skipped=True,
            )
    for bad in search.exclude_keywords_in_jd:
        if contains_term(job.description, bad):
            return MatchResult(
                score=0,
                reasons=[f"岗位正文命中排除词「{bad}」，主动跳过"],
                risks=[],
                skipped=True,
            )

    # job_types 是岗位类型白名单（默认只有全职）：身份三要素带非白名单类型标记的主动跳过。
    # 2026-11-07 日志驱动：这钮此前只在 config.py 解析、从没接线，「Java开发（短期4-6个月）」因此过闸被发送。
    for marker in ("短期", "兼职", "实习"):
        if marker not in search.job_types and contains_term(haystack, marker):
            allowed = "/".join(search.job_types)
            return MatchResult(
                score=0,
                reasons=[f"岗位带「{marker}」类型标记，不在 job_types 白名单（{allowed}），主动跳过"],
                risks=[],
                skipped=True,
            )

    reasons: list[str] = []
    risks: list[str] = []
    score = 0
    # 技能/标题两把硬闸不在这里直接 return：那条 18:51 定下的口径是「分数照算、只是拦下」，
    # 提前 return 会让台账只剩一个 0 分，事后看不出这条岗差多少（第 2 格夹具就是这么把它抓出来的）。
    hard_block: list[str] = []

    conflict = salary_font_conflict(job)
    if conflict:
        return MatchResult(
            score=0,
            reasons=[f"{conflict} ⇒ 薪资不可信，按 0 分拦下，需人工确认字体版本"],
            risks=[conflict],
            skipped=True,
        )

    core_hits = _skill_hits(job, profile.skills_core)
    familiar_hits = [s for s in _skill_hits(job, profile.skills_familiar) if s not in core_hits]
    matched = [*core_hits, *familiar_hits]
    skill_score = min(SKILL_CAP, len(core_hits) * CORE_WEIGHT + len(familiar_hits) * FAMILIAR_WEIGHT)
    if skill_score:
        score += skill_score
        shown = "、".join(matched[:4])
        reasons.append(f"技能命中 {shown}（+{skill_score}）")
    elif len(job.description) >= MIN_JD_CHARS:
        # 2026-11-04 裁定「要。职位匹配，技能匹配」⇒ 重合度低从「只挂 risk」升成硬拦。
        # 但只在 JD 真到手时判：当日 19 条真岗位里 14 条压根没正文，那 0 命中是「没拿到 JD」不是「技能不匹配」；
        # 不看正文就拦，详情面板没换过来的那一场会被整场清空（下午那场 stale=8 正是这个形状）。
        detail = f"本岗位 JD 已到手（{len(job.description)} 字），简历技能与它一条都没重合 ⇒ 硬拦，不投递"
        risks.append("简历技能与岗位描述重合度低")
        hard_block.append(detail)
    else:
        risks.append("没拿到本岗位 JD，判不了技能重合度（这一条没进硬闸，按卡片信息放行）")

    title_lower = job.title.casefold()
    if any(k in title_lower for k in ("java", "后端", "服务端", "开发工程师")):
        score += 15
        reasons.append("岗位标题匹配 Java 后端方向（+15）")
    elif "工程师" in title_lower:
        score += 8
        reasons.append("岗位为工程师序列（+8）")
    else:
        # 「职位匹配」那一半（2026-11-04 裁定）：标题既不含目标方向词、也不是工程师序列 ⇒ 硬拦。
        # 中间那一档（如「测试工程师」，+8）不拦——它是「同序列但偏方向」，拦下去的代价与收益不对等。
        # 标题这一档不需要 JD：标题一直在手上，取不取得到正文都判得了。
        risks.append(f"岗位标题「{job.title}」与目标方向偏差较大")
        hard_block.append(f"岗位标题「{job.title}」既不含目标方向词也不是工程师序列 ⇒ 硬拦，不投递")
    if any(k in title_lower for k in ("架构师", "技术经理", "项目经理", "负责人", "组长")):
        risks.append("标题偏管理/架构，可能要求更高或偏管理职责")

    salary = parse_salary(job.salary)
    want_low, want_high = profile.expected_salary_k
    if salary is None:
        score += 5
        if salary_is_obfuscated(job.salary):
            risks.append("薪资数字被平台字体加密，本机未取到（不是面议，需人工看一眼）")
        else:
            risks.append("薪资未明确（面议），需进一步确认")
    else:
        job_low, job_high = salary
        # salary_floor_k 只能放宽命中口径（上限贴边算命中），设到期望下限之上不会反向收紧评分。
        floor = want_low
        if search.salary_floor_k is not None:
            floor = min(int(search.salary_floor_k), want_low)
        # AGENTS.md：大连以外地区薪资 25K 起——非本城把下限抬到 salary_floor_non_local_k（只收紧、不影响本城）。
        if search.salary_floor_non_local_k is not None and city != search.local_city:
            floor = max(floor, int(search.salary_floor_non_local_k))
        if job_high < floor:
            # 2026-11-04 决定：低于可接受下限的岗位直接拦下，不进计划也不发送（只挂 risk 会被分数闸放行）。
            detail = f"薪资上限 {job_high}K 低于可接受下限 {floor}K（期望 {want_low}-{want_high}K）"
            risks.append(detail)
            reasons.insert(0, f"{detail} ⇒ 硬拦，不投递")
            return MatchResult(
                score=score,
                reasons=reasons,
                risks=risks,
                matched_skills=matched,
                skipped=True,
            )
        elif job_low > want_high:
            score += 12
            reasons.append(f"薪资 {job_low}-{job_high}K 高于期望上限（+12）")
            risks.append("薪资明显高于期望，岗位要求可能更高")
        elif job_high < want_low:
            score += 20
            reasons.append(f"薪资上限 {job_high}K 贴边（按可接受下限 {floor}K 起算）算命中（+20）")
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
        detail = f"工作地点「{location}」不在目标城市「{city}」"
        risks.append(detail)
        hard_block.append(f"{detail} ⇒ 硬拦，不投递")

    if len(job.description) >= 50:
        score += 3
        reasons.append("已获取职位详情（+3）")

    axes = weakness_axes(profile.weaknesses)
    jd_text = _squash(" ".join([job.title, *job.tags, job.description]))
    if "age" in axes:
        age_cap = parse_age_cap(job.tags, job.description)
        if age_cap is not None and age_cap < profile.age:
            score -= AGE_PENALTY
            detail = f"岗位年龄上限 {age_cap} 岁，候选人 {profile.age} 岁（劣势：年龄）"
            risks.append(f"{detail}，-{AGE_PENALTY}")
            hard_block.append(f"{detail} ⇒ 硬拦，不投递")
    if "education" in axes:
        hit = find_hard_requirement(jd_text, EDUCATION_CONFLICT_RES)
        if hit:
            score -= EDUCATION_PENALTY
            detail = f"岗位要求「{hit}」，候选人为非全日制且无学位证（劣势：学历）"
            risks.append(f"{detail}，-{EDUCATION_PENALTY}")
            hard_block.append(f"{detail} ⇒ 硬拦，不投递")
    if "language" in axes:
        hit = find_hard_requirement(jd_text, LANGUAGE_CONFLICT_RES)
        if hit:
            score -= LANGUAGE_PENALTY
            detail = f"岗位要求「{hit}」，候选人外语仅可读写（劣势：外语）"
            risks.append(f"{detail}，-{LANGUAGE_PENALTY}")
            hard_block.append(f"{detail} ⇒ 硬拦，不投递")

    score = max(0, min(100, score))
    if hard_block:
        # 分数一路照算到这里才拦：台账/报表看得见「这条差多少」，而不是一行 0 分。
        # 所有硬拦明细（技能/标题/工作地/学历/外语）统一在此 prepend 进 reasons。
        for detail in reversed(hard_block):
            reasons.insert(0, detail)
        return MatchResult(
            score=score,
            reasons=reasons,
            risks=risks,
            matched_skills=matched,
            rule_score=score,
            skipped=True,
        )
    return MatchResult(
        score=score,
        reasons=reasons,
        risks=risks,
        matched_skills=matched,
        rule_score=score,
    )


RULE_WEIGHT = 0.4
AI_WEIGHT = 0.6


def merge_ai_score(rule_score: int, ai_score: int) -> int:
    """合并分 = 规则分 × 40% + AI 精排分 × 60%（0-100 截断后取整）。

    2026-11-10 由 60/40 调成 40/60：把排序话语权交给 AI 精排（用户裁定「排序交给 AI」）。
    """
    merged = round(rule_score * RULE_WEIGHT + max(0, min(100, ai_score)) * AI_WEIGHT)
    return max(0, min(100, merged))
