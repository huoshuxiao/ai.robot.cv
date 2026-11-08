from __future__ import annotations

import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path

from .models import Plan
from .storage import Storage

STATUS_LABELS = {
    "planned": "待投递",
    "sent": "已投递",
    "failed": "失败",
    "unknown": "待确认",
    "requires_action": "需人工",
    "skipped": "已拦下（未发送）",
}


def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(text))


def _clip(text: str, width: int) -> str:
    text = str(text)
    if _width(text) <= width:
        return text
    out = ""
    used = 0
    for ch in text:
        w = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + w > width - 1:
            break
        out += ch
        used += w
    return out + "…"


def _pad(text: str, width: int) -> str:
    text = _clip(text, width)
    return text + " " * max(0, width - _width(text))


def _skip_category(reason: str) -> str:
    if "已投递" in reason or "计划" in reason:
        return "已在台账"
    if "平台显示" in reason:
        return "平台已沟通"
    if "排除词" in reason:
        return "硬过滤"
    if "限额" in reason or "排名" in reason:
        return "限额/排名"
    if "详情" in reason:
        return "详情失败"
    if "重复" in reason:
        return "重复"
    return "低分/其他"


def format_plan(plan: Plan) -> str:
    lines = [
        f"投递计划 {plan.run_id}",
        f"平台：{plan.platform}　城市：{plan.city}　生成：{plan.created_at}　"
        f"本次上限：{plan.limit}　最低分：{plan.min_score}",
        "",
    ]
    if not plan.items:
        lines.append("（没有入选职位）")
    else:
        header = (
            f"{_pad('#', 3)}{_pad('分数', 5)}{_pad('职位', 24)}"
            f"{_pad('公司', 22)}{_pad('薪资', 14)}状态"
        )
        lines.append(header)
        lines.append("-" * 88)
        for idx, item in enumerate(plan.items, 1):
            lines.append(
                f"{_pad(idx, 3)}{_pad(item.match.score, 5)}"
                f"{_pad(item.job.title, 24)}{_pad(item.job.company, 22)}"
                f"{_pad(item.job.salary, 14)}{STATUS_LABELS.get('planned', '待投递')}"
            )
        lines.append("")
        lines.append("打招呼语预览：")
        for idx, item in enumerate(plan.items, 1):
            source = "AI" if item.greeting_source == "ai" else "模板"
            lines.append(f"  {idx}. [{source}] {item.greeting}")

    if plan.skipped:
        categories = Counter(_skip_category(s.get("reason", "")) for s in plan.skipped)
        lines.append("")
        lines.append(
            "跳过统计：" + "，".join(f"{name} {count}" for name, count in categories.most_common())
        )
        for item in plan.skipped[:8]:
            lines.append(
                f"  - {item.get('title', '')} / {item.get('company', '')}：{item.get('reason', '')}"
            )
        if len(plan.skipped) > 8:
            lines.append(f"  …… 另有 {len(plan.skipped) - 8} 条，详见计划文件")
    return "\n".join(lines)


def write_daily_report(
    storage: Storage,
    reports_dir: Path,
    day: date,
    platform: str | None = None,
) -> tuple[Path, dict[str, int]]:
    rows = storage.list_for_date(day, platform)
    counts = Counter(row["status"] for row in rows)
    reports_dir.mkdir(parents=True, exist_ok=True)
    suffix = platform or "all"
    path = reports_dir / f"jobbot-report-{day.isoformat()}-{suffix}.md"

    lines = [
        f"# 投递日报 {day.isoformat()}",
        "",
        f"- 平台：{platform or '全部'}",
        f"- 已投递：{counts.get('sent', 0)}",
        f"- 待投递：{counts.get('planned', 0)}",
        f"- 待确认：{counts.get('unknown', 0)}",
        f"- 需人工：{counts.get('requires_action', 0)}",
        f"- 失败：{counts.get('failed', 0)}",
        f"- 已拦下：{counts.get('skipped', 0)}",
        "",
        "| 状态 | 分数 | 职位 | 公司 | 薪资 | 更新时间 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {STATUS_LABELS.get(row['status'], row['status'])} | {row['score']} | "
            f"{row['title']} | {row['company']} | {row['salary']} | {row['updated_at']} |"
        )
    if not rows:
        lines.append("| - | - | 今日暂无记录 | - | - | - |")

    details = [row for row in rows if row["status"] in ("sent", "unknown", "requires_action", "skipped")]
    if details:
        lines.extend(["", "## 明细", ""])
        for idx, row in enumerate(details, 1):
            lines.append(f"### {idx}. {row['title']} · {row['company']}")
            lines.append("")
            lines.append(f"- 状态：{STATUS_LABELS.get(row['status'], row['status'])}")
            lines.append(f"- 分数：{row['score']}")
            lines.append(f"- 链接：{row['url']}")
            if row["greeting"]:
                lines.append(f"- 招呼语：{row['greeting']}")
            if row["error"]:
                lines.append(f"- 备注：{row['error']}")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path, dict(counts)


def format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "暂无记录"
    order = ["sent", "planned", "unknown", "requires_action", "skipped", "failed"]
    parts = [f"{STATUS_LABELS.get(k, k)} {counts[k]}" for k in order if counts.get(k)]
    return "，".join(parts) if parts else "暂无记录"
