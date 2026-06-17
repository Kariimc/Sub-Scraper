"""Cross-platform desktop OS integration: reveal a file in the system file
manager, open a file with its default app, and post a native notification.

The command *builders* are pure functions returning an argv list (or ``None``
when the platform/path can't support the action) so they can be unit-tested
without spawning anything. The thin public wrappers run them with a detached
``subprocess`` and never raise — these are conveniences, not core flows.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

from .logging_config import get_logger

log = get_logger("desktop")


def _platform() -> str:
    if sys.platform.startswith("darwin"):
        return "darwin"
    if sys.platform.startswith("win"):
        return "win"
    return "linux"


# ---------------------------------------------------------------------------
# Pure command builders (testable)
# ---------------------------------------------------------------------------

def reveal_command(path: str, platform: Optional[str] = None) -> Optional[list[str]]:
    """argv that reveals ``path`` *selected* in the OS file manager."""
    if not path:
        return None
    plat = platform or _platform()
    if plat == "darwin":
        return ["open", "-R", path]
    if plat == "win":
        # explorer wants the switch and path glued together, no space.
        return ["explorer", f"/select,{path}"]
    # Linux: no portable "select" — open the containing directory.
    parent = str(Path(path).parent)
    return ["xdg-open", parent]


def open_command(path: str, platform: Optional[str] = None) -> Optional[list[str]]:
    """argv that opens ``path`` with its default application."""
    if not path:
        return None
    plat = platform or _platform()
    if plat == "darwin":
        return ["open", path]
    if plat == "win":
        # Handled specially via os.startfile in open_path(); no argv form.
        return None
    return ["xdg-open", path]


def notify_command(
    title: str, message: str, platform: Optional[str] = None
) -> Optional[list[str]]:
    """argv that posts a desktop notification, or ``None`` if unsupported."""
    plat = platform or _platform()
    if plat == "darwin":
        script = f'display notification {_osa(message)} with title {_osa(title)}'
        return ["osascript", "-e", script]
    if plat == "linux":
        return ["notify-send", "-a", "Sub-Scraper", title, message]
    # Windows toast needs PowerShell scripting; skip rather than ship something
    # fragile. The in-app toast still fires.
    return None


def _osa(text: str) -> str:
    """Quote a string as an AppleScript literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


# ---------------------------------------------------------------------------
# Side-effecting wrappers (best-effort, never raise)
# ---------------------------------------------------------------------------

def _spawn(cmd: Optional[list[str]]) -> bool:
    if not cmd:
        return False
    try:
        subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except Exception as exc:  # noqa: BLE001 - convenience action, never fatal
        log.debug(f"desktop.spawn.failed cmd={cmd[0]} error={exc}")
        return False


def reveal_in_folder(path: str) -> bool:
    return _spawn(reveal_command(path))


def open_path(path: str) -> bool:
    if not path:
        return False
    if _platform() == "win":
        try:
            import os
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606
            return True
        except Exception as exc:  # noqa: BLE001
            log.debug(f"desktop.open.failed error={exc}")
            return False
    return _spawn(open_command(path))


def notify(title: str, message: str) -> bool:
    return _spawn(notify_command(title, message))
