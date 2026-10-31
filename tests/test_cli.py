from __future__ import annotations

import json

from jobbot.cli import main
from jobbot.storage import Storage


def run_cli(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_demo_runs_offline_end_to_end(tmp_cfg, capsys):
    code, out, err = run_cli(capsys, "--config", str(tmp_cfg.path), "demo")
    assert code == 0, err
    assert "离线演示" in out
    assert "已投递 5" in out
    assert "去重复核" in out
    assert "不会重复投递" in out

    demo_dirs = list((tmp_cfg.root / "data").glob("demo-*"))
    assert len(demo_dirs) == 1
    reports = list((demo_dirs[0] / "reports").glob("*.md"))
    assert len(reports) == 1
    assert "已投递：5" in reports[0].read_text(encoding="utf-8")


def test_run_without_yes_only_plans(tmp_cfg, capsys):
    fixture = str(tmp_cfg.root / "examples" / "sample_jobs.json")
    code, out, err = run_cli(
        capsys, "--config", str(tmp_cfg.path), "run", "--fixture", fixture, "--limit", "2"
    )
    assert code == 0, err
    assert "未发送" in out
    plans = list((tmp_cfg.root / "data" / "plans").glob("*.json"))
    assert len(plans) == 1
    assert not list((tmp_cfg.root / "data" / "plans").glob("*.result.json"))


def test_run_with_yes_sends_to_fixture(tmp_cfg, capsys):
    fixture = str(tmp_cfg.root / "examples" / "sample_jobs.json")
    code, out, err = run_cli(
        capsys,
        "--config",
        str(tmp_cfg.path),
        "run",
        "--fixture",
        fixture,
        "--limit",
        "2",
        "--yes",
    )
    assert code == 0, err
    assert "已投递 2" in out
    results = list((tmp_cfg.root / "data" / "plans").glob("*.result.json"))
    assert len(results) == 1
    data = json.loads(results[0].read_text(encoding="utf-8"))
    assert data["sent"] == 2
    assert data["failed"] == 0
    storage = Storage(tmp_cfg.storage.db)
    assert storage.count_sent_today("boss") == 2


def test_apply_requires_yes_in_non_interactive_mode(tmp_cfg, capsys):
    fixture = str(tmp_cfg.root / "examples" / "sample_jobs.json")
    code, _, err = run_cli(
        capsys, "--config", str(tmp_cfg.path), "run", "--fixture", fixture, "--limit", "1"
    )
    assert code == 0, err
    plan = next((tmp_cfg.root / "data" / "plans").glob("*.json"))
    code, _, err = run_cli(capsys, "--config", str(tmp_cfg.path), "apply", "--plan", str(plan))
    assert code == 2
    assert "非交互" in err


def test_report_after_apply(tmp_cfg, capsys):
    fixture = str(tmp_cfg.root / "examples" / "sample_jobs.json")
    code, _, err = run_cli(
        capsys,
        "--config",
        str(tmp_cfg.path),
        "run",
        "--fixture",
        fixture,
        "--limit",
        "1",
        "--yes",
    )
    assert code == 0, err
    code, out, err = run_cli(capsys, "--config", str(tmp_cfg.path), "report")
    assert code == 0, err
    assert "日报" in out
    reports = list(tmp_cfg.storage.reports_dir.glob("*.md"))
    assert len(reports) == 1
    text = reports[0].read_text(encoding="utf-8")
    assert "已投递" in text


def test_doctor_prints_config(tmp_cfg, capsys):
    code, out, err = run_cli(capsys, "--config", str(tmp_cfg.path), "doctor")
    assert code in (0, 2), err
    assert "孙文昆" in out
    assert "21 年经验" in out
    assert "大连" in out
