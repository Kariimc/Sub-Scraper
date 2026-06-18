"""Device Sync panel — copy your library to a portable audio player.

All worker-thread → widget updates are marshalled through a ``queue.Queue``
that is drained by ``self.after(100, self._drain_queue)`` on the main thread.
The sync itself runs in a ``threading.Thread(daemon=True)`` so the GUI stays
live throughout.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from typing import TYPE_CHECKING

import customtkinter as ctk

from ..core.config import Config
from ..core.device_sync import DeviceInfo, DeviceSyncer, SyncResult, detect_devices
from ..core.library_index import DownloadIndex
from .styles import (
    BLUE, BLUE_HOVER, BORDER, CARD_ALT, ERROR, FONT_MEDIUM, FONT_SECTION,
    FONT_SMALL, FONT_TITLE, HIGHLIGHT, HIGHLIGHT_HOVER, ORANGE, PANEL_BG,
    SUCCESS, TEXT_PRIMARY, TEXT_SECONDARY, WHITE,
)

if TYPE_CHECKING:
    pass  # Track is reconstructed via _IndexTrack below; no circular import needed.


# ---------------------------------------------------------------------------
# Mapping between display strings and internal values
# ---------------------------------------------------------------------------

_FORMAT_OPTIONS = [
    "As downloaded",
    "FLAC (lossless)",
    "MP3 320k",
    "MP3 192k",
    "AAC 256k",
]

_FORMAT_TO_CONVERT: dict[str, str | None] = {
    "As downloaded": None,
    "FLAC (lossless)": "flac",
    "MP3 320k": "mp3",
    "MP3 192k": "mp3",
    "AAC 256k": "aac",
}

_ORGANISE_OPTIONS = [
    "Flat (all in one folder)",
    "Artist / Album folders",
]

_ORGANISE_TO_KEY: dict[str, str] = {
    "Flat (all in one folder)": "flat",
    "Artist / Album folders": "artist_album",
}

_NO_DEVICE_LABEL = "No removable drives found — plug in your device and click Refresh"


def _human_size(n: int) -> str:
    size = float(max(0, n))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


# ---------------------------------------------------------------------------
# Panel
# ---------------------------------------------------------------------------

class DevicePanel(ctk.CTkFrame):
    """Top-level frame for the Device Sync feature.

    Grid layout (rows):
      0 — heading + subheading
      1 — device row (option menu + refresh)
      2 — format row (output format + file organisation)
      3 — track-list header bar (title + select/deselect buttons)
      4 — scrollable track list  [weight=1]
      5 — action bar (progress bar + sync button)
    """

    def __init__(self, master, config: Config, index: DownloadIndex) -> None:
        super().__init__(master, fg_color="transparent")
        self._config = config
        self._index = index

        # Detected devices; list index mirrors the option-menu values list.
        self._devices: list[DeviceInfo] = []
        # Track checkboxes: track.id -> BooleanVar
        self._track_vars: dict[str, tk.BooleanVar] = {}
        # Track objects in display order.
        self._tracks: list[_IndexTrack] = []
        # Sync worker state.
        self._syncing = False
        self._queue: queue.Queue = queue.Queue()

        # The scrollable list is the only vertically expanding row.
        self.rowconfigure(4, weight=1)
        self.columnconfigure(0, weight=1)

        self._build_header()       # row 0
        self._build_device_row()   # row 1
        self._build_format_row()   # row 2
        self._build_track_list()   # rows 3 + 4
        self._build_action_bar()   # row 5
        self._build_toast()

        self._refresh_devices()
        self._refresh_track_list()

        self.after(100, self._drain_queue)

    # ------------------------------------------------------------------
    # Layout builders
    # ------------------------------------------------------------------

    def _build_header(self) -> None:
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=20, pady=(20, 4))

        ctk.CTkLabel(
            hdr, text="Device Sync",
            font=FONT_TITLE, text_color=TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            hdr, text="Copy your music to a portable player",
            font=FONT_SMALL, text_color=TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(2, 0))

    def _build_device_row(self) -> None:
        card = ctk.CTkFrame(self, fg_color=PANEL_BG, corner_radius=10,
                            border_width=1, border_color=BORDER)
        card.grid(row=1, column=0, sticky="ew", padx=20, pady=(8, 0))
        card.columnconfigure(1, weight=1)

        ctk.CTkLabel(
            card, text="Target device",
            font=FONT_MEDIUM, text_color=TEXT_PRIMARY,
            width=120, anchor="w",
        ).grid(row=0, column=0, padx=(14, 8), pady=14, sticky="w")

        self._device_var = tk.StringVar(value=_NO_DEVICE_LABEL)
        self._device_menu = ctk.CTkOptionMenu(
            card,
            variable=self._device_var,
            values=[_NO_DEVICE_LABEL],
            fg_color=CARD_ALT,
            button_color=BLUE,
            button_hover_color=BLUE_HOVER,
            text_color=TEXT_PRIMARY,
            font=FONT_MEDIUM,
            dynamic_resizing=False,
            width=380,
        )
        self._device_menu.grid(row=0, column=1, padx=(0, 8), pady=14, sticky="w")

        ctk.CTkButton(
            card,
            text="Refresh",
            width=90,
            fg_color=BLUE,
            hover_color=BLUE_HOVER,
            text_color=WHITE,
            font=FONT_SMALL,
            command=self._refresh_devices,
        ).grid(row=0, column=2, padx=(0, 14), pady=14)

    def _build_format_row(self) -> None:
        card = ctk.CTkFrame(self, fg_color=PANEL_BG, corner_radius=10,
                            border_width=1, border_color=BORDER)
        card.grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 0))

        # Output Format
        ctk.CTkLabel(
            card, text="Output Format",
            font=FONT_MEDIUM, text_color=TEXT_PRIMARY,
            width=120, anchor="w",
        ).grid(row=0, column=0, padx=(14, 8), pady=12, sticky="w")

        self._format_var = tk.StringVar(value=_FORMAT_OPTIONS[0])
        ctk.CTkOptionMenu(
            card,
            variable=self._format_var,
            values=_FORMAT_OPTIONS,
            fg_color=CARD_ALT,
            button_color=BLUE,
            button_hover_color=BLUE_HOVER,
            text_color=TEXT_PRIMARY,
            font=FONT_MEDIUM,
            dynamic_resizing=False,
            width=220,
        ).grid(row=0, column=1, padx=(0, 24), pady=12, sticky="w")

        # File Organisation
        ctk.CTkLabel(
            card, text="File Organisation",
            font=FONT_MEDIUM, text_color=TEXT_PRIMARY,
            width=130, anchor="w",
        ).grid(row=0, column=2, padx=(0, 8), pady=12, sticky="w")

        self._organise_var = tk.StringVar(value=_ORGANISE_OPTIONS[0])
        ctk.CTkOptionMenu(
            card,
            variable=self._organise_var,
            values=_ORGANISE_OPTIONS,
            fg_color=CARD_ALT,
            button_color=BLUE,
            button_hover_color=BLUE_HOVER,
            text_color=TEXT_PRIMARY,
            font=FONT_MEDIUM,
            dynamic_resizing=False,
            width=240,
        ).grid(row=0, column=3, padx=(0, 14), pady=12, sticky="w")

    def _build_track_list(self) -> None:
        # Header bar: section label + track count + select/deselect buttons
        list_hdr = ctk.CTkFrame(self, fg_color="transparent")
        list_hdr.grid(row=3, column=0, sticky="ew", padx=20, pady=(14, 2))

        ctk.CTkLabel(
            list_hdr, text="Tracks to sync",
            font=FONT_SECTION, text_color=TEXT_PRIMARY,
        ).pack(side="left")

        self._track_count_lbl = ctk.CTkLabel(
            list_hdr, text="",
            font=FONT_SMALL, text_color=TEXT_SECONDARY,
        )
        self._track_count_lbl.pack(side="left", padx=(12, 0))

        ctk.CTkButton(
            list_hdr, text="Deselect All", width=100, height=28,
            fg_color="transparent", hover_color=BORDER,
            text_color=TEXT_SECONDARY, font=FONT_SMALL,
            command=self._deselect_all,
        ).pack(side="right", padx=(6, 0))

        ctk.CTkButton(
            list_hdr, text="Select All", width=90, height=28,
            fg_color="transparent", hover_color=BORDER,
            text_color=TEXT_SECONDARY, font=FONT_SMALL,
            command=self._select_all,
        ).pack(side="right")

        # Scrollable list
        self._scroll = ctk.CTkScrollableFrame(self, fg_color=CARD_ALT, corner_radius=10)
        self._scroll.grid(row=4, column=0, sticky="nsew", padx=20, pady=(0, 8))

    def _build_action_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=PANEL_BG, corner_radius=10,
                           border_width=1, border_color=BORDER)
        bar.grid(row=5, column=0, sticky="ew", padx=20, pady=(0, 20))
        bar.columnconfigure(0, weight=1)

        # Left side: progress bar + label
        left = ctk.CTkFrame(bar, fg_color="transparent")
        left.grid(row=0, column=0, sticky="ew", padx=14, pady=14)
        left.columnconfigure(0, weight=1)

        self._progress_bar = ctk.CTkProgressBar(
            left, height=10, corner_radius=5, progress_color=ORANGE,
        )
        self._progress_bar.set(0)
        self._progress_bar.grid(row=0, column=0, sticky="ew", pady=(0, 4))

        self._progress_lbl = ctk.CTkLabel(
            left, text="0 / 0 files",
            font=FONT_SMALL, text_color=TEXT_SECONDARY, anchor="w",
        )
        self._progress_lbl.grid(row=1, column=0, sticky="w")

        # Right side: sync button
        self._sync_btn = ctk.CTkButton(
            bar,
            text="Sync to Device",
            width=160,
            fg_color=HIGHLIGHT,
            hover_color=HIGHLIGHT_HOVER,
            text_color=WHITE,
            font=FONT_MEDIUM,
            command=self._on_sync_clicked,
        )
        self._sync_btn.grid(row=0, column=1, padx=(0, 14), pady=14)

    def _build_toast(self) -> None:
        self._toast_frame = ctk.CTkFrame(self, fg_color=SUCCESS, corner_radius=18)
        self._toast_lbl = ctk.CTkLabel(
            self._toast_frame, text="",
            font=FONT_MEDIUM, text_color=WHITE,
        )
        self._toast_lbl.pack(padx=20, pady=10)
        self._toast_after: str | None = None

    # ------------------------------------------------------------------
    # Device management
    # ------------------------------------------------------------------

    def _refresh_devices(self) -> None:
        """Re-scan for removable drives and repopulate the option menu."""
        self._devices = detect_devices()
        if self._devices:
            labels = [str(d) for d in self._devices]
            self._device_menu.configure(values=labels)
            self._device_var.set(labels[0])
        else:
            self._device_menu.configure(values=[_NO_DEVICE_LABEL])
            self._device_var.set(_NO_DEVICE_LABEL)

    def _selected_device(self) -> DeviceInfo | None:
        """Return the currently-chosen DeviceInfo, or None if none detected."""
        if not self._devices:
            return None
        label = self._device_var.get()
        if label == _NO_DEVICE_LABEL:
            return None
        for device in self._devices:
            if str(device) == label:
                return device
        # Label mismatch fallback (shouldn't occur in practice).
        return self._devices[0]

    # ------------------------------------------------------------------
    # Track list
    # ------------------------------------------------------------------

    def _get_tracks(self) -> list[_IndexTrack]:
        """Return tracks from the index that have a ``local_path`` set and
        whose file still exists on disk."""
        with self._index._lock:  # type: ignore[attr-defined]
            snapshot = dict(self._index._entries)  # type: ignore[attr-defined]
        result: list[_IndexTrack] = []
        for key, entry in snapshot.items():
            path = entry.get("path") or ""
            if not path or not Path(path).is_file():
                continue
            result.append(_IndexTrack(
                id=key,
                title=entry.get("title") or "",
                artist=entry.get("artist") or "",
                local_path=path,
                size_bytes=int(entry.get("size") or 0),
                album="",
            ))
        return result

    def _refresh_track_list(self) -> None:
        """Rebuild the scrollable track list from the current index."""
        for widget in self._scroll.winfo_children():
            widget.destroy()
        self._track_vars.clear()

        self._tracks = self._get_tracks()
        for track in self._tracks:
            self._add_track_row(track)

        count = len(self._tracks)
        label = f"{count} track{'s' if count != 1 else ''} available"
        self._track_count_lbl.configure(text=label)
        self._progress_lbl.configure(text=f"0 / {count} files")

    def _add_track_row(self, track: _IndexTrack) -> None:
        var = tk.BooleanVar(value=True)
        self._track_vars[track.id] = var

        row = ctk.CTkFrame(self._scroll, fg_color="transparent")
        row.pack(fill="x", pady=1)

        ctk.CTkCheckBox(
            row, variable=var, text="",
            width=28, onvalue=True, offvalue=False,
        ).pack(side="left", padx=(6, 4))

        display = f"{track.artist} - {track.title}" if track.artist else track.title
        ctk.CTkLabel(
            row, text=display,
            font=FONT_MEDIUM, text_color=TEXT_PRIMARY, anchor="w",
        ).pack(side="left", fill="x", expand=True)

        if track.size_bytes:
            ctk.CTkLabel(
                row, text=_human_size(track.size_bytes),
                font=FONT_SMALL, text_color=TEXT_SECONDARY, width=72, anchor="e",
            ).pack(side="right", padx=(0, 10))

    # ------------------------------------------------------------------
    # Selection helpers
    # ------------------------------------------------------------------

    def _select_all(self) -> None:
        for var in self._track_vars.values():
            var.set(True)

    def _deselect_all(self) -> None:
        for var in self._track_vars.values():
            var.set(False)

    def _selected_tracks(self) -> list[_IndexTrack]:
        return [t for t in self._tracks if self._track_vars.get(t.id, tk.BooleanVar()).get()]

    # ------------------------------------------------------------------
    # Sync action
    # ------------------------------------------------------------------

    def _on_sync_clicked(self) -> None:
        if self._syncing:
            return

        device = self._selected_device()
        if device is None:
            self._toast(
                "No device selected — plug in your player and click Refresh.",
                color=ERROR,
            )
            return

        tracks = self._selected_tracks()
        if not tracks:
            self._toast(
                "No tracks selected — tick at least one track to sync.",
                color=ERROR,
            )
            return

        convert_to = _FORMAT_TO_CONVERT.get(self._format_var.get())
        organise = _ORGANISE_TO_KEY.get(self._organise_var.get(), "flat")
        total = len(tracks)

        self._syncing = True
        self._sync_btn.configure(state="disabled", text="Syncing…")
        self._progress_bar.set(0)
        self._progress_lbl.configure(text=f"0 / {total} files")

        threading.Thread(
            target=self._run_sync,
            args=(device, tracks, organise, convert_to, total),
            daemon=True,
        ).start()

    def _run_sync(
        self,
        device: DeviceInfo,
        tracks: list[_IndexTrack],
        organise: str,
        convert_to: str | None,
        total: int,
    ) -> None:
        """Worker: runs in a daemon thread.  All widget access goes via self._queue."""
        def on_progress(fraction: float, message: str) -> None:
            # DeviceSyncer emits fraction = files_done / total, so use it directly.
            done_count = round(fraction * total)
            self._queue.put(("progress", fraction, done_count, total, message))

        def on_log(msg: str) -> None:
            self._queue.put(("log", msg))

        syncer = DeviceSyncer(device, on_log=on_log, on_progress=on_progress)
        try:
            result: SyncResult = syncer.sync(
                tracks, organize=organise, convert_to=convert_to,
            )
            self._queue.put(("done", result))
        except Exception as exc:  # noqa: BLE001
            self._queue.put(("error", str(exc)))

    # ------------------------------------------------------------------
    # Queue drain (main thread)
    # ------------------------------------------------------------------

    def _drain_queue(self) -> None:
        """Drain all pending worker messages and update widgets (main thread only)."""
        try:
            for _ in range(200):  # cap per tick so the UI stays responsive
                msg = self._queue.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    _, fraction, done_count, total, _message = msg
                    self._progress_bar.set(fraction)
                    self._progress_lbl.configure(text=f"{done_count} / {total} files")
                elif kind == "log":
                    pass  # reserved: could wire into a CTkTextbox if desired
                elif kind == "done":
                    self._on_sync_done(msg[1])
                elif kind == "error":
                    self._on_sync_error(msg[1])
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    # ------------------------------------------------------------------
    # Sync completion handlers (main thread)
    # ------------------------------------------------------------------

    def _on_sync_done(self, result: SyncResult) -> None:
        self._syncing = False
        self._sync_btn.configure(state="normal", text="Sync to Device")
        total = len(self._tracks)
        self._progress_bar.set(1.0)
        self._progress_lbl.configure(text=f"{result.synced} / {total} files")

        parts = [f"Synced {result.synced} track{'s' if result.synced != 1 else ''}"]
        if result.skipped:
            parts.append(f"skipped {result.skipped} already on device")
        if result.failed:
            parts.append(f"{result.failed} failed")
        self._toast(", ".join(parts) + ".", color=ERROR if result.failed else SUCCESS)

    def _on_sync_error(self, error: str) -> None:
        self._syncing = False
        self._sync_btn.configure(state="normal", text="Sync to Device")
        self._toast(f"Sync failed: {error}", color=ERROR)

    # ------------------------------------------------------------------
    # Toast
    # ------------------------------------------------------------------

    def _toast(self, message: str, *, color: str = SUCCESS, duration: int = 5000) -> None:
        self._toast_frame.configure(fg_color=color)
        self._toast_lbl.configure(text=message)
        self._toast_frame.place(relx=0.5, rely=0.02, anchor="n")
        self._toast_frame.lift()
        if self._toast_after is not None:
            try:
                self.after_cancel(self._toast_after)
            except Exception:  # noqa: BLE001
                pass
        self._toast_after = self.after(duration, self._hide_toast)

    def _hide_toast(self) -> None:
        self._toast_frame.place_forget()
        self._toast_after = None


# ---------------------------------------------------------------------------
# Lightweight index-track stand-in
# ---------------------------------------------------------------------------

class _IndexTrack:
    """Minimal track-like object reconstructed from a DownloadIndex entry.

    Has the same ``local_path``, ``artist``, ``title``, ``album``, and ``id``
    attributes as :class:`~sub_scraper.scrapers.base.Track` so
    :class:`~sub_scraper.core.device_sync.DeviceSyncer` can operate on it
    without any circular imports.
    """

    __slots__ = ("id", "title", "artist", "album", "local_path", "size_bytes")

    def __init__(
        self,
        *,
        id: str,
        title: str,
        artist: str,
        local_path: str,
        size_bytes: int = 0,
        album: str = "",
    ) -> None:
        self.id = id
        self.title = title
        self.artist = artist
        self.album = album
        self.local_path = local_path
        self.size_bytes = size_bytes


__all__ = ["DevicePanel"]
