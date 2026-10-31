from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

SUPPORTED_PLATFORMS = {"boss"}


class ConfigError(Exception):
    """配置缺失或非法。"""


@dataclass(frozen=True)
class StorageConfig:
    db: Path
    plans_dir: Path
    reports_dir: Path
    debug_dir: Path


@dataclass(frozen=True)
class DefaultsConfig:
    limit_per_run: int = 5
    limit_per_day: int = 5
    min_score: int = 60
    min_interval_seconds: float = 20.0
    max_interval_seconds: float = 45.0
    headless: bool = False
    page_timeout_seconds: float = 30.0
    dedupe_window_days: int = 7


@dataclass(frozen=True)
class SearchConfig:
    keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    salary_range_k: tuple[int, int]
    max_pages: int = 5
    job_types: tuple[str, ...] = ("全职",)


@dataclass(frozen=True)
class AIProviderConfig:
    name: str
    type: str = "openai"
    enabled: bool = True
    base_url: str = ""
    model: str = ""
    api_key_env: str = ""
    timeout_seconds: float = 30.0
    keep_alive: str = "5m"
    num_ctx: int = 4096
    thinking_budget: int = 512
    think: bool = False


def _default_ai_providers() -> dict[str, AIProviderConfig]:
    return {
        "online": AIProviderConfig(
            name="online",
            type="openai",
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
            api_key_env="OPENAI_API_KEY",
            timeout_seconds=30.0,
        )
    }


@dataclass(frozen=True)
class AIConfig:
    enabled: bool = True
    provider: str = "online"
    fallback: str | None = None
    providers: dict[str, AIProviderConfig] = field(default_factory=_default_ai_providers)
    max_greeting_chars: int = 120
    use_for_scoring: bool = True

    def provider_config(self, name: str | None = None) -> AIProviderConfig:
        target = name or self.provider
        if target not in self.providers:
            raise ConfigError(f"未知 AI provider：{target}；已配置：{', '.join(self.providers) or '无'}")
        return self.providers[target]

    @property
    def selected_provider(self) -> AIProviderConfig:
        return self.provider_config()


@dataclass(frozen=True)
class CityConfig:
    name: str
    code: str


@dataclass(frozen=True)
class PlatformConfig:
    name: str
    enabled: bool
    city: str = ""
    city_code: str = ""
    cities: tuple[CityConfig, ...] = ()
    limit_per_run: int = 5
    limit_per_day: int = 5
    selectors: Path | None = None
    user_data_dir: Path | None = None
    locale: str = "zh-CN"
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def search_cities(self) -> tuple[CityConfig, ...]:
        if self.cities:
            return self.cities
        if self.city and self.city_code:
            return (CityConfig(self.city, self.city_code),)
        return ()


@dataclass(frozen=True)
class Profile:
    name: str
    gender: str
    age: int
    location: str
    email: str
    years_experience: int
    target_title: str
    expected_salary_k: tuple[int, int]
    skills_core: tuple[str, ...]
    skills_familiar: tuple[str, ...]
    summary: str
    highlights: tuple[str, ...]
    education: dict[str, Any]
    experiences: tuple[dict[str, Any], ...]
    greeting_style: str
    raw: dict[str, Any]

    @property
    def all_skills(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*self.skills_core, *self.skills_familiar)))


@dataclass(frozen=True)
class Config:
    path: Path
    root: Path
    profile_path: Path
    storage: StorageConfig
    defaults: DefaultsConfig
    search: SearchConfig
    ai: AIConfig
    platforms: dict[str, PlatformConfig]
    raw: dict[str, Any]

    def enabled_platforms(self) -> list[str]:
        return [name for name, p in self.platforms.items() if p.enabled]

    def platform(self, name: str | None = None) -> PlatformConfig:
        if name:
            if name not in self.platforms:
                raise ConfigError(f"未知平台：{name}；已配置：{', '.join(self.platforms) or '无'}")
            return self.platforms[name]
        enabled = self.enabled_platforms()
        if not enabled:
            raise ConfigError("没有启用的平台，请在 config.yaml 的 platforms 中开启至少一个")
        return self.platforms[enabled[0]]


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} 必须是映射（键值对）")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConfigError(f"{label} 必须是列表")
    return value


def _str(data: dict[str, Any], key: str, label: str, default: str | None = None) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label}.{key} 必须是非空字符串")
    return value.strip()


def _int(data: dict[str, Any], key: str, label: str, default: int | None = None, minimum: int | None = None) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{label}.{key} 必须是整数")
    if minimum is not None and value < minimum:
        raise ConfigError(f"{label}.{key} 不能小于 {minimum}")
    return value


