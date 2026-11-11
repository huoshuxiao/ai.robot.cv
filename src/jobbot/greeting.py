from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Profile
from .models import Job, MatchResult

# 「槽一：」这一族是 11-06 平台实跑里模型把提示词内部槽位名抄进正文那一条（`run=boss1793939777` 第 3 条，
# 已送达无锡盖酷）加进来的。只认「槽＋序号」，不认泛化的「序号＋冒号」：后者会连「统一、规范化」这种
# 正常中文一起打掉（构造反例实测 4/5 误杀，见第 103 批），而误杀的落点是回退模板——不是拦投递。
_PLACEHOLDER_RE = re.compile(r"\{[^}]*\}|\[[^\]]*\]|XX+|xx+|公司名称|岗位名称|职位名称|槽[一二三四五六七八九十0-9]")
# 简历补充.md 标注的劣势反面对照：招呼语里出现这些说法就是在夸大，直接回退模板。
# 「非全日制」「不流利」是对劣势的如实描述，不算夸大。
_OVERCLAIM_RE = re.compile(
    r"(?<!非)全日制|学位证?|(?<!不)流利|母语级|口语无障碍|(?:英语|日语|外语)[^，。；]{0,6}(?:交流|沟通|会议)"
)
_SENTENCE_ENDS = ("。", "！", "？", "；")
_CLAUSE_ENDS = ("，", "、", ",", " ")


# 末句腰斩的名单：只收「必须带补语/宾语才能成句」的双字单位。
# 为什么不按单字判（在/到/于/和…）：同一枚单字既能挂残句（「错误率控制在。」）也能合法收尾
# （「工作氛围自由自在。」「他的考虑很周到。」），按单字判会把好句子拦死；拦下的落点是回退模板，
# 而模板是最千篇一律的那条 ⇒ 误杀比漏报更贵。11-06 本地实测里 V3 真跑出过前者，负对照就照它写。
_DANGLING_UNITS = (
    "控制在|限制在|停留在|关键在于|降低到|提升到|减少到|增加到|缩小到|扩大至|减少至|增加至|"
    "不低于|不高于|不超过|超过|大约|约为|旨在|属于|基于|位于|对于|至于|即使|即便|以及|并且|而且|或者|"
    "介于|区别于|适用于|依赖于|倾向于|相当于|等同于|聚焦于|着眼于|得益于|致力于|置身于|立足于|应用于|作用于"
)
_DANGLING_RE = re.compile(rf"(?:{_DANGLING_UNITS})\s*[。！？]?\s*$")
_TAIL_PILE_RE = re.compile(r"[，、；]\s*[。！？]\s*$")
_NO_TERMINATOR_RE = re.compile(r"[，、；,;：]\s*$")


