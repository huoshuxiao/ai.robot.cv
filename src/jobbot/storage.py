from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    platform   TEXT NOT NULL,
    job_id     TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    company    TEXT NOT NULL DEFAULT '',
    salary     TEXT NOT NULL DEFAULT '',
    area       TEXT NOT NULL DEFAULT '',
    url        TEXT NOT NULL DEFAULT '',
    score      INTEGER NOT NULL DEFAULT 0,
    greeting   TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL,
    error      TEXT NOT NULL DEFAULT '',
    run_id     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at    TEXT,
    PRIMARY KEY (platform, job_id)
);
CREATE INDEX IF NOT EXISTS idx_app_status ON applications(platform, status, sent_at);

CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    platform    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    finished_at TEXT,
    planned     INTEGER NOT NULL DEFAULT 0,
    sent        INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'planned',
    note        TEXT NOT NULL DEFAULT ''
);
"""

# 加列只走这一张表：`_migrate()` 对着 `PRAGMA table_info` 逐枚补，老库新库都得到同一个形状。
# 新列一律带 DEFAULT ''，否则往已有行上加 NOT NULL 列会当场把 `Storage()` 打崩（起不来比少一列糟）。
NEW_COLUMNS: dict[str, str] = {"area": "TEXT NOT NULL DEFAULT ''"}


def now_local() -> datetime:
    return datetime.now().astimezone()
def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now_local().tzinfo)
    return parsed


class Storage:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)
            conn.commit()

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """老库补新列：`CREATE TABLE IF NOT EXISTS` 对已存在的表不生效，新列不补就永远是"没有这一格"。

        这里的失败要抛出来，不要吞：日报读不到 `area` 与"这一场没写地区"在磁盘上会长得一模一样。
        """
        have = {row["name"] for row in conn.execute("PRAGMA table_info(applications)")}
        for column in NEW_COLUMNS:
            if column not in have:
                conn.execute(
                    f"ALTER TABLE applications ADD COLUMN {column} {NEW_COLUMNS[column]}"
                )
        conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def get(self, platform: str, job_id: str) -> sqlite3.Row | None:
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT * FROM applications WHERE platform=? AND job_id=?", (platform, job_id)
            ).fetchone()

    def is_duplicate(
        self,
        platform: str,
        job_id: str,
        company: str = "",
        title: str = "",
        window_days: int = 7,
        planned_ttl_days: int = 3,
    ) -> tuple[bool, str]:
        now = now_local()
        cutoff = now - timedelta(days=max(1, window_days))
        row = self.get(platform, job_id)
        if row is not None:
            status = row["status"]
            if status == "sent":
                sent_at = _parse_time(row["sent_at"] or row["updated_at"])
                if sent_at is not None and sent_at >= cutoff:
                    return True, f"该职位 {window_days} 天内已投递"
            elif status in ("requires_action", "unknown"):
                updated = _parse_time(row["updated_at"])
                if updated is not None and updated >= cutoff:
                    return True, f"状态为 {status}，需人工确认后再投（{window_days} 天内）"
            # 2026-11-04 裁定：注释掉「planned 行 3 天内不再投」这条限制——计划过但没发出去的岗位应当还能再进计划，
            # 只有真发出去过的（`sent`，7 日窗口）才拦。`planned_ttl_days` 参数保留是为了不改调用方形状，当前不生效。
            # elif status == "planned":
            #     created = _parse_time(row["created_at"])
            #     if created is not None and created >= now - timedelta(days=planned_ttl_days):
            #         return True, "已有未执行的计划"

        if company and title:
            target_company = _norm(company)
            target_title = _norm(title)
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    "SELECT * FROM applications "
                    "WHERE platform=? AND status IN ('sent','unknown','requires_action')",
                    (platform,),
                ).fetchall()
            for candidate in rows:
                if _norm(candidate["company"]) != target_company:
                    continue
                if _norm(candidate["title"]) != target_title:
                    continue
                when = _parse_time(
                    candidate["sent_at"] or candidate["updated_at"] or candidate["created_at"]
                )
                if when is not None and when >= cutoff:
                    return (
                        True,
                        f"同公司同职位 {window_days} 天内已投递："
                        f"{candidate['company']} / {candidate['title']}",
                    )
        return False, ""

    def record_planned(self, job: Job, score: int, greeting: str, run_id: str,
                       area: str | None = None) -> None:
        """写/更新台账行。`area` 缺省时用 `job.location`。

        为什么要单独收一枚：`job_from_payload` 在扩展没带 `area` 时把 location 兜成「大连」——
        那是**评分**的兜底，拿去当台账事实就会把北京/上海的岗记成大连。
        补账那条路因此显式传 payload 里的原值（没传＝空＝日报念「未记地区」）。
        """
        ts = iso(now_local())
        地区 = (job.location if area is None else area).strip()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO applications
                    (platform, job_id, title, company, salary, area, url, score, greeting,
                     status, error, run_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', '', ?, ?, ?)
                ON CONFLICT(platform, job_id) DO UPDATE SET
                    title=excluded.title,
                    company=excluded.company,
                    salary=excluded.salary,
                    area=excluded.area,
                    url=excluded.url,
                    score=excluded.score,
                    greeting=excluded.greeting,
                    status=CASE WHEN applications.status='sent'
                                THEN applications.status ELSE 'planned' END,
                    error=CASE WHEN applications.status='sent'
                               THEN applications.error ELSE '' END,
                    run_id=excluded.run_id,
                    updated_at=excluded.updated_at
                """,
                (
                    job.platform,
                    job.job_id,
                    job.title,
                    job.company,
                    job.salary,
                    地区,
                    job.url,
                    score,
                    greeting,
                    run_id,
                    ts,
                    ts,
                ),
            )
            conn.commit()

    def areas_missing(self) -> int:
        with closing(self._connect()) as conn:
            return int(conn.execute(
                "SELECT COUNT(*) FROM applications WHERE COALESCE(area,'')=''"
            ).fetchone()[0])

    def backfill_areas(self, mapping: dict[str, str]) -> tuple[int, int]:
        """按 `job_id → 地区` 把台账里**空着的那一格**填上，返回 (写入行数, 映射里没有的行数)。

        只填空、不改写已有值：新岗的 `area` 由扩展当场传上来，比事后回推更可信；
        这一条只负责把"加列之前那批历史行"补齐，否则日报会把它们整批念成「未记地区」。
        键只按 `job_id`（流水里没有平台这一格）——BOSS 的 id 本身就带平台特征，
        真接第二个平台时要把键换成 `(platform, job_id)`，这里不假装已经做到。
        """
        written = 0
        missing = 0
        with closing(self._connect()) as conn:
            空行 = conn.execute(
                "SELECT platform, job_id FROM applications WHERE COALESCE(area,'')=''"
            ).fetchall()
            for row in 空行:
                地区 = mapping.get(str(row["job_id"]))
                if not 地区:
                    missing += 1
                    continue
                conn.execute(
                    "UPDATE applications SET area=? WHERE platform=? AND job_id=?",
                    (str(地区), row["platform"], row["job_id"]),
                )
                written += 1
            conn.commit()
        return written, missing

    def missing_area_rows(self) -> list[tuple[str, str, str]]:
        """还空着地区的那几行 `(job_id, 公司, 职位)`——点名出来，不许折成"填不上＝没问题"。"""
        with closing(self._connect()) as conn:
            return [(str(r["job_id"]), str(r["company"]), str(r["title"])) for r in conn.execute(
                "SELECT job_id, company, title FROM applications "
                "WHERE COALESCE(area,'')='' ORDER BY company, title"
            ).fetchall()]

    def mark_sent(self, platform: str, job_id: str, evidence: str = "") -> None:
        ts = iso(now_local())
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE applications
                   SET status='sent', sent_at=?, updated_at=?, error=?
                 WHERE platform=? AND job_id=?
                """,
                (ts, ts, evidence[:500], platform, job_id),
            )
            conn.commit()

    def mark_status(self, platform: str, job_id: str, status: str, error: str = "") -> None:
        ts = iso(now_local())
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE applications SET status=?, error=?, updated_at=?
                 WHERE platform=? AND job_id=?
                """,
                (status, error[:1000], ts, platform, job_id),
            )
            conn.commit()

    def count_sent_today(self, platform: str, day: date | None = None) -> int:
        day = day or now_local().date()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n FROM applications
                 WHERE platform=? AND status='sent' AND substr(sent_at, 1, 10)=?
                """,
                (platform, day.isoformat()),
            ).fetchone()
        return int(row["n"])

    def status_counts(self, platform: str | None = None) -> dict[str, int]:
        sql = "SELECT status, COUNT(*) AS n FROM applications"
        params: tuple[Any, ...] = ()
        if platform:
            sql += " WHERE platform=?"
            params = (platform,)
        sql += " GROUP BY status"
        with closing(self._connect()) as conn:
            rows = conn.execute(sql, params).fetchall()
        return {row["status"]: int(row["n"]) for row in rows}

    def recent(self, limit: int = 50, platform: str | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM applications"
        params: list[Any] = []
        if platform:
            sql += " WHERE platform=?"
            params.append(platform)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with closing(self._connect()) as conn:
            return conn.execute(sql, params).fetchall()

    def list_for_date(self, day: date, platform: str | None = None) -> list[sqlite3.Row]:
        sql = """
            SELECT * FROM applications
             WHERE substr(COALESCE(sent_at, created_at), 1, 10)=?
        """
        params: list[Any] = [day.isoformat()]
        if platform:
            sql += " AND platform=?"
            params.append(platform)
        sql += " ORDER BY score DESC, updated_at DESC"
        with closing(self._connect()) as conn:
            return conn.execute(sql, params).fetchall()

    def add_run(self, run_id: str, platform: str, planned: int) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runs (run_id, platform, created_at, planned, status) "
                "VALUES (?, ?, ?, ?, 'planned')",
                (run_id, platform, iso(now_local()), planned),
            )
            conn.commit()

    def finish_run(self, run_id: str, sent: int, failed: int, status: str, note: str = "") -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                """
                UPDATE runs SET finished_at=?, sent=?, failed=?, status=?, note=?
                 WHERE run_id=?
                """,
                (iso(now_local()), sent, failed, status, note[:1000], run_id),
            )
            conn.commit()

    def get_run(self, run_id: str) -> sqlite3.Row | None:
        with closing(self._connect()) as conn:
            return conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