def _float(data: dict[str, Any], key: str, label: str, default: float, minimum: float | None = None) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{label}.{key} 必须是数字")
    value = float(value)
    if minimum is not None and value < minimum:
        raise ConfigError(f"{label}.{key} 不能小于 {minimum}")
    return value


def _bool(data: dict[str, Any], key: str, label: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{label}.{key} 必须是 true/false")
    return value


def _resolve(base: Path, value: str) -> Path:
    p = Path(os.path.expanduser(value))
    return p if p.is_absolute() else (base / p).resolve()


def _project_root(config_path: Path) -> Path:
    return config_path.parent.parent if config_path.parent.name == "config" else config_path.parent


def _load_yaml(path: Path, label: str) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"{label} 不存在：{path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{label} YAML 解析失败：{exc}") from exc
    if data is None:
        raise ConfigError(f"{label} 为空：{path}")
    return _mapping(data, label)


def load_profile(path: Path) -> Profile:
    data = _load_yaml(path, "简历档案")
    for key in ("name", "location", "email", "target_title", "summary"):
        _str(data, key, "profile")
    years = _int(data, "years_experience", "profile", minimum=0)
    age = _int(data, "age", "profile", minimum=0)
    salary = data.get("expected_salary_k")
    if not isinstance(salary, list) or len(salary) != 2 or not all(isinstance(x, int) for x in salary):
        raise ConfigError("profile.expected_salary_k 必须是 [下限, 上限] 两个整数（单位 K）")
    if salary[0] > salary[1]:
        raise ConfigError("profile.expected_salary_k 下限不能大于上限")
    skills = _mapping(data.get("skills"), "profile.skills")
    core = tuple(
        str(x).strip() for x in _list(skills.get("core"), "profile.skills.core") if str(x).strip()
    )
    if not core:
        raise ConfigError("profile.skills.core 至少填写一项")
    familiar = tuple(
        str(x).strip()
        for x in _list(skills.get("familiar", []), "profile.skills.familiar")
        if str(x).strip()
    )
    highlights = tuple(
        str(x).strip() for x in _list(data.get("highlights", []), "profile.highlights")
    )
    experiences = tuple(
        _mapping(x, "profile.experiences[]")
        for x in _list(data.get("experiences", []), "profile.experiences")
    )
    education = _mapping(data.get("education", {}), "profile.education")
    return Profile(
        name=data["name"].strip(),
        gender=str(data.get("gender", "")),
        age=age,
        location=data["location"].strip(),
        email=data["email"].strip(),
        years_experience=years,
        target_title=data["target_title"].strip(),
        expected_salary_k=(int(salary[0]), int(salary[1])),
        skills_core=core,
        skills_familiar=familiar,
        summary=data["summary"].strip(),
        highlights=highlights,
        education=education,
        experiences=experiences,
        greeting_style=str(data.get("greeting_style", "")).strip(),
        raw=data,
    )


def _parse_search(data: dict[str, Any]) -> SearchConfig:
    raw = _mapping(data.get("search"), "config.search")
    keywords = tuple(str(x).strip() for x in _list(raw.get("keywords"), "config.search.keywords") if str(x).strip())
    if not keywords:
        raise ConfigError("config.search.keywords 至少填写一个关键词")
    excludes = tuple(
        str(x).strip()
        for x in _list(raw.get("exclude_keywords", []), "config.search.exclude_keywords")
        if str(x).strip()
    )
    salary = raw.get("salary_range_k")
    if not isinstance(salary, list) or len(salary) != 2 or not all(isinstance(x, int) for x in salary):
        raise ConfigError("config.search.salary_range_k 必须是 [下限, 上限] 两个整数")
    if salary[0] > salary[1]:
        raise ConfigError("config.search.salary_range_k 下限不能大于上限")
    pages = _int(raw, "max_pages", "config.search", default=5, minimum=1)
    job_types = tuple(
        str(x).strip()
        for x in _list(raw.get("job_types", ["全职"]), "config.search.job_types")
        if str(x).strip()
    )
    return SearchConfig(
        keywords=keywords,
        exclude_keywords=excludes,
        salary_range_k=(int(salary[0]), int(salary[1])),
        max_pages=pages,
        job_types=job_types,
    )


