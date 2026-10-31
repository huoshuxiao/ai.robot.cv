from __future__ import annotations

import pytest
import yaml

from jobbot.config import ConfigError, load_config, load_profile


def test_goal_alignment(cfg, profile):
    pc = cfg.platform("boss")
    assert pc.enabled
    assert [city.name for city in pc.search_cities] == ["大连", "北京"]
    assert [city.code for city in pc.search_cities] == ["101070200", "101010100"]
    assert pc.limit_per_run == 3
    assert pc.limit_per_day == 3
    assert profile.name == "孙文昆"
    assert profile.years_experience == 21
    assert profile.expected_salary_k == (13, 26)


def test_single_city_backward_compat(project):
    path = project / "config" / "config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["platforms"]["boss"].pop("cities", None)
    data["platforms"]["boss"]["city"] = "大连"
    data["platforms"]["boss"]["city_code"] = "101070200"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    cfg = load_config(path)
    pc = cfg.platform("boss")
    assert [(c.name, c.code) for c in pc.search_cities] == [("大连", "101070200")]


def test_reject_empty_cities(mutate_cfg):
    with pytest.raises(ConfigError, match="cities"):
        mutate_cfg(lambda d: d["platforms"]["boss"].__setitem__("cities", []))


def test_ai_local_and_online_providers(cfg):
    ai = cfg.ai
    assert ai.enabled
    assert ai.provider == "local"
    assert ai.fallback is None
    local = ai.providers["local"]
    assert local.enabled is True
    assert local.type == "ollama"
    assert local.model == "deepseek-r1:8b"
    assert local.base_url == "http://127.0.0.1:11434"
    assert local.thinking_budget == 2048
    assert local.num_ctx == 6144
    assert local.timeout_seconds == 900
    online = ai.providers["online"]
    assert online.enabled is False
    assert online.type == "openai"
    assert online.api_key_env == "OPENAI_API_KEY"
    assert ai.provider_config("local") is local


def test_ai_defaults_when_flags_omitted(project):
    path = project / "config" / "config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["ai"].pop("provider", None)
    data["ai"].pop("fallback", None)
    data["ai"]["providers"]["local"].pop("enabled", None)
    data["ai"]["providers"]["online"].pop("enabled", None)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    cfg = load_config(path)
    assert cfg.ai.provider == "local"
    assert cfg.ai.fallback is None
    assert cfg.ai.providers["local"].enabled is True
    assert cfg.ai.providers["online"].enabled is False


def test_reject_fallback_to_disabled_provider(mutate_cfg):
    with pytest.raises(ConfigError, match="fallback"):
        mutate_cfg(lambda d: d["ai"].__setitem__("fallback", "online"))


def test_reject_unknown_ai_provider(mutate_cfg):
    with pytest.raises(ConfigError, match="provider"):
        mutate_cfg(lambda d: d["ai"].__setitem__("provider", "nope"))


def test_reject_unknown_ai_fallback(mutate_cfg):
    with pytest.raises(ConfigError, match="fallback"):
        mutate_cfg(lambda d: d["ai"].__setitem__("fallback", "nope"))


def test_reject_online_provider_without_key_env(mutate_cfg):
    def mutate(data):
        data["ai"]["providers"]["online"]["api_key_env"] = ""

    with pytest.raises(ConfigError, match="api_key_env"):
        mutate_cfg(mutate)


def test_reject_bad_ai_provider_type(mutate_cfg):
    def mutate(data):
        data["ai"]["providers"]["local"]["type"] = "foo"

    with pytest.raises(ConfigError, match="type"):
        mutate_cfg(mutate)


def test_legacy_flat_ai_config_still_loads(project):
    path = project / "config" / "config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["ai"] = {
        "enabled": True,
        "base_url": "https://legacy.test/v1",
        "model": "legacy-model",
        "api_key_env": "LEGACY_KEY",
        "timeout_seconds": 12,
    }
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    cfg = load_config(path)
    provider = cfg.ai.providers["online"]
    assert cfg.ai.provider == "online"
    assert provider.enabled is True
    assert provider.model == "legacy-model"
    assert provider.base_url == "https://legacy.test/v1"
    assert provider.api_key_env == "LEGACY_KEY"
    assert provider.timeout_seconds == 12


def test_relative_paths_resolve_against_project_root(tmp_cfg, project):
    assert tmp_cfg.storage.db == (project / "data" / "jobbot.sqlite3").resolve()
    assert tmp_cfg.platform("boss").selectors == (
        project / "config" / "selectors.boss.yaml"
    ).resolve()


def test_reject_zero_run_limit(mutate_cfg):
    with pytest.raises(ConfigError, match="limit_per_run"):
        mutate_cfg(lambda d: d["platforms"]["boss"].__setitem__("limit_per_run", 0))


def test_reject_unimplemented_platform(mutate_cfg):
    def mutate(data):
        data["platforms"]["51job"] = {"enabled": True, "city": "大连", "city_code": "1"}

    with pytest.raises(ConfigError, match="尚未实现"):
        mutate_cfg(mutate)


def test_disabled_reserved_platform_is_allowed(mutate_cfg):
    def mutate(data):
        data["platforms"]["51job"] = {"enabled": False, "city": "大连", "city_code": "1"}

    cfg = mutate_cfg(mutate)
    assert "51job" in cfg.platforms
    assert not cfg.platforms["51job"].enabled


def test_reject_empty_keywords(mutate_cfg):
    with pytest.raises(ConfigError, match="keywords"):
        mutate_cfg(lambda d: d["search"].__setitem__("keywords", []))


def test_reject_interval_order(mutate_cfg):
    def mutate(data):
        data["defaults"]["min_interval_seconds"] = 60
        data["defaults"]["max_interval_seconds"] = 10

    with pytest.raises(ConfigError, match="min_interval_seconds"):
        mutate_cfg(mutate)


def test_reject_score_above_100(mutate_cfg):
    with pytest.raises(ConfigError, match="min_score"):
        mutate_cfg(lambda d: d["defaults"].__setitem__("min_score", 101))


def test_reject_enabled_platform_without_city(mutate_cfg):
    def mutate(data):
        boss = data["platforms"]["boss"]
        boss.pop("cities", None)
        boss["city"] = ""
        boss["city_code"] = ""

    with pytest.raises(ConfigError, match="city"):
        mutate_cfg(mutate)


def test_profile_requires_core_skills(project, tmp_cfg):
    path = project / "config" / "profile.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["skills"]["core"] = []
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="skills.core"):
        load_profile(tmp_cfg.profile_path)


def test_profile_supplement_is_loaded(profile):
    assert profile.education["note"] == "非全日制专科/本科，学信网可查，有毕业证无学位证"
    assert any("日语 N3" in item for item in profile.raw["languages"])
    assert any("英语" in item for item in profile.raw["languages"])


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        load_config(tmp_path / "nope.yaml")
