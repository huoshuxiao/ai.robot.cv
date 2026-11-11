from __future__ import annotations

import sqlite3
import unicodedata
from collections import Counter
from contextlib import closing
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


def _area_cell(row) -> str:
    """表格里那一格地区。

    老库的 `area` 是迁移补出来的空串——不许折成"没地区"就完事，要念成**这一条没记到地区**，
    否则"扩展没传 area"与"这条岗真没有地区"在报告里长一个样。
    """
    try:
        value = str(row["area"] or "").strip()
    except (IndexError, KeyError):
        return "未记地区"
    return value or "未记地区"


def _city_of(area: str) -> str:
    """`大连·甘井子区·七贤岭` 取到城市那一层，用来做分地区小结。"""
    head = str(area or "").split("·", 1)[0].strip()
    return head or "未记地区"


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

    平台集 = sorted({str(row["platform"]) for row in rows})
    地区计 = Counter(_city_of(_area_cell(row)) for row in rows)
    if 平台集:
        平台行 = platform or f"全部（当天出现过 {len(平台集)} 个：{'、'.join(平台集)}）"
    else:
        平台行 = platform or "全部（当天无记录）"
    lines = [
        f"# 投递日报 {day.isoformat()}",
        "",
        f"- 平台：{平台行}",
    ]
    if 地区计:
        lines.append(f"- 地区：{'、'.join(f'{name} {count}' for name, count in 地区计.most_common())}")
    lines.extend([
        f"- 已投递：{counts.get('sent', 0)}",
        f"- 待投递：{counts.get('planned', 0)}",
        f"- 待确认：{counts.get('unknown', 0)}",
        f"- 需人工：{counts.get('requires_action', 0)}",
        f"- 失败：{counts.get('failed', 0)}",
        f"- 已拦下：{counts.get('skipped', 0)}",
        "",
    ])
    if len(平台集) > 1:
        平台计 = Counter(str(row["platform"]) for row in rows)
        投了 = Counter(str(row["platform"]) for row in rows if row["status"] == "sent")
        lines.append("分平台：" + "，".join(
            f"{name} 共 {平台计[name]}（已投递 {投了.get(name, 0)}）" for name in 平台集))
        lines.append("")
    lines.extend([
        "| 平台 | 地区 | 状态 | 分数 | 职位 | 公司 | 薪资 | 更新时间 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ])
    for row in rows:
        lines.append(
            f"| {row['platform']} | {_area_cell(row)} | "
            f"{STATUS_LABELS.get(row['status'], row['status'])} | {row['score']} | "
            f"{row['title']} | {row['company']} | {row['salary']} | {row['updated_at']} |"
        )
    if not rows:
        lines.append("| - | - | - | - | 今日暂无记录 | - | - | - |")

    details = [row for row in rows if row["status"] in ("sent", "unknown", "requires_action", "skipped")]
    if details:
        lines.extend(["", "## 明细", ""])
        for idx, row in enumerate(details, 1):
            lines.append(f"### {idx}. {row['title']} · {row['company']}")
            lines.append("")
            lines.append(f"- 平台：{row['platform']}　地区：{_area_cell(row)}")
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


def refresh_daily_reports(
    storage: Storage,
    reports_dir: Path,
    day: date,
) -> list[tuple[Path, dict[str, int]]]:
    """把这一天的日报全部重算一遍：先写"全部"那一份，再按当天真实出现过的平台各写一份。

    为什么不是"只写当天那一个平台"：同一天里跑过 BOSS 又跑过别的平台时，只重写其中一份
    会让另一份留在旧内容上——日报的名字里带日期，读的人默认它就是那天最新的事实。
    幂等：整文件覆写（不是追加），同一台机器上连着跑多次 `start_jobbot.py` 只会把当天那几份刷新，
    不会越跑越多份、也不会把行叠两遍。
    """
    wrote = [write_daily_report(storage, reports_dir, day, None)]
    with closing(sqlite3.connect(str(storage.path), timeout=30)) as conn:
        平台们 = [str(r[0]) for r in conn.execute(
            "SELECT DISTINCT platform FROM applications "
            "WHERE substr(COALESCE(sent_at, created_at), 1, 10)=? ORDER BY platform",
            (day.isoformat(),),
        ).fetchall()]
    for name in 平台们:
        wrote.append(write_daily_report(storage, reports_dir, day, name))
    return wrote


def format_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "暂无记录"
    order = ["sent", "planned", "unknown", "requires_action", "skipped", "failed"]
    parts = [f"{STATUS_LABELS.get(k, k)} {counts[k]}" for k in order if counts.get(k)]
    return "，".join(parts) if parts else "暂无记录"