def _parse_ai_provider(name: str, value: Any) -> AIProviderConfig:
    label = f"config.ai.providers.{name}"
    raw = _mapping(value, label)
    kind = str(raw.get("type", "openai")).strip().lower()
    if kind not in ("ollama", "openai"):
        raise ConfigError(f"{label}.type 只支持 ollama/openai")
    default_base = "http://127.0.0.1:11434" if kind == "ollama" else "https://api.openai.com/v1"
    default_model = "deepseek-r1:8b" if kind == "ollama" else "gpt-4o-mini"
    base_url = _str(raw, "base_url", label, default_base).rstrip("/")
    model = _str(raw, "model", label, default_model)
    key_env = str(raw.get("api_key_env", "")).strip()
    if kind == "openai" and not key_env:
        raise ConfigError(f"{label}.api_key_env 必须填写在线服务的 Key 环境变量")
    return AIProviderConfig(
        name=name,
        type=kind,
        enabled=_bool(raw, "enabled", label, kind == "ollama"),
        base_url=base_url,
        model=model,
        api_key_env=key_env,
        timeout_seconds=_float(
            raw, "timeout_seconds", label, 300.0 if kind == "ollama" else 30.0, minimum=1
        ),
        keep_alive=str(raw.get("keep_alive", "5m")).strip() or "5m",
        num_ctx=_int(raw, "num_ctx", label, 4096, minimum=512),
        thinking_budget=_int(raw, "thinking_budget", label, 512, minimum=0),
        think=_bool(raw, "think", label, False),
    )


def _parse_ai(data: dict[str, Any]) -> AIConfig:
    raw = _mapping(data.get("ai", {}), "config.ai")
    max_greeting = _int(raw, "max_greeting_chars", "config.ai", default=120, minimum=20)
    enabled = _bool(raw, "enabled", "config.ai", True)
    use_for_scoring = _bool(raw, "use_for_scoring", "config.ai", True)

    providers_raw = raw.get("providers")
    if providers_raw is not None:
        providers = {
            str(name): _parse_ai_provider(str(name), value)
            for name, value in _mapping(providers_raw, "config.ai.providers").items()
        }
    else:
        base_url = _str(raw, "base_url", "config.ai", "https://api.openai.com/v1")
        model = _str(raw, "model", "config.ai", "gpt-4o-mini")
        key_env = _str(raw, "api_key_env", "config.ai", "OPENAI_API_KEY")
        providers = {
            "online": AIProviderConfig(
                name="online",
                type="openai",
                base_url=base_url.rstrip("/"),
                model=model,
                api_key_env=key_env,
                timeout_seconds=_float(raw, "timeout_seconds", "config.ai", 30.0, minimum=1),
            )
        }
    if not providers:
        raise ConfigError("config.ai.providers 至少配置一个 provider")

    enabled_providers = [name for name, provider in providers.items() if provider.enabled]
    if "local" in enabled_providers:
        default_provider = "local"
    elif enabled_providers:
        default_provider = enabled_providers[0]
    elif "online" in providers:
        default_provider = "online"
    else:
        default_provider = next(iter(providers))
    provider = str(raw.get("provider", default_provider)).strip()
    if provider not in providers:
        raise ConfigError(
            f"config.ai.provider={provider} 未找到；已配置：{', '.join(providers)}"
        )

    fallback_value = raw.get("fallback")
    fallback: str | None = None
    if fallback_value not in (None, "", False):
        fallback = str(fallback_value).strip()
        if fallback not in providers:
            raise ConfigError(
                f"config.ai.fallback={fallback} 未找到；已配置：{', '.join(providers)}"
            )
        if not providers[fallback].enabled:
            raise ConfigError(
                f"config.ai.fallback={fallback} 已在配置中关闭（enabled: false）"
            )
        if fallback == provider:
            fallback = None

    return AIConfig(
        enabled=enabled,
        provider=provider,
        fallback=fallback,
        providers=providers,
        max_greeting_chars=max_greeting,
        use_for_scoring=use_for_scoring,
    )


