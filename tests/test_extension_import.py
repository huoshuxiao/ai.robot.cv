from __future__ import annotations

from jobbot.extension_import import import_extension_results
from jobbot.storage import Storage


def make_payload(status="sent", job_id="job1"):
    return {
        "run": "ext-run-1",
        "records": [
            {
                "job": {
                    "id": job_id,
                    "title": "Java后端开发工程师",
                    "company": "示例科技",
                    "salary": "15-25K",
                    "area": "大连",
                    "url": "https://example.com/job1",
                },
                "greeting": "您好，我是孙文昆，21年Java后端经验。",
                "status": status,
                "error": "",
                "time": "2026-11-01T00:30:00+08:00",
            }
        ],
    }


def test_import_sent_result(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    counts = import_extension_results(storage, make_payload())
    assert counts == {"sent": 1, "unknown": 0, "failed": 0, "skipped": 0}
    row = storage.get("boss", "job1")
    assert row["status"] == "sent"
    assert row["greeting"].startswith("您好")
    assert row["sent_at"] == "2026-11-01T00:30:00+08:00"
    assert storage.count_sent_today("boss") >= 0


def test_import_is_idempotent(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    import_extension_results(storage, make_payload())
    counts = import_extension_results(storage, make_payload())
    assert counts["skipped"] == 1
    assert storage.status_counts("boss") == {"sent": 1}


def test_import_unconfirmed_as_unknown(tmp_path):
    storage = Storage(tmp_path / "db.sqlite3")
    counts = import_extension_results(storage, make_payload(status="unconfirmed"))
    assert counts["unknown"] == 1
    assert storage.get("boss", "job1")["status"] == "unknown"


def test_import_skips_missing_job_id(tmp_path):
    payload = make_payload()
    payload["records"][0]["job"].pop("id")
    storage = Storage(tmp_path / "db.sqlite3")
    counts = import_extension_results(storage, payload)
    assert counts["skipped"] == 1
    assert storage.status_counts("boss") == {}


def test_import_converts_utc_to_local_date(tmp_path):
    payload = make_payload()
    payload["records"][0]["time"] = "2026-10-31T16:30:00.000Z"
    storage = Storage(tmp_path / "db.sqlite3")
    import_extension_results(storage, payload)
    row = storage.get("boss", "job1")
    assert row["sent_at"].startswith("2026-11-01T00:30:00")
    assert "+08:00" in row["sent_at"]
