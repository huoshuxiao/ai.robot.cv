from __future__ import annotations

import pytest
import yaml

from jobbot.config import ConfigError, load_config, load_profile


def test_goal_alignment(cfg, profile):
    pc = cfg.platform("boss")
    assert pc.enabled
    assert pc.city == "大连"
    assert pc.city_code == "101070200"
    assert pc.limit_per_run == 5
    assert pc.limit_per_day == 5
    assert profile.name == "孙文昆"
    assert profile.years_experience == 21
    assert profile.expected_salary_k == (13, 26)


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
    with pytest.raises(ConfigError, match="city"):
        mutate_cfg(lambda d: d["platforms"]["boss"].__setitem__("city", ""))


def test_profile_requires_core_skills(project, tmp_cfg):
    path = project / "config" / "profile.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["skills"]["core"] = []
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(ConfigError, match="skills.core"):
        load_profile(tmp_cfg.profile_path)


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="不存在"):
        load_config(tmp_path / "nope.yaml")
