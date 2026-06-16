from __future__ import annotations

import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from ..core.config import Config
from ..core.download_manager import DownloadJob, DownloadManager
from ..core.library_index import DownloadIndex
from ..core.logging_config import configure_logging
from .logo import get_ctk_image, set_window_icon
from .wizard import SetupWizard
from ..scrapers.base import DownloadStatus, Track
from ..scrapers.soundcloud import SoundCloudScraper
from ..scrapers.spotify import SpotifyScraper
from .styles import (
    BORDER, DARK_BG, ERROR, FONT_BRAND, FONT_MEDIUM, FONT_MONO,
    FONT_SECTION, FONT_SMALL, FONT_TITLE, HIGHLIGHT, HIGHLIGHT_HOVER, INFO,
    NAVY_LIGHT, ORANGE, PANEL_BG, SIDEBAR_BG, SUCCESS, TEXT_ON_NAVY,
    TEXT_ON_NAVY_MUTED, TEXT_PRIMARY, TEXT_SECONDARY, WHITE,
)

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

_RENDER_BATCH = 50  # rows created per after(0) tick during library load


def _norm(text: str) -> str:
    """Case-fold + alphanumeric-only normalisation (mirrors library_index._normalise)."""
    keep = [ch.lower() for ch in text if ch.isalnum() or ch.isspace()]
    return " ".join("".join(keep).split())

_STATUS_COLOR = {
    DownloadStatus.PENDING: TEXT_SECONDARY,
    DownloadStatus.DOWNLOADING: INFO,
    DownloadStatus.COMPLETE: SUCCESS,
    DownloadStatus.FAILED: ERROR,
}
_STATUS_ICON = {
    DownloadStatus.PENDING: "○",
    DownloadStatus.DOWNLOADING: "↓",
    DownloadStatus.COMPLETE: "✓",
    DownloadStatus.FAILED: "✗",
}


# ---------------------------------------------------------------------------
# Track row widget
# ---------------------------------------------------------------------------

class TrackRow(ctk.CTkFrame):
    def __init__(self, master: ctk.CTkScrollableFrame, track: Track, on_toggle: callable) -> None:
        super().__init__(master, fg_color=PANEL_BG, corner_radius=8,
                         border_width=1, border_color=BORDER)
        self.track = track
        # True when this track was already on disk at load time — used to hide
        # it under the "show downloaded" toggle without affecting live results.
        self.pre_downloaded = False
        self.selected = tk.BooleanVar(value=False)
        self._on_toggle = on_toggle

        self._checkbox = ctk.CTkCheckBox(
            self, variable=self.selected, text="", width=28,
            onvalue=True, offvalue=False,
        )
        self._checkbox.pack(side="left", padx=(8, 4))
        # Track selection via the variable's trace rather than the checkbox
        # command: this fires for clicks, Select All and Deselect All alike,
        # and reliably reports both select AND deselect.
        self.selected.trace_add("write", self._notify_toggle)

        info = ctk.CTkFrame(self, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, pady=6)

        ctk.CTkLabel(info, text=track.title, anchor="w", font=FONT_MEDIUM, text_color=TEXT_PRIMARY).pack(fill="x")
        ctk.CTkLabel(info, text=track.artist, anchor="w", font=FONT_SMALL, text_color=TEXT_SECONDARY).pack(fill="x")

        dur_s = track.duration_ms // 1000
        dur_str = f"{dur_s // 60}:{dur_s % 60:02d}" if dur_s else "--:--"
        ctk.CTkLabel(self, text=dur_str, font=FONT_SMALL, text_color=TEXT_SECONDARY, width=52).pack(
            side="right", padx=8
        )

        self._status_lbl = ctk.CTkLabel(
            self, text=_STATUS_ICON[track.status], font=FONT_MEDIUM,
            text_color=_STATUS_COLOR[track.status], width=28,
        )
        self._status_lbl.pack(side="right", padx=4)

    def _notify_toggle(self, *_) -> None:
        self._on_toggle(self.track, self.selected.get())

    def refresh_status(self) -> None:
        self._status_lbl.configure(
            text=_STATUS_ICON[self.track.status],
            text_color=_STATUS_COLOR[self.track.status],
        )

    def set_selected(self, value: bool) -> None:
        self.selected.set(value)


