from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from jobbot.config import load_config, load_profile

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def root() -> Path:
    return ROOT


@pytest.fixture
def cfg():
    return load_config(ROOT / "config" / "config.yaml")


@pytest.fixture
def profile(cfg):
    return load_profile(cfg.profile_path)


@pytest.fixture
def project(tmp_path):
    dst = tmp_path / "project"
    shutil.copytree(ROOT / "config", dst / "config")
    shutil.copytree(ROOT / "examples", dst / "examples")
    path = dst / "config" / "config.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["defaults"]["min_interval_seconds"] = 0
    data["defaults"]["max_interval_seconds"] = 0
    data["ai"]["enabled"] = False
    data["search"]["exclude_keywords"] = ["销售", "保险", "房产中介", "培训讲师", "实习"]
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return dst


@pytest.fixture
def tmp_cfg(project):
    return load_config(project / "config" / "config.yaml")


@pytest.fixture
def tmp_profile(tmp_cfg):
    return load_profile(tmp_cfg.profile_path)


@pytest.fixture
def mutate_cfg(project):
    def _mutate(fn):
        path = project / "config" / "config.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        fn(data)
        path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return load_config(path)

    return _mutate