def _parse_platforms(data: dict[str, Any], defaults: DefaultsConfig, root: Path) -> dict[str, PlatformConfig]:
    raw_platforms = _mapping(data.get("platforms"), "config.platforms")
    if not raw_platforms:
        raise ConfigError("config.platforms 至少配置一个平台")
    result: dict[str, PlatformConfig] = {}
    for name, value in raw_platforms.items():
        label = f"config.platforms.{name}"
        raw = _mapping(value, label)
        enabled = _bool(raw, "enabled", label, False)
        if enabled and name not in SUPPORTED_PLATFORMS:
            raise ConfigError(
                f"平台 {name} 尚未实现适配器（当前可用：{', '.join(sorted(SUPPORTED_PLATFORMS))}）。"
                f"请先实现适配器，或将 enabled 设为 false。"
            )
        city = str(raw.get("city", "")).strip()
        city_code = str(raw.get("city_code", "")).strip()
        cities_raw = raw.get("cities")
        cities: tuple[CityConfig, ...] = ()
        if cities_raw is not None:
            cities = tuple(
                CityConfig(
                    name=_str(_mapping(item, f"{label}.cities[]"), "name", f"{label}.cities[]"),
                    code=_str(_mapping(item, f"{label}.cities[]"), "code", f"{label}.cities[]"),
                )
                for item in _list(cities_raw, f"{label}.cities")
            )
            if not cities:
                raise ConfigError(f"{label}.cities 至少配置一个城市")
            city = cities[0].name
            city_code = cities[0].code
        if enabled:
            if not cities and not (city and city_code):
                raise ConfigError(f"{label} 必须配置 city/city_code 或 cities")
            if cities and any(not c.name or not c.code for c in cities):
                raise ConfigError(f"{label}.cities 的 name/code 不能为空")
        selectors = raw.get("selectors")
        user_data_dir = raw.get("user_data_dir")
        result[name] = PlatformConfig(
            name=name,
            enabled=enabled,
            city=city,
            city_code=city_code,
            cities=cities,
            limit_per_run=_int(raw, "limit_per_run", label, defaults.limit_per_run, minimum=1),
            limit_per_day=_int(raw, "limit_per_day", label, defaults.limit_per_day, minimum=1),
            selectors=_resolve(root, selectors) if isinstance(selectors, str) else None,
            user_data_dir=_resolve(root, user_data_dir) if isinstance(user_data_dir, str) else None,
            locale=str(raw.get("locale", "zh-CN")),
            extra=dict(raw),
        )
    return result


def load_config(path: str | Path = "config/config.yaml") -> Config:
    config_path = Path(path).expanduser().resolve()
    data = _load_yaml(config_path, "配置文件")
    root = _project_root(config_path)

    storage_raw = _mapping(data.get("storage", {}), "config.storage")
    storage = StorageConfig(
        db=_resolve(root, str(storage_raw.get("db", "data/jobbot.sqlite3"))),
        plans_dir=_resolve(root, str(storage_raw.get("plans_dir", "data/plans"))),
        reports_dir=_resolve(root, str(storage_raw.get("reports_dir", "data/reports"))),
        debug_dir=_resolve(root, str(storage_raw.get("debug_dir", "data/debug"))),
    )

    defaults_raw = _mapping(data.get("defaults", {}), "config.defaults")
    min_interval = _float(defaults_raw, "min_interval_seconds", "config.defaults", 20.0, minimum=0)
    max_interval = _float(defaults_raw, "max_interval_seconds", "config.defaults", 45.0, minimum=0)
    if min_interval > max_interval:
        raise ConfigError("config.defaults.min_interval_seconds 不能大于 max_interval_seconds")
    defaults = DefaultsConfig(
        limit_per_run=_int(defaults_raw, "limit_per_run", "config.defaults", 5, minimum=1),
        limit_per_day=_int(defaults_raw, "limit_per_day", "config.defaults", 5, minimum=1),
        min_score=_int(defaults_raw, "min_score", "config.defaults", 60, minimum=0),
        min_interval_seconds=min_interval,
        max_interval_seconds=max_interval,
        headless=_bool(defaults_raw, "headless", "config.defaults", False),
        page_timeout_seconds=_float(defaults_raw, "page_timeout_seconds", "config.defaults", 30.0, minimum=1),
        dedupe_window_days=_int(defaults_raw, "dedupe_window_days", "config.defaults", 7, minimum=1),
    )
    if defaults.min_score > 100:
        raise ConfigError("config.defaults.min_score 不能大于 100")

    search = _parse_search(data)
    ai = _parse_ai(data)
    platforms = _parse_platforms(data, defaults, root)
    if not any(p.enabled for p in platforms.values()):
        raise ConfigError("config.platforms 中至少启用一个平台（enabled: true）")

    profile_relative = _str(data, "profile", "config", "config/profile.yaml")
    profile_path = _resolve(root, profile_relative)
    if not profile_path.exists():
        raise ConfigError(f"config.profile 指向的简历档案不存在：{profile_path}")

    return Config(
        path=config_path,
        root=root,
        profile_path=profile_path,
        storage=storage,
        defaults=defaults,
        search=search,
        ai=ai,
        platforms=platforms,
        raw=data,
    )
