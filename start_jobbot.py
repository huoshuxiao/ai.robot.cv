#!/usr/bin/env python3
"""一键启动自动化AI找工作。

把分散的启动步骤整合成一个脚本：本地 AI 桥 → 存储看门狗 → 带扩展的浏览器 → auto_plan
（搜索/评分/生成招呼语），可选 auto_send（合规后自动发送招呼）→ 收口对账。

用法：
  .venv/bin/python start_jobbot.py                 # 只搜索+生成计划，不外发（默认安全）
  .venv/bin/python start_jobbot.py --send          # 计划完成后自动发送招呼（外发！慎用）
  .venv/bin/python start_jobbot.py --limit 5 --send
  .venv/bin/python start_jobbot.py --no-watchdog --timeout 1800

前置：登录态需先 `.venv/bin/jobbot login`（扫码）。配置在 config/config.yaml。
默认不发送；只有显式 --send 才走 auto_send 外发腿。浏览器若已开着旧实例，会提示先关。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# 一键：若用系统 python 起的，重 exec 进项目 venv（jobbot 依赖装在那），用户可直接 ./start_jobbot.py。
_VENV_PY = ROOT / ".venv" / "bin" / "python"
if _VENV_PY.exists() and Path(sys.executable).resolve() != _VENV_PY.resolve():
    os.execv(str(_VENV_PY), [str(_VENV_PY), str(Path(__file__).resolve()), *sys.argv[1:]])
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "data" / "verify-20261104"))

import audit_run as ar  # noqa: E402

from jobbot.browser import find_profile_instances, find_system_chromium  # noqa: E402
from jobbot.cli import _login_cookie_present, build_plan_url  # noqa: E402
from jobbot.config import load_config  # noqa: E402

PY = str(ROOT / ".venv" / "bin" / "python")
DATA = ROOT / "data"
EXT = ROOT / "extension"


def log(msg: str) -> None:
    print(f"[start] {msg}", flush=True)


def bridge_health(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as r:
            return bool(json.loads(r.read().decode()).get("ok"))
    except Exception:
        return False


def start_bridge(port: int) -> None:
    if bridge_health(port):
        log(f"桥已在 127.0.0.1:{port} 跑着，复用")
        return
    DATA.mkdir(exist_ok=True)
    logfile = DATA / f"bridge-start-{time.strftime('%Y%m%d-%H%M%S')}.log"
    log(f"起桥 → {logfile.name}")
    with open(logfile, "ab") as fh:
        subprocess.Popen([PY, "-u", "-m", "jobbot", "bridge", "--port", str(port)],
                         stdout=fh, stderr=fh, cwd=str(ROOT), start_new_session=True)
    for _ in range(30):
        if bridge_health(port):
            log("桥健康检查通过")
            return
        time.sleep(1)
    raise SystemExit(f"[拒绝] 桥 30 秒内没起来，看 {logfile}")


def watchdog_running() -> bool:
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = entry.joinpath("cmdline").read_bytes().decode("utf-8", "ignore")
        except OSError:
            continue
        if "store_watch.py" in cmd:
            return True
    return False


def start_watchdog() -> None:
    if watchdog_running():
        log("看门狗已在跑，复用")
        return
    script = ROOT / "data" / "verify-20261104" / "store_watch.py"
    if not script.exists():
        log("看门狗脚本不在，跳过（不影响跑场，只影响证据快照）")
        return
    logfile = DATA / f"store-watch-start-{time.strftime('%Y%m%d-%H%M%S')}.log"
    log(f"起看门狗 → {logfile.name}")
    with open(logfile, "ab") as fh:
        subprocess.Popen([PY, "-u", str(script), "--interval", "2", "--keep", "240",
                          "--max-minutes", "1440"], stdout=fh, stderr=fh,
                         cwd=str(ROOT), start_new_session=True)


def no_new_privs() -> bool:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("NoNewPrivs:"):
                return line.split()[1] == "1"
    except OSError:
        pass
    return False


def launch_browser(url: str, profile_dir: Path) -> None:
    exe = os.environ.get("JOBBOT_CHROMIUM", "").strip() or find_system_chromium()
    if not exe:
        raise SystemExit("[拒绝] 未找到 Chromium/Chrome，设 JOBBOT_CHROMIUM 指向浏览器")
    cmd = [exe, f"--user-data-dir={profile_dir}", f"--disable-extensions-except={EXT}",
           f"--load-extension={EXT}", "--no-first-run", "--no-default-browser-check", url]
    # 本类会话 shell NoNewPrivs=1 时 snap-confine 起不来 ⇒ 经 systemd 用户管理器逃逸。
    if no_new_privs() and "/snap/" in exe:
        display = os.environ.get("DISPLAY", ":0")
        xauth = os.environ.get("XAUTHORITY", "")
        wrapped = ["systemd-run", "--user", "--collect", f"--setenv=DISPLAY={display}"]
        if xauth:
            wrapped.append(f"--setenv=XAUTHORITY={xauth}")
        wrapped += cmd
        log("NoNewPrivs=1 + snap ⇒ 经 systemd-run --user 起浏览器")
        subprocess.run(wrapped, check=True, cwd=str(ROOT))
    else:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         cwd=str(ROOT), start_new_session=True)


def wait_run(key: str, run_id: str, timeout: int) -> dict | None:
    """轮询扩展存储，直到该 run 出现且 active=False（收官）。run_id 唯一，旧态不会误匹配。"""
    deadline = time.monotonic() + timeout
    seen = False
    while time.monotonic() < deadline:
        got, _note, _full = ar.read_state(key, ("run", "active", "phase", "results", "records"))
        if isinstance(got, dict) and got.get("run") == run_id:
            seen = True
            if got.get("active") is False:
                return got
        time.sleep(5)
    log(f"等 {key} run={run_id} 超时（{timeout}s，seen={seen}）")
    return None


def auto_send_url(results: list, run_id: str) -> str | None:
    for r in results:
        j = r.get("job") or {}
        if r.get("passed") and j.get("url"):
            return f"{j['url']}#jobbot_auto=auto_send&jobbot_run={run_id}"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="一键启动自动化AI找工作（桥+看门狗+扩展浏览器+auto_plan/可选auto_send）")
    ap.add_argument("--config", default="config/config.yaml")
    ap.add_argument("--send", action="store_true", help="计划完成后自动发送招呼（外发！默认只计划不发送）")
    ap.add_argument("--limit", type=int, default=None, help="覆盖本次入选数量")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--timeout", type=int, default=1800, help="每条腿等收官的秒数")
    ap.add_argument("--no-watchdog", action="store_true", help="不起存储看门狗")
    args = ap.parse_args()

    cfg = load_config(args.config)
    pc = cfg.platform("boss")
    profile_dir = pc.user_data_dir
    if profile_dir is None:
        log("[拒绝] boss 平台缺少 user_data_dir 配置")
        return 2
    profile_dir = Path(profile_dir)
    if not _login_cookie_present(profile_dir):
        log("[拒绝] 未检测到 BOSS 登录态，请先运行：.venv/bin/jobbot login")
        return 3

    holders = find_profile_instances(profile_dir)
    if holders:
        log(f"[提示] profile 被 {len(holders)} 个浏览器进程占用（pid={holders[0][0]}）；"
            "新 URL 会转给旧实例，若扩展是旧版请先关掉浏览器再跑")

    start_bridge(args.port)
    if not args.no_watchdog:
        start_watchdog()

    run_id = f"boss{int(time.time())}"
    plan_url = build_plan_url(cfg, "boss", run_id, limit=args.limit)
    log(f"run={run_id}｜城市={','.join(c.name for c in pc.search_cities)}｜"
        f"limit={args.limit if args.limit is not None else pc.limit_per_run}")
    log("开浏览器跑 auto_plan（搜索→评分→招呼语，不外发）")
    launch_browser(plan_url, profile_dir)

    st = wait_run("jobbotAutoState", run_id, args.timeout)
    if st is None:
        return 5
    results = st.get("results") or []
    passed = [r for r in results if r.get("passed")]
    log(f"auto_plan 收官：results={len(results)}，过闸={len(passed)}")
    for r in passed:
        j = r.get("job") or {}
        log(f"  ✓ {j.get('searchCity')} {j.get('company')}/{j.get('title')} "
            f"score={r.get('score')} salary={j.get('salary')}")

    if args.send:
        if not passed:
            log("没有过闸岗位，不发送")
        else:
            surl = auto_send_url(passed, run_id)
            log(f"[外发] --send 已开：自动发送 {len(passed)} 条招呼语")
            launch_browser(surl, profile_dir)
            ss = wait_run("jobbotAutoSendState", run_id, args.timeout)
            if ss is None:
                return 5
            recs = ss.get("records") or []
            sent = sum(1 for r in recs if r.get("status") == "sent")
            log(f"auto_send 收官：records={len(recs)}，sent={sent}")
    else:
        log("未开 --send：只生成计划，未发送任何消息")

    log("收口对账（audit_run）：")
    proc = subprocess.run([PY, str(ROOT / "data" / "verify-20261104" / "audit_run.py")],
                          cwd=str(ROOT), capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        if any(t in line for t in ("已见到证据", "未见证据", "判不了", "判定汇总")):
            print("  " + line.strip(), flush=True)
    log(f"完成 run={run_id}（audit EXIT={proc.returncode}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())