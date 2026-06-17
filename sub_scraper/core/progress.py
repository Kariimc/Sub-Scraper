"""Parse live download progress out of yt-dlp / spotdl stdout.

yt-dlp prints progress lines like::

    [download]  45.2% of 5.00MiB at 1.20MiB/s ETA 00:03
    [download] 100% of 5.00MiB in 00:04

spotdl drives yt-dlp under the hood and forwards the same lines (sometimes
prefixed). We only need three things off each line: the completion fraction, a
human speed string, and an ETA string. Everything is best-effort — anything we
can't parse simply yields ``None`` and the caller keeps the previous state.

This is a pure, side-effect-free module so it can be unit-tested without a real
downloader.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A percentage anywhere on the line (e.g. "45.2%"). We additionally require the
# line to look like a download-progress line (see ``parse_progress``) so stray
# percentages in log chatter don't move the bar.
_PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
_SPEED = re.compile(r"\bat\s+([\d.]+\s*(?:[KMGT]i?)?B/s)", re.IGNORECASE)
_ETA = re.compile(r"\bETA\s+([\d:]+)", re.IGNORECASE)


@dataclass(frozen=True)
class ProgressInfo:
    fraction: float        # 0.0 .. 1.0
    speed: str = ""        # e.g. "1.20MiB/s"
    eta: str = ""          # e.g. "00:03"


def parse_progress(line: str) -> "ProgressInfo | None":
    """Return :class:`ProgressInfo` if ``line`` is a download-progress line.

    A line counts as progress only when it carries a percentage *and* a
    download marker (``[download]``/``downloading``) or a speed/ETA token — this
    keeps us from reacting to unrelated percentages in metadata or error text.
    """
    if not line:
        return None
    pct_m = _PCT.search(line)
    if pct_m is None:
        return None

    low = line.lower()
    has_marker = "[download]" in low or "downloading" in low
    speed_m = _SPEED.search(line)
    eta_m = _ETA.search(line)
    if not (has_marker or speed_m or eta_m):
        return None

    try:
        pct = float(pct_m.group(1))
    except ValueError:
        return None
    fraction = max(0.0, min(1.0, pct / 100.0))
    return ProgressInfo(
        fraction=fraction,
        speed=(speed_m.group(1).strip() if speed_m else ""),
        eta=(eta_m.group(1).strip() if eta_m else ""),
    )
