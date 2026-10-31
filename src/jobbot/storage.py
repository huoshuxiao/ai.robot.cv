from __future__ import annotations

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


def now_local() -> datetime:
    return datetime.now().astimezone()


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


class Storage:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.executescript(SCHEMA)
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
        planned_ttl_days: int = 3,
        action_ttl_days: int = 7,
    ) -> tuple[bool, str]:
        row = self.get(platform, job_id)
        if row is None:
            return False, ""
        status = row["status"]
        now = now_local()
        if status == "sent":
            return True, "台账显示已投递"
        if status == "planned":
            created = datetime.fromisoformat(row["created_at"])
            if created >= now - timedelta(days=planned_ttl_days):
                return True, "已有未执行的计划"
            return False, ""
        if status in ("requires_action", "unknown"):
            updated = datetime.fromisoformat(row["updated_at"])
            if updated >= now - timedelta(days=action_ttl_days):
                return True, f"状态为 {status}，需人工确认后再投"
        return False, ""

    def record_planned(self, job: Job, score: int, greeting: str, run_id: str) -> None:
        ts = iso(now_local())
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO applications
                    (platform, job_id, title, company, salary, url, score, greeting,
                     status, error, run_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'planned', '', ?, ?, ?)
                ON CONFLICT(platform, job_id) DO UPDATE SET
                    title=excluded.title,
                    company=excluded.company,
                    salary=excluded.salary,
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
                    job.url,
                    score,
                    greeting,
                    run_id,
                    ts,
                    ts,
                ),
            )
            conn.commit()

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