# ---------------------------------------------------------------------------
# Library panel  (grid layout: row0=header, row1=list, row2=log, row3=footer)
# ---------------------------------------------------------------------------

class LibraryPanel(ctk.CTkFrame):
    def __init__(self, master, config: Config, manager: DownloadManager, index: DownloadIndex) -> None:
        super().__init__(master, fg_color="transparent")
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        self._config = config
        self._manager = manager
        self._index = index
        self._tracks: list[Track] = []
        self._rows: dict[str, TrackRow] = {}
        self._selected: set[str] = set()
        self._log_visible = False

        # Batch progress state (updated only on the main thread).
        self._dl_total = 0
        self._dl_done = 0
        self._dl_failed = 0
        self._dl_active: dict[str, str] = {}  # track id -> display name

        # All worker-thread → GUI updates flow through this queue and are
        # drained on the main thread. tkinter is not thread-safe, so workers
        # must never touch widgets directly.
        self._events: queue.Queue = queue.Queue()
        self._playlists: list[dict] = []
        self._scraper = None
        self._populate_gen = 0  # incremented per load; cancels stale chunk renders

        self._build_header()
        self._build_list()
        self._build_log_panel()
        self._build_status_bar()
        self._build_footer()

        self.after(100, self._process_events)

    # ------------------------------------------------------------------
    # Layout builders
    # ------------------------------------------------------------------

    def _build_header(self) -> None:
        hdr = ctk.CTkFrame(self, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))

        ctk.CTkLabel(hdr, text="Your Library", font=FONT_TITLE, text_color=TEXT_PRIMARY).pack(
            anchor="w", pady=(0, 10)
        )

        # Top row: source selector, search, load button
        top = ctk.CTkFrame(hdr, fg_color="transparent")
        top.pack(fill="x")

        self._source_var = tk.StringVar(value="Spotify")
        ctk.CTkSegmentedButton(
            top, values=["Spotify", "SoundCloud"],
            variable=self._source_var,
            command=self._on_source_change,
        ).pack(side="left")

        self._load_btn = ctk.CTkButton(top, text="Load Library", width=130, command=self._load_library)
        self._load_btn.pack(side="right")

        self._search_var = tk.StringVar()
        self._search_var.trace_add("write", self._refresh_visibility)
        ctk.CTkEntry(top, textvariable=self._search_var, placeholder_text="Search...", width=200).pack(
            side="right", padx=8
        )

        self._status_lbl = ctk.CTkLabel(top, text="", font=FONT_SMALL, text_color=TEXT_SECONDARY)
        self._status_lbl.pack(side="left", padx=16)

        # Content-mode row: liked songs vs playlist picker + show-downloaded toggle
        content_row = ctk.CTkFrame(hdr, fg_color="transparent")
        content_row.pack(fill="x", pady=(8, 0))

        self._content_var = tk.StringVar(value="Liked Songs")
        ctk.CTkSegmentedButton(
            content_row, values=["Liked Songs", "Playlists"],
            variable=self._content_var,
            command=self._on_content_change,
        ).pack(side="left")

        self._playlist_var = tk.StringVar(value="")
        self._playlist_menu = ctk.CTkOptionMenu(
            content_row, values=[""], variable=self._playlist_var,
            command=self._on_playlist_select, width=280,
        )
        # Not packed until playlists have been fetched

        # Already-downloaded tracks are hidden by default to keep the list to
        # what's left to grab; this reveals them on demand.
        self._show_dl_var = tk.BooleanVar(value=not bool(self._config.hide_downloaded))
        ctk.CTkCheckBox(
            content_row, text="Show downloaded", variable=self._show_dl_var,
            font=FONT_SMALL, text_color=TEXT_SECONDARY, command=self._refresh_visibility,
        ).pack(side="right")

    def _build_list(self) -> None:
        self._scroll = ctk.CTkScrollableFrame(self, fg_color=DARK_BG)
        self._scroll.grid(row=1, column=0, sticky="nsew", padx=16, pady=4)
        self._bind_mousewheel(self._scroll)

    # ------------------------------------------------------------------
    # Mouse-wheel scrolling
    # ------------------------------------------------------------------

    def _scroll_canvas(self):
        return getattr(self._scroll, "_parent_canvas", None)

    def _on_mousewheel(self, event) -> None:
        canvas = self._scroll_canvas()
        if canvas is None:
            return
        if event.num == 4:            # Linux scroll up
            canvas.yview_scroll(-1, "units")
        elif event.num == 5:          # Linux scroll down
            canvas.yview_scroll(1, "units")
        elif event.delta:             # Windows / macOS
            canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def _bind_mousewheel(self, widget) -> None:
        widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
        widget.bind("<Button-4>", self._on_mousewheel, add="+")
        widget.bind("<Button-5>", self._on_mousewheel, add="+")
        for child in widget.winfo_children():
            self._bind_mousewheel(child)

    def _build_log_panel(self) -> None:
        self._log_frame = ctk.CTkFrame(self, fg_color=PANEL_BG, corner_radius=8,
                                       border_width=1, border_color=BORDER)

        log_hdr = ctk.CTkFrame(self._log_frame, fg_color="transparent")
        log_hdr.pack(fill="x", padx=8, pady=(6, 2))
        ctk.CTkLabel(log_hdr, text="Download Log", font=FONT_SMALL, text_color=TEXT_SECONDARY).pack(side="left")
        ctk.CTkButton(
            log_hdr, text="Clear", width=56, height=24, font=FONT_SMALL,
            command=self._clear_log,
        ).pack(side="right")

        self._log_box = ctk.CTkTextbox(
            self._log_frame, height=140, font=FONT_MONO,
            fg_color=DARK_BG, text_color=TEXT_PRIMARY,
        )
        self._log_box.pack(fill="x", padx=8, pady=(0, 8))
        self._log_box.configure(state="disabled")

    def _build_status_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=PANEL_BG, corner_radius=8,
                           border_width=1, border_color=BORDER)
        bar.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 4))
        bar.columnconfigure(0, weight=1)

        self._status_bar_lbl = ctk.CTkLabel(
            bar, text="Ready — no downloads yet", anchor="w",
            font=FONT_SMALL, text_color=TEXT_SECONDARY,
        )
        self._status_bar_lbl.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 2))

        self._progress_bar = ctk.CTkProgressBar(bar, height=8, progress_color=ORANGE)
        self._progress_bar.set(0)
        self._progress_bar.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

    def _build_footer(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=4, column=0, sticky="ew", padx=16, pady=(4, 16))

        ctk.CTkButton(bar, text="Select All", width=100, command=self._select_all).pack(side="left", padx=(0, 6))
        ctk.CTkButton(bar, text="Deselect All", width=110, command=self._deselect_all).pack(side="left")

        self._log_btn = ctk.CTkButton(
            bar, text="▶ Log", width=70, font=FONT_SMALL,
            fg_color="transparent", hover_color=BORDER, text_color=TEXT_SECONDARY,
            command=self._toggle_log,
        )
        self._log_btn.pack(side="left", padx=(14, 0))

        ctk.CTkButton(bar, text="Download All", width=120, command=self._download_all).pack(
            side="right", padx=(8, 0)
        )
        ctk.CTkButton(
            bar, text="Download Selected", width=150,
            fg_color=HIGHLIGHT, hover_color=HIGHLIGHT_HOVER, text_color=WHITE,
            command=self._download_selected,
        ).pack(side="right")

    # ------------------------------------------------------------------
    # Log panel
    # ------------------------------------------------------------------

    def _toggle_log(self) -> None:
        self._log_visible = not self._log_visible
        if self._log_visible:
            self._log_frame.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 4))
            self._log_btn.configure(text="▼ Log")
        else:
            self._log_frame.grid_remove()
            self._log_btn.configure(text="▶ Log")

    def _clear_log(self) -> None:
        self._log_box.configure(state="normal")
        self._log_box.delete("0.0", "end")
        self._log_box.configure(state="disabled")

    def _append_log(self, line: str) -> None:
        self._log_box.configure(state="normal")
        self._log_box.insert("end", line + "\n")
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _on_log(self, line: str) -> None:
        # Called from worker threads — only touch the thread-safe queue.
        self._events.put(("log", line))

    def _process_events(self) -> None:
        """Drain worker events on the main thread (the only thread allowed to
        touch tkinter widgets). Reschedules itself for the life of the panel."""
        try:
            for _ in range(500):  # cap per tick so the UI stays responsive
                event = self._events.get_nowait()
                kind = event[0]
                if kind == "log":
                    self._append_log(event[1])
                elif kind == "progress":
                    self._handle_progress(event[1], event[2])
                elif kind == "populate":
                    self._populate(event[1])
                elif kind == "playlists":
                    self._on_playlists_loaded(event[1], event[2])
                elif kind == "fetch_error":
                    self._on_fetch_error(event[1])
        except queue.Empty:
            pass
        self.after(100, self._process_events)

    # ------------------------------------------------------------------
    # Download progress / status bar
    # ------------------------------------------------------------------

    def _begin_batch(self, total: int) -> None:
        self._dl_total = total
        self._dl_done = 0
        self._dl_failed = 0
        self._dl_active.clear()
        self._progress_bar.set(0)
        self._status_bar_lbl.configure(
            text=f"Starting {total} download{'s' if total != 1 else ''}…",
            text_color=INFO,
        )

    def _handle_progress(self, track: Track, status: DownloadStatus) -> None:
        row = self._rows.get(track.id)
        if row is not None:
            row.refresh_status()

        if status == DownloadStatus.DOWNLOADING:
            self._dl_active[track.id] = track.display_name
        elif status in (DownloadStatus.COMPLETE, DownloadStatus.FAILED):
            self._dl_active.pop(track.id, None)
            self._dl_done += 1
            if status == DownloadStatus.FAILED:
                self._dl_failed += 1

        self._refresh_status_bar()

    def _refresh_status_bar(self) -> None:
        total = self._dl_total
        if total == 0:
            return

        self._progress_bar.set(self._dl_done / total)

        if self._dl_done >= total:
            ok = total - self._dl_failed
            msg = f"✓ Finished — {ok} downloaded"
            if self._dl_failed:
                msg += f", {self._dl_failed} failed"
            self._status_bar_lbl.configure(
                text=msg, text_color=ERROR if self._dl_failed else SUCCESS,
            )
            return

        active = list(self._dl_active.values())
        if active:
            current = active[0]
            extra = f"   (+{len(active) - 1} more)" if len(active) > 1 else ""
        else:
            current = "preparing…"
            extra = ""
        self._status_bar_lbl.configure(
            text=f"↓  {self._dl_done}/{total}   •   {current}{extra}",
            text_color=INFO,
        )

    # ------------------------------------------------------------------
    # Library loading
    # ------------------------------------------------------------------

    def _source_key(self) -> str:
        return "spotify" if self._source_var.get() == "Spotify" else "soundcloud"

    def _on_source_change(self, _: str) -> None:
        self._content_var.set("Liked Songs")
        self._clear()
        self._playlists = []
        self._scraper = None
        self._status_lbl.configure(text="")
        self._playlist_menu.pack_forget()
        self._load_btn.configure(text="Load Library")

    def _load_library(self) -> None:
        # In playlist mode, once playlists are loaded the reload button
        # re-fetches the currently selected playlist's tracks.
        if self._content_var.get() == "Playlists" and self._playlists and self._playlist_var.get():
            self._on_playlist_select(self._playlist_var.get())
            return
        self._load_btn.configure(state="disabled", text="Loading…")
        self._status_lbl.configure(text="Fetching…", text_color=TEXT_SECONDARY)
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self) -> None:
        try:
            source = self._source_var.get()
            try:
                frags = int(self._config.concurrent_fragments)
            except (ValueError, TypeError):
                frags = 4
            if source == "Spotify":
                scraper = SpotifyScraper(
                    self._config.spotify_client_id,
                    self._config.spotify_client_secret,
                    concurrent_fragments=frags,
                )
                self._manager.configure_spotify(scraper)
            else:
                scraper = SoundCloudScraper(
                    auth_token=self._config.soundcloud_auth_token,
                    username=self._config.soundcloud_username,
                    concurrent_fragments=frags,
                )
                self._manager.configure_soundcloud(scraper)

            if self._content_var.get() == "Playlists":
                playlists = scraper.fetch_playlists()
                self._events.put(("playlists", playlists, scraper))
            else:
                tracks = scraper.fetch_library()
                self._events.put(("populate", tracks))
        except Exception as exc:
            self._events.put(("fetch_error", str(exc)))

    def _on_fetch_error(self, msg: str) -> None:
        self._load_btn.configure(state="normal", text="Load Library")
        self._status_lbl.configure(text=f"Error: {msg}", text_color=ERROR)

    def _on_content_change(self, value: str) -> None:
        self._clear()
        self._playlists = []
        self._scraper = None
        self._status_lbl.configure(text="")
        self._playlist_menu.pack_forget()
        self._load_btn.configure(text="Load Library")

    def _on_playlists_loaded(self, playlists: list[dict], scraper) -> None:
        self._playlists = playlists
        self._scraper = scraper
        if not playlists:
            self._load_btn.configure(state="normal", text="Load Library")
            self._status_lbl.configure(text="No playlists found", text_color=ERROR)
            return
        names = [p["name"] for p in playlists]
        self._playlist_menu.configure(values=names)
        self._playlist_var.set(names[0])
        self._playlist_menu.pack(side="left", padx=(12, 0))
        self._on_playlist_select(names[0])

    def _on_playlist_select(self, name: str) -> None:
        pl = next((p for p in self._playlists if p["name"] == name), None)
        if pl is None or self._scraper is None:
            return
        self._load_btn.configure(state="disabled", text="Loading…")
        self._status_lbl.configure(text=f"Fetching '{name}'…", text_color=TEXT_SECONDARY)
        pid = pl["id"]
        threading.Thread(target=self._fetch_playlist_tracks, args=(pid,), daemon=True).start()

    def _fetch_playlist_tracks(self, playlist_id: str) -> None:
        try:
            tracks = self._scraper.fetch_playlist_tracks(playlist_id)
            self._events.put(("populate", tracks))
        except Exception as exc:
            self._events.put(("fetch_error", str(exc)))

    def _mark_downloaded(self, tracks: list[Track]) -> int:
        """Flag tracks already on disk (index or matching file) as COMPLETE so
        they're skipped on download and hidden from the list. Returns the count.

        The download directory is scanned exactly once into a normalised-stem
        dict so the overall complexity is O(N+M) rather than O(N×M) — avoids
        freezing when N (tracks) and M (existing files) are both large.
        """
        source = self._source_key()

        # One-shot directory scan: normalised stem → absolute path.
        dir_map: dict[str, str] = {}
        directory = Path(self._config.download_path)
        if directory.is_dir():
            for entry in directory.iterdir():
                if entry.is_file() and not entry.name.startswith("."):
                    dir_map[_norm(entry.stem)] = str(entry)

        count = 0
        for t in tracks:
            already = self._index.contains(source, t.id)
            if not already:
                wanted = _norm(f"{t.artist} - {t.title}")
                title_only = _norm(t.title)
                match = dir_map.get(wanted)
                if match is None and len(title_only) > 4:
                    match = next(
                        (p for stem, p in dir_map.items() if stem.endswith(title_only)),
                        None,
                    )
                if match:
                    t.local_path = match
                    self._index.record(source, t)
                    already = True
            if already:
                t.status = DownloadStatus.COMPLETE
                count += 1
        return count

    def _populate(self, tracks: list[Track]) -> None:
        self._clear()
        self._tracks = tracks
        self._mark_downloaded(tracks)
        # Bump the generation counter so any in-progress chunk render from a
        # previous load stops without touching the now-cleared widget dict.
        self._populate_gen += 1
        total = len(tracks)
        if total == 0:
            self._load_btn.configure(state="normal", text="Reload")
            self._refresh_visibility()
            return
        self._load_btn.configure(state="disabled", text="Loading…")
        self._status_lbl.configure(
            text=f"Rendering {total} tracks…", text_color=TEXT_SECONDARY,
        )
        # Yield immediately so the "Rendering…" label paints before we start.
        self.after(0, self._populate_chunk, tracks, 0, self._populate_gen)

    def _populate_chunk(self, tracks: list[Track], offset: int, gen: int) -> None:
        """Create _RENDER_BATCH rows then yield via after(0) to keep the UI live."""
        if gen != self._populate_gen:
            return  # a newer load has started; discard this render pass
        end = min(offset + _RENDER_BATCH, len(tracks))
        for t in tracks[offset:end]:
            row = TrackRow(self._scroll, t, on_toggle=self._on_toggle)
            row.pre_downloaded = t.status == DownloadStatus.COMPLETE
            self._bind_mousewheel(row)
            self._rows[t.id] = row
        if end < len(tracks):
            self._status_lbl.configure(
                text=f"Loading… {end}/{len(tracks)}", text_color=TEXT_SECONDARY,
            )
            self.after(0, self._populate_chunk, tracks, end, gen)
        else:
            self._load_btn.configure(state="normal", text="Reload")
            self._refresh_visibility()

    def _clear(self) -> None:
        for w in self._scroll.winfo_children():
            w.destroy()
        self._rows.clear()
        self._selected.clear()
        self._tracks = []

    # ------------------------------------------------------------------
    # Selection / visibility
    # ------------------------------------------------------------------

    def _on_toggle(self, track: Track, selected: bool) -> None:
        if selected:
            self._selected.add(track.id)
        else:
            self._selected.discard(track.id)

    def _select_all(self) -> None:
        # Only act on currently-visible rows so hidden/downloaded tracks aren't
        # silently selected.
        for tid, row in self._rows.items():
            if row.winfo_ismapped():
                row.set_selected(True)
                self._selected.add(tid)

    def _deselect_all(self) -> None:
        for row in self._rows.values():
            row.set_selected(False)
        self._selected.clear()

    def _refresh_visibility(self, *_) -> None:
        """Single ordered pass: pack visible rows (in track order) and forget the
        rest. Visibility = matches search AND (not hidden as already-downloaded).
        """
        q = self._search_var.get().lower().strip()
        show_dl = self._show_dl_var.get()
        hidden_dl = 0
        for row in self._rows.values():
            t = row.track
            if row.pre_downloaded and not show_dl:
                row.pack_forget()
                hidden_dl += 1
                continue
            match = not q or q in t.title.lower() or q in t.artist.lower()
            if match:
                row.pack(fill="x", pady=2)
            else:
                row.pack_forget()

        total = len(self._rows)
        msg = f"{total} track{'s' if total != 1 else ''}"
        if hidden_dl:
            msg += f"  ·  {hidden_dl} downloaded (hidden)"
        self._status_lbl.configure(text=msg, text_color=TEXT_SECONDARY)

    # ------------------------------------------------------------------
    # Downloads
    # ------------------------------------------------------------------

    def _make_jobs(self, tracks: list[Track]) -> list[DownloadJob]:
        source = self._source_key()
        return [
            DownloadJob(
                track=t,
                source=source,
                output_dir=self._config.download_path,
                quality=self._config.audio_quality,
                fmt=self._config.output_format,
                on_progress=self._on_progress,
                on_log=self._on_log,
            )
            for t in tracks
            if t.status != DownloadStatus.COMPLETE
        ]

    def _start_download(self, tracks: list[Track]) -> None:
        jobs = self._make_jobs(tracks)
        if not jobs:
            messagebox.showinfo("Sub-Scraper", "Those tracks are already downloaded.")
            return
        self._maybe_configure_gdrive()
        self._begin_batch(len(jobs))
        self._manager.submit_batch(jobs)

    def _download_selected(self) -> None:
        tracks = [self._rows[tid].track for tid in self._selected if tid in self._rows]
        if not tracks:
            messagebox.showinfo("Sub-Scraper", "No tracks selected.")
            return
        self._start_download(tracks)

    def _download_all(self) -> None:
        if not self._tracks:
            messagebox.showinfo("Sub-Scraper", "Library is empty — load it first.")
            return
        self._start_download(self._tracks)

    def _maybe_configure_gdrive(self) -> None:
        if self._config.use_gdrive and self._config.gdrive_credentials_path:
            from ..uploaders.gdrive import GDriveUploader
            self._manager.configure_gdrive(
                GDriveUploader(self._config.gdrive_credentials_path, self._config.gdrive_folder_id)
            )

    def _on_progress(self, track: Track) -> None:
        # Called from worker threads — only touch the thread-safe queue.
        # Snapshot the status now; the track object keeps mutating.
        self._events.put(("progress", track, track.status))


