from __future__ import annotations

import json
from pathlib import Path

from ..models import Job
from .base import BaseAdapter, SendResult


class FixtureAdapter(BaseAdapter):
    """离线夹具适配器：用于 demo 与测试，不访问任何网络。"""

    name = "fixture"

    def __init__(self, *args, jobs_path: str | Path, **kwargs):
        super().__init__(*args, **kwargs)
        self.jobs_path = Path(jobs_path)
        self.jobs: list[Job] = self._load(self.jobs_path)
        self.send_log: list[tuple[str, str]] = []

    def _load(self, path: Path) -> list[Job]:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("jobs", []) if isinstance(data, dict) else data
        jobs: list[Job] = []
        for item in items:
            item = dict(item)
            item.setdefault("platform", self.platform.name)
            job = Job.from_dict(item)
            if not job.job_id or not job.title:
                raise ValueError(f"夹具职位缺少 job_id/title：{item}")
            jobs.append(job)
        return jobs

    def all_jobs(self) -> list[Job]:
        return list(self.jobs)

    def check_login(self) -> bool:
        return True

    def login(self, timeout_s: int = 180) -> bool:
        return True

    def search_jobs(self) -> list[Job]:
        return list(self.jobs)

    def fetch_detail(self, job: Job) -> Job:
        return job

    def send_greeting(self, job: Job, greeting: str) -> SendResult:
        self.send_log.append((job.job_id, greeting))
        mode = str(job.raw.get("send", "ok"))
        if mode == "captcha":
            return SendResult(ok=False, error="模拟验证码", requires_action=True)
        if mode == "fail":
            return SendResult(ok=False, error="模拟发送失败")
        if mode == "uncertain":
            return SendResult(ok=False, error="模拟发送未确认", uncertain=True)
        return SendResult(ok=True, evidence="fixture: message bubble matched")
