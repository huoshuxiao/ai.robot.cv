from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from jobbot.models import Job
from jobbot.storage import Storage, now_local


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path / "jobbot.sqlite3")


def make_job(job_id: str = "j1") -> Job:
    return Job(
        platform="boss",
        job_id=job_id,
        title="Java后端开发工程师",
        company="示例科技",
        salary="15-25K",
        url=f"https://example.com/{job_id}",
    )


def test_planned_is_duplicate_within_ttl(storage):
    storage.record_planned(make_job(), 80, "您好", "run1")
    duplicate, reason = storage.is_duplicate("boss", "j1")
    assert duplicate
    assert "计划" in reason


def test_sent_is_duplicate_and_counted(storage):
    job = make_job()
    storage.record_planned(job, 80, "您好", "run1")
    storage.mark_sent("boss", job.job_id, "bubble")
    duplicate, reason = storage.is_duplicate("boss", job.job_id)
    assert duplicate
    assert "已投递" in reason
    assert storage.count_sent_today("boss") == 1


def test_planned_does_not_overwrite_sent(storage):
    job = make_job()
    storage.record_planned(job, 80, "您好", "run1")
    storage.mark_sent("boss", job.job_id)
    storage.record_planned(job, 99, "新招呼语", "run2")
    row = storage.get("boss", job.job_id)
    assert row["status"] == "sent"
    assert row["sent_at"] is not None


def test_expired_plan_can_be_replanned(storage):
    storage.record_planned(make_job(), 80, "您好", "run1")
    old = (now_local() - timedelta(days=4)).isoformat(timespec="seconds")
    with sqlite3.connect(storage.path) as conn:
        conn.execute(
            "UPDATE applications SET created_at=? WHERE platform='boss' AND job_id='j1'",
            (old,),
        )
    duplicate, _ = storage.is_duplicate("boss", "j1")
    assert not duplicate


def test_unknown_blocks_for_manual_check(storage):
    storage.record_planned(make_job(), 80, "您好", "run1")
    storage.mark_status("boss", "j1", "unknown", "未确认")
    duplicate, reason = storage.is_duplicate("boss", "j1")
    assert duplicate
    assert "unknown" in reason


def test_requires_action_blocks(storage):
    storage.record_planned(make_job(), 80, "您好", "run1")
    storage.mark_status("boss", "j1", "requires_action", "验证码")
    duplicate, reason = storage.is_duplicate("boss", "j1")
    assert duplicate
    assert "requires_action" in reason


def test_list_for_date_and_runs(storage):
    job = make_job()
    storage.add_run("run1", "boss", 1)
    storage.record_planned(job, 80, "您好", "run1")
    storage.mark_sent("boss", job.job_id)
    storage.finish_run("run1", 1, 0, "completed")
    rows = storage.list_for_date(now_local().date(), "boss")
    assert len(rows) == 1
    assert rows[0]["status"] == "sent"
    run = storage.get_run("run1")
    assert run["sent"] == 1
    assert run["status"] == "completed"


def test_status_counts(storage):
    storage.record_planned(make_job("a"), 80, "hi", "r")
    storage.record_planned(make_job("b"), 70, "hi", "r")
    storage.mark_sent("boss", "a")
    assert storage.status_counts("boss") == {"planned": 1, "sent": 1}
