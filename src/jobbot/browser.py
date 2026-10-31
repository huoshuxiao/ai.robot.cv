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


def find_system_chromium() -> str | None:
    for candidate in SYSTEM_CHROMIUM_CANDIDATES:
        resolved = shutil.which(candidate) or candidate
        path = Path(resolved)
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None