def tail_defect(text: str) -> str:
    """末句读不通时返回一句缺陷说明，读得通返回空串。**只报不拦**：
    招呼语在校验之后还可能被模型自己收在半截短语上（11-06 实测 117 字那条以「错误率控制在。」收尾，
    而它在 120 窗口内，`validate_greeting` 的截断分支根本没进 ⇒ `校验过=True` 与「读得通」是两格）。
    这一格现在只进流水与尺子；要让它拦投递，得先裁"拦下之后走模板还是重问一次"。
    名单外的形状看不见 ⇒ 返回值只支持"至少这么多残"这种念法，不支持"一共几残"。
    """
    t = str(text or "").strip()
    if not t:
        return "空文本"
    m = _TAIL_PILE_RE.search(t)
    if m:
        return f"标点叠收尾「…{t[-10:]}」"
    m = _DANGLING_RE.search(t)
    if m:
        return f"末句以必须带补语的单位收尾「{m.group(0).strip()}」⇒…{t[-18:]}"
    if _NO_TERMINATOR_RE.search(t):
        return f"无句末标点收尾「…{t[-12:]}」"
    return ""


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
    # 模型原本写了多少字（0＝没问过模型）。有 `chars` 才知道一条 120 字的正文是**天生 120 字**，
    # 还是写了 300 字被我们砍到 120——后者是「简历内容体现不全」被截断**伪装成已完成**。
    raw_chars: int = 0
    # 是否真的动过刀：由 `validate_greeting` 那一支决定，不让下游用长度差去猜
    #（`clean_greeting` 会折空白、剥引号，未截断的正文本来就会比原文短几个字，猜就会误报）。
    truncated: bool = False
    # 回退的那一次，被作废的正文长什么样、为什么作废——不留这两格的话，磁盘上只剩「回退了」这个事实，
    # 而 11-06 生产重跑正卡在这上面：同一条岗连着 3 次「未包含任何简历技能」，却没有任何地方能看到那 3 句原文。
    reject_reason: str = ""
    reject_head: str = ""


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
    min_chars: int = 0,
) -> tuple[bool, str, str]:
    cleaned = clean_greeting(text)
    if not cleaned:
        return False, "", "AI 返回空文本"
    if _PLACEHOLDER_RE.search(cleaned):
        return False, cleaned, "含占位符"
    overclaim = _OVERCLAIM_RE.search(cleaned)
    if overclaim:
        return False, cleaned, f"含与档案劣势冲突的夸大表述「{overclaim.group(0)}」"
    if profile.name not in cleaned:
        return False, cleaned, "未包含姓名"
    # 畸形守卫（11-11）：模型偶发把「我是<姓名>，」复制两遍、或整句重复（上场上海那条实际发出过）。
    # 只拦畸形、正常语句不误伤；落点是回退模板，不是拦投递。
    if cleaned.count(f"我是{profile.name}") > 1:
        return False, cleaned, "姓名前缀重复（畸形）"
    _sents = [s.strip() for s in re.split(r"[。！？；]", cleaned) if len(s.strip()) >= 6]
    if len(_sents) != len(set(_sents)):
        return False, cleaned, "整句重复（畸形）"
    if not any(s.casefold() in cleaned.casefold() for s in profile.all_skills):
        return False, cleaned, "未包含任何简历技能"
    if len(cleaned) > max_chars:
        original = len(cleaned)
        cleaned = truncate_text(cleaned, max_chars)
        # 截断后连允许窗口的一半都不到：说明原文在窗口内没有可断的句界，内容被大段丢弃，算残稿。
        # 实测形状：模型写 141 字，窗口 120，只剩回第一段 43 字。
        if len(cleaned) * 2 < max_chars:
            return False, cleaned, f"截断后只剩 {len(cleaned)}/{original} 字（窗口 {max_chars}），判为残稿"
    if len(cleaned) < min_chars:
        return False, cleaned, f"招呼语过短（{len(cleaned)} 字 < {min_chars} 字）"
    return True, cleaned, ""


def build_greeting(
    ai_client,
    profile: Profile,
    job: Job,
    match: MatchResult,
    max_chars: int = 120,
    logger=None,
    min_chars: int = 0,
) -> Greeting:
    rejected, why = "", ""
    if ai_client is not None and getattr(ai_client, "enabled", False):
        candidate = ai_client.write_greeting(job, match)
        if candidate:
            ok, cleaned, reason = validate_greeting(candidate, profile, job, max_chars, min_chars)
            if ok:
                # 判定"有没有被截"用同一条干净化后的长度比：截断分支若跑过，cleaned 一定比
                # 只做过 clean_greeting 的候选短；不看这个就直接比原文长度，会把折空白误报成截断。
                return Greeting(cleaned, "ai", raw_chars=len(candidate),
                                truncated=len(cleaned) < len(clean_greeting(candidate)))
            if logger:
                logger.warning("AI 招呼语不可用（%s），回退模板", reason)
            rejected, why = cleaned, reason
        else:
            rejected, why = "", "AI 返回空文本"
    return Greeting(template_greeting(profile, job, match.matched_skills, max_chars), "template",
                    reject_reason=why, reject_head=rejected[:120])
