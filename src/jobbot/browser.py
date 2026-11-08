from __future__ import annotations

import os
import shutil
from pathlib import Path

SYSTEM_CHROMIUM_CANDIDATES = (
    "chromium",
    "chromium-browser",
    "google-chrome",
    "google-chrome-stable",
    "/snap/bin/chromium",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/opt/google/chrome/chrome",
)


def profile_flags(cmdline: str) -> list[str]:
    """从命令行里取出所有 --user-data-dir 的取值（NUL 分隔或空格分隔都支持）。"""
    text = cmdline.replace("\0", " ")
    values: list[str] = []
    for chunk in text.split("--user-data-dir")[1:]:
        piece = chunk.lstrip("=").strip().split(" ")[0].strip()
        if piece:
            values.append(piece)
    return values


def find_profile_instances(user_data_dir: Path) -> list[tuple[int, float, str]]:
    """正占用该 user-data-dir 的浏览器进程 [(pid, 启动时间, 进程名)]。

    同一个 profile 上若跑着更早启动的实例，`--load-extension` 只会把 URL 转给它，
    新代码不会被加载 —— 11-02 那次旧版扩展绕过分数闸就是这么来的。
    """
    target = str(Path(user_data_dir).expanduser())
    resolved = str(Path(target).resolve()) if Path(target).is_absolute() else target
    my_pid = os.getpid()
    found: list[tuple[int, float, str]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == my_pid:
            continue
        try:
            cmdline = entry.joinpath("cmdline").read_bytes().decode("utf-8", "ignore")
        except OSError:
            continue
        if "--user-data-dir" not in cmdline:
            continue
        used = profile_flags(cmdline)
        if not any(
            v == target or v == resolved or os.path.realpath(os.path.expanduser(v)) == resolved
            for v in used
        ):
            continue
        try:
            comm = entry.joinpath("comm").read_text().strip()
            started = entry.stat().st_mtime
        except OSError:
            continue
        if comm.startswith(("chrome", "chromium")):
            found.append((int(entry.name), started, comm))
    return sorted(found, key=lambda row: row[1])


def find_system_chromium() -> str | None:
    for candidate in SYSTEM_CHROMIUM_CANDIDATES:
        resolved = shutil.which(candidate) or candidate
        path = Path(resolved)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None
