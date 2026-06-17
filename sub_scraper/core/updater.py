"""Keep yt-dlp fresh.

YouTube/SoundCloud rotate their extraction internals constantly, so a yt-dlp
that's a few weeks old is the single most common reason "nothing downloads".
On launch we run ``pip install -U yt-dlp`` in a background thread and surface a
one-line result; it never blocks the UI and never crashes the app if pip or the
network is unavailable.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Callable, Optional

from .logging_config import get_logger

log = get_logger("updater")

# Result codes returned by update_ytdlp / classify_pip_output.
UPDATED = "updated"
CURRENT = "current"
FAILED = "failed"


def classify_pip_output(returncode: int, output: str) -> str:
    """Map a ``pip install -U`` run to UPDATED / CURRENT / FAILED.

    Pure + testable: no subprocess, just the exit code and combined output.
    """
    if returncode != 0:
        return FAILED
    low = output.lower()
    if "successfully installed" in low:
        return UPDATED
    # "Requirement already satisfied" / "already up-to-date" => nothing changed.
    return CURRENT


def update_ytdlp(
    on_log: Optional[Callable[[str], None]] = None,
    *,
    timeout: float = 120.0,
) -> str:
    """Upgrade yt-dlp via pip. Returns UPDATED / CURRENT / FAILED.

    Best-effort: any exception (no pip, offline, sandbox) is swallowed and
    reported as FAILED so startup is never affected.
    """
    cmd = [sys.executable, "-m", "pip", "install", "-U", "--disable-pip-version-check", "yt-dlp"]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001 - network/sandbox/no-pip; non-fatal
        log.info(f"updater.skip error={exc}")
        if on_log:
            on_log(f"[updater] yt-dlp update skipped ({exc})")
        return FAILED

    result = classify_pip_output(proc.returncode, (proc.stdout or "") + (proc.stderr or ""))
    if result == UPDATED:
        log.info("updater.ytdlp.updated")
        if on_log:
            on_log("[updater] yt-dlp updated to the latest version")
    elif result == CURRENT:
        log.info("updater.ytdlp.current")
        if on_log:
            on_log("[updater] yt-dlp is already up to date")
    else:
        log.warning("updater.ytdlp.failed " + (proc.stderr or "").strip()[:200])
        if on_log:
            on_log("[updater] could not update yt-dlp (continuing with the installed version)")
    return result
