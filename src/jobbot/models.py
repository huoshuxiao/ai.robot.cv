from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

STATUSES = ("planned", "sent", "failed", "unknown", "requires_action")


@dataclass
class Job:
    platform: str
    job_id: str
    title: str
    company: str = ""
    salary: str = ""
    location: str = ""
    url: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    recruiter: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Job:
        allowed = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})


@dataclass
class MatchResult:
    score: int
    reasons: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    matched_skills: list[str] = field(default_factory=list)
    skipped: bool = False
    rule_score: int = 0
    ai_score: int | None = None

    def passed(self, min_score: int) -> bool:
        return not self.skipped and self.score >= min_score


@dataclass
class PlanItem:
    job: Job
    match: MatchResult
    greeting: str = ""
    greeting_source: str = "template"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlanItem:
        return cls(
            job=Job.from_dict(data.get("job", {})),
            match=MatchResult(**data.get("match", {})),
            greeting=data.get("greeting", ""),
            greeting_source=data.get("greeting_source", "template"),
        )


@dataclass
class Plan:
    run_id: str
    platform: str
    city: str
    created_at: str
    min_score: int
    limit: int
    items: list[PlanItem] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Plan:
        return cls(
            run_id=data["run_id"],
            platform=data["platform"],
            city=data.get("city", ""),
            created_at=data.get("created_at", ""),
            min_score=int(data.get("min_score", 0)),
            limit=int(data.get("limit", 0)),
            items=[PlanItem.from_dict(i) for i in data.get("items", [])],
            skipped=list(data.get("skipped", [])),
            meta=dict(data.get("meta", {})),
        )