# ---------------------------------------------------------------------------
# Settings panel
# ---------------------------------------------------------------------------

class SettingsPanel(ctk.CTkScrollableFrame):
    def __init__(self, master, config: Config, index: DownloadIndex) -> None:
        super().__init__(master, fg_color="transparent")
        self._config = config
        self._index = index
        self._build()

    def _section(self, title: str) -> None:
        ctk.CTkLabel(self, text=title, font=FONT_SECTION, text_color=TEXT_PRIMARY, anchor="w").pack(
            fill="x", padx=16, pady=(20, 2)
        )
        ctk.CTkFrame(self, height=2, fg_color=ORANGE).pack(fill="x", padx=16, pady=(0, 8))

    def _field(self, label: str, attr: str, show: str = "", browse: bool = False, browse_file: bool = False) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=3)
        ctk.CTkLabel(row, text=label, width=210, anchor="w", font=FONT_MEDIUM, text_color=TEXT_PRIMARY).pack(
            side="left"
        )
        var = tk.StringVar(value=str(getattr(self._config, attr, "")))
        var.trace_add("write", lambda *_: setattr(self._config, attr, var.get()))
        ctk.CTkEntry(row, textvariable=var, show=show, width=280).pack(side="left")
        if browse or browse_file:
            def _pick(v=var):
                path = filedialog.askopenfilename() if browse_file else filedialog.askdirectory()
                if path:
                    v.set(path)
            ctk.CTkButton(row, text="Browse", width=72, command=_pick).pack(side="left", padx=8)

    def _dropdown(self, label: str, attr: str, choices: list[str]) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=3)
        ctk.CTkLabel(row, text=label, width=210, anchor="w", font=FONT_MEDIUM, text_color=TEXT_PRIMARY).pack(
            side="left"
        )
        var = tk.StringVar(value=str(getattr(self._config, attr, choices[0])))
        ctk.CTkOptionMenu(row, values=choices, variable=var,
                          command=lambda v: setattr(self._config, attr, v)).pack(side="left")

    def _checkbox(self, label: str, attr: str) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=3)
        var = tk.BooleanVar(value=bool(getattr(self._config, attr, False)))
        ctk.CTkCheckBox(
            row, text=label, variable=var, font=FONT_MEDIUM, text_color=TEXT_PRIMARY,
            command=lambda: setattr(self._config, attr, var.get()),
        ).pack(side="left")

    def _build(self) -> None:
        self._section("Spotify")
        self._field("Client ID", "spotify_client_id")
        self._field("Client Secret", "spotify_client_secret", show="*")

        self._section("SoundCloud")
        self._field("Username (public likes)", "soundcloud_username")
        self._field("Auth Token (optional)", "soundcloud_auth_token", show="*")

        self._section("Output")
        self._field("Download Path", "download_path", browse=True)
        self._dropdown("Format", "output_format", ["mp3", "flac", "m4a", "opus", "ogg"])
        self._dropdown("Quality", "audio_quality", ["128k", "192k", "256k", "320k"])

        self._section("Library")
        self._checkbox("Hide tracks I've already downloaded", "hide_downloaded")
        clear_row = ctk.CTkFrame(self, fg_color="transparent")
        clear_row.pack(fill="x", padx=16, pady=3)
        ctk.CTkLabel(
            clear_row, text="Forget download history (re-shows every track)",
            width=210, anchor="w", font=FONT_SMALL, text_color=TEXT_SECONDARY,
        ).pack(side="left")
        ctk.CTkButton(clear_row, text="Clear History", width=120, command=self._clear_history).pack(side="left")

        self._section("Performance & Resilience")
        self._field("Max Concurrent Downloads", "max_concurrent")
        self._field("Parallel Fragments / Track", "concurrent_fragments")
        self._field("Retry Limit", "retry_limit")
        self._field("Circuit-Breaker Threshold", "breaker_threshold")
        self._field("Circuit-Breaker Cooldown (s)", "breaker_cooldown")
        self._field("Request Timeout (s)", "request_timeout")
        self._checkbox("Verify downloads (size + checksum)", "verify_downloads")

        self._section("Google Drive")
        self._checkbox("Enable Google Drive Sync", "use_gdrive")
        self._field("credentials.json Path", "gdrive_credentials_path", browse_file=True)
        self._field("Folder ID (optional)", "gdrive_folder_id")

        ctk.CTkButton(
            self, text="Save Settings", fg_color=HIGHLIGHT, hover_color=HIGHLIGHT_HOVER,
            text_color=WHITE, command=self._save,
        ).pack(pady=20)

    def _clear_history(self) -> None:
        count = self._index.clear()
        messagebox.showinfo("Sub-Scraper", f"Cleared {count} item(s) from download history.")

    def _save(self) -> None:
        int_fields = (
            ("max_concurrent", 6), ("chunk_size", 50), ("concurrent_fragments", 4),
            ("retry_limit", 3), ("breaker_threshold", 6), ("io_chunk_bytes", 1 << 17),
        )
        float_fields = (
            ("breaker_cooldown", 30.0), ("request_timeout", 30.0),
            ("retry_base_delay", 1.0), ("retry_max_delay", 30.0),
        )
        for attr, default in int_fields:
            try:
                setattr(self._config, attr, int(getattr(self._config, attr)))
            except (ValueError, TypeError):
                setattr(self._config, attr, default)
        for attr, default in float_fields:
            try:
                setattr(self._config, attr, float(getattr(self._config, attr)))
            except (ValueError, TypeError):
                setattr(self._config, attr, default)
        self._config.save()
        messagebox.showinfo(
            "Sub-Scraper",
            "Settings saved. Restart the app for concurrency changes to take effect.",
        )


