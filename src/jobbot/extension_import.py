from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import Job
from .storage import Storage, iso, now_local


def local_timestamp(value: Any) -> str:
    if not value:
        return iso(now_local())
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return iso(now_local())
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=now_local().tzinfo)
    return parsed.astimezone().isoformat(timespec="seconds")


def _job_from_record(record: dict[str, Any]) -> Job:
    raw = record.get("job") if isinstance(record.get("job"), dict) else {}
    return Job(
        platform="boss",
        job_id=str(raw.get("id") or "").strip(),
        title=str(raw.get("title") or "").strip(),
        company=str(raw.get("company") or "").strip(),
        salary=str(raw.get("salary") or "").strip(),
        location=str(raw.get("area") or raw.get("location") or "").strip(),
        url=str(raw.get("url") or "").strip(),
        tags=[str(tag) for tag in (raw.get("tags") or [])],
    )


def import_extension_results(
    storage: Storage, payload: dict[str, Any], platform: str = "boss"
) -> dict[str, int]:
    records = payload.get("records")
    if not isinstance(records, list):
        records = [payload]
    run_id = str(payload.get("run") or "extension-import")
    counts = {"sent": 0, "unknown": 0, "failed": 0, "skipped": 0}
    for record in records:
        if not isinstance(record, dict):
            counts["skipped"] += 1
            continue
        job = _job_from_record(record)
        if not job.job_id or not job.title:
            counts["skipped"] += 1
            continue
        existing = storage.get(platform, job.job_id)
        if existing is not None and existing["status"] == "sent":
            counts["skipped"] += 1
            continue
        status = str(record.get("status") or "").strip()
        greeting = str(record.get("greeting") or "")
        error = str(record.get("error") or "")
        storage.record_planned(job, int(record.get("score") or 0), greeting, run_id)
        if status == "sent":
            when = local_timestamp(record.get("time"))
            storage.mark_sent(platform, job.job_id, error or "extension: imported")
            with sqlite3.connect(storage.path) as conn:
                conn.execute(
                    "UPDATE applications SET sent_at=?, updated_at=? "
                    "WHERE platform=? AND job_id=?",
                    (when, when, platform, job.job_id),
                )
                conn.commit()
            counts["sent"] += 1
        elif status in ("unconfirmed", "unknown"):
            storage.mark_status(platform, job.job_id, "unknown", error or "extension: unconfirmed")
            counts["unknown"] += 1
        elif status == "skipped":
            storage.mark_status(platform, job.job_id, "skipped", error or "extension: skipped")
            counts["skipped"] += 1
        else:
            storage.mark_status(platform, job.job_id, "failed", error or f"extension status: {status}")
            counts["failed"] += 1
    return counts


def import_extension_file(
    storage: Storage, path: str | Path, platform: str = "boss"
) -> dict[str, int]:
    payload = json.loads(Path(path).expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("结果文件必须是 JSON 对象")
    return import_extension_results(storage, payload, platform)
