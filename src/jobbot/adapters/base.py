from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..config import DefaultsConfig, PlatformConfig, Profile, SearchConfig
from ..models import Job


class AdapterError(Exception):
    """平台适配器错误。"""


class LoginRequired(AdapterError):
    """需要人工登录。"""


class SelectorError(AdapterError):
    """页面结构变化导致元素缺失，已保存调试现场。"""


class RiskControlDetected(AdapterError):
    """触发验证码或风控，必须人工处理。"""


@dataclass
class SendResult:
    ok: bool
    evidence: str = ""
    error: str = ""
    uncertain: bool = False
    requires_action: bool = False


class BaseAdapter(ABC):
    name = "base"

    def __init__(
        self,
        platform: PlatformConfig,
        search: SearchConfig,
        defaults: DefaultsConfig,
        profile: Profile,
        logger: logging.Logger | None = None,
    ):
        self.platform = platform
        self.search = search
        self.defaults = defaults
        self.profile = profile
        self.log = logger or logging.getLogger(__name__)

    def __enter__(self) -> BaseAdapter:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @abstractmethod
    def check_login(self) -> bool:
        raise NotImplementedError

    def login(self, timeout_s: int = 180) -> bool:
        raise AdapterError(f"平台 {self.name} 不支持交互登录")

    @abstractmethod
    def search_jobs(self) -> list[Job]:
        raise NotImplementedError

    @abstractmethod
    def fetch_detail(self, job: Job) -> Job:
        raise NotImplementedError

    @abstractmethod
    def send_greeting(self, job: Job, greeting: str) -> SendResult:
        raise NotImplementedError

    def close(self) -> None:
        return None