# ---------------------------------------------------------------------------
# Root application
# ---------------------------------------------------------------------------

class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Sub-Scraper")
        self.geometry("1120x720")
        self.minsize(940, 600)
        self.configure(fg_color=DARK_BG)

        self._config = Config.load()
        # Structured logs go to stderr; INFO+ also surface in the GUI log panel
        # (wired once the Library panel exists).
        configure_logging()

        self._index = DownloadIndex()
        self._manager = DownloadManager.from_config(self._config)
        self._manager.configure_index(self._index)
        self._manager.start()

        self._panels: dict[str, ctk.CTkFrame] = {}
        self._nav_btns: dict[str, ctk.CTkButton] = {}

        set_window_icon(self)
        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        sidebar = ctk.CTkFrame(self, width=210, fg_color=SIDEBAR_BG, corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        # --- Brand lockup: logo badge + wordmark + accent rule -----------
        brand = ctk.CTkFrame(sidebar, fg_color="transparent")
        brand.pack(pady=(28, 30))
        self._logo_img = get_ctk_image(54)
        if self._logo_img is not None:
            ctk.CTkLabel(brand, image=self._logo_img, text="").pack()
        ctk.CTkLabel(brand, text="Sub-Scraper", font=FONT_BRAND, text_color=WHITE).pack(pady=(10, 0))
        ctk.CTkFrame(brand, height=3, width=50, fg_color=ORANGE, corner_radius=2).pack(pady=(8, 0))

        for name in ("Library", "Settings"):
            btn = ctk.CTkButton(
                sidebar, text=name, anchor="w", width=178, height=40,
                fg_color="transparent", hover_color=NAVY_LIGHT,
                font=FONT_MEDIUM, text_color=TEXT_ON_NAVY,
                command=lambda n=name: self._show(n),
            )
            btn.pack(pady=3, padx=16)
            self._nav_btns[name] = btn

        ctk.CTkLabel(
            sidebar, text="v2.0  ·  async engine", font=FONT_SMALL,
            text_color=TEXT_ON_NAVY_MUTED,
        ).pack(side="bottom", pady=16)

        content = ctk.CTkFrame(self, fg_color=DARK_BG, corner_radius=0)
        content.pack(side="left", fill="both", expand=True)

        self._panels["Library"] = LibraryPanel(content, self._config, self._manager, self._index)
        self._panels["Settings"] = SettingsPanel(content, self._config, self._index)

        # Now that the Library panel (and its thread-safe log sink) exists, route
        # structured INFO logs into the in-app Download Log too.
        configure_logging(gui_sink=self._panels["Library"]._on_log)

        if self._needs_setup():
            self._panels["Setup"] = SetupWizard(content, self._config, on_complete=self._finish_setup)
            self._show("Setup")
        else:
            self._show("Library")

    def _needs_setup(self) -> bool:
        return not (self._config.spotify_client_id or self._config.soundcloud_username)

    def _finish_setup(self) -> None:
        self._panels.pop("Setup", None)
        self._show("Library")

    def _show(self, name: str) -> None:
        for p in self._panels.values():
            p.pack_forget()
        self._panels[name].pack(fill="both", expand=True)
        for n, btn in self._nav_btns.items():
            btn.configure(fg_color=NAVY_LIGHT if n == name else "transparent")

    def _on_close(self) -> None:
        self._manager.stop()
        self.destroy()
