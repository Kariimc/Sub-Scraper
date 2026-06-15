from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Optional

import customtkinter as ctk

from ..core.config import Config
from ..core.download_manager import DownloadJob, DownloadManager
from .wizard import SetupWizard
from ..scrapers.base import DownloadStatus, Track
from ..scrapers.soundcloud import SoundCloudScraper
from ..scrapers.spotify import SpotifyScraper
from .styles import (
    ACCENT, DARK_BG, ERROR, FONT_MEDIUM, FONT_SECTION,
    FONT_SMALL, FONT_TITLE, HIGHLIGHT, HIGHLIGHT_HOVER,
    INFO, PANEL_BG, SIDEBAR_BG, SUCCESS, TEXT_PRIMARY, TEXT_SECONDARY,
)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

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
        super().__init__(master, fg_color=PANEL_BG, corner_radius=6)
        self.track = track
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
        # Keep the checkbox enabled in every state so the user can always
        # deselect or re-queue a track. Completed tracks are skipped at
        # download time (_make_jobs), so locking the box was unnecessary.
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
    def __init__(self, master, config: Config, manager: DownloadManager) -> None:
        super().__init__(master, fg_color="transparent")
        self.rowconfigure(1, weight=1)
        self.columnconfigure(0, weight=1)

        self._config = config
        self._manager = manager
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
        self._search_var.trace_add("write", self._filter_rows)
        ctk.CTkEntry(top, textvariable=self._search_var, placeholder_text="Search...", width=200).pack(
            side="right", padx=8
        )

        self._status_lbl = ctk.CTkLabel(top, text="", font=FONT_SMALL, text_color=TEXT_SECONDARY)
        self._status_lbl.pack(side="left", padx=16)

        # Content-mode row: liked songs vs playlist picker
        content_row = ctk.CTkFrame(hdr, fg_color="transparent")
        content_row.pack(fill="x", pady=(6, 0))

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
        self._log_frame = ctk.CTkFrame(self, fg_color=PANEL_BG, corner_radius=6)

        log_hdr = ctk.CTkFrame(self._log_frame, fg_color="transparent")
        log_hdr.pack(fill="x", padx=8, pady=(6, 2))
        ctk.CTkLabel(log_hdr, text="Download Log", font=FONT_SMALL, text_color=TEXT_SECONDARY).pack(side="left")
        ctk.CTkButton(
            log_hdr, text="Clear", width=56, height=24, font=FONT_SMALL,
            command=self._clear_log,
        ).pack(side="right")

        self._log_box = ctk.CTkTextbox(
            self._log_frame, height=140, font=("Consolas", 11),
            fg_color=DARK_BG, text_color=TEXT_SECONDARY,
        )
        self._log_box.pack(fill="x", padx=8, pady=(0, 8))
        self._log_box.configure(state="disabled")

    def _build_status_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=PANEL_BG, corner_radius=6)
        bar.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 4))
        bar.columnconfigure(0, weight=1)

        self._status_bar_lbl = ctk.CTkLabel(
            bar, text="Ready — no downloads yet", anchor="w",
            font=FONT_SMALL, text_color=TEXT_SECONDARY,
        )
        self._status_bar_lbl.grid(row=0, column=0, sticky="ew", padx=10, pady=(6, 2))

        self._progress_bar = ctk.CTkProgressBar(bar, height=8)
        self._progress_bar.set(0)
        self._progress_bar.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

    def _build_footer(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.grid(row=4, column=0, sticky="ew", padx=16, pady=(4, 16))

        ctk.CTkButton(bar, text="Select All", width=100, command=self._select_all).pack(side="left", padx=(0, 6))
        ctk.CTkButton(bar, text="Deselect All", width=110, command=self._deselect_all).pack(side="left")

        ctk.CTkLabel(bar, text="Chunk:", font=FONT_SMALL, text_color=TEXT_SECONDARY).pack(
            side="left", padx=(16, 4)
        )
        self._chunk_var = tk.StringVar(value=str(self._config.chunk_size))
        ctk.CTkEntry(bar, textvariable=self._chunk_var, width=64).pack(side="left")

        self._log_btn = ctk.CTkButton(
            bar, text="▶ Log", width=70, font=FONT_SMALL,
            fg_color="transparent", hover_color=ACCENT, text_color=TEXT_SECONDARY,
            command=self._toggle_log,
        )
        self._log_btn.pack(side="left", padx=(14, 0))

        ctk.CTkButton(bar, text="Download All", width=120, command=self._download_all).pack(
            side="right", padx=(8, 0)
        )
        ctk.CTkButton(
            bar, text="Download Selected", width=150,
            fg_color=HIGHLIGHT, hover_color=HIGHLIGHT_HOVER,
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
        # re-fetches the currently selected playlist's tracks instead of
        # re-fetching the playlist list.
        if self._content_var.get() == "Playlists" and self._playlists and self._playlist_var.get():
            self._on_playlist_select(self._playlist_var.get())
            return
        self._load_btn.configure(state="disabled", text="Loading…")
        self._status_lbl.configure(text="Fetching…", text_color=TEXT_SECONDARY)
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self) -> None:
        try:
            source = self._source_var.get()
            if source == "Spotify":
                scraper = SpotifyScraper(
                    self._config.spotify_client_id,
                    self._config.spotify_client_secret,
                )
                self._manager.configure_spotify(scraper)
            else:
                scraper = SoundCloudScraper(
                    auth_token=self._config.soundcloud_auth_token,
                    username=self._config.soundcloud_username,
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

    def _populate(self, tracks: list[Track]) -> None:
        self._clear()
        self._tracks = tracks
        for t in tracks:
            row = TrackRow(self._scroll, t, on_toggle=self._on_toggle)
            row.pack(fill="x", pady=2)
            self._bind_mousewheel(row)
            self._rows[t.id] = row
        self._load_btn.configure(state="normal", text="Reload")
        self._status_lbl.configure(text=f"{len(tracks)} tracks", text_color=TEXT_SECONDARY)

    def _clear(self) -> None:
        for w in self._scroll.winfo_children():
            w.destroy()
        self._rows.clear()
        self._selected.clear()
        self._tracks = []

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _on_toggle(self, track: Track, selected: bool) -> None:
        if selected:
            self._selected.add(track.id)
        else:
            self._selected.discard(track.id)

    def _select_all(self) -> None:
        for tid, row in self._rows.items():
            row.set_selected(True)
            self._selected.add(tid)

    def _deselect_all(self) -> None:
        for row in self._rows.values():
            row.set_selected(False)
        self._selected.clear()

    def _filter_rows(self, *_) -> None:
        q = self._search_var.get().lower()
        for row in self._rows.values():
            t = row.track
            match = not q or q in t.title.lower() or q in t.artist.lower()
            if match:
                row.pack(fill="x", pady=2)
            else:
                row.pack_forget()

    # ------------------------------------------------------------------
    # Downloads
    # ------------------------------------------------------------------

    def _chunk_size(self) -> int:
        try:
            return int(self._chunk_var.get())
        except ValueError:
            return 0

    def _make_jobs(self, tracks: list[Track]) -> list[DownloadJob]:
        source = "spotify" if self._source_var.get() == "Spotify" else "soundcloud"
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
        self._manager.submit_batch(jobs, chunk_size=self._chunk_size())

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
    def __init__(self, master, config: Config) -> None:
        super().__init__(master, fg_color="transparent")
        self._config = config
        self._build()

    def _section(self, title: str) -> None:
        ctk.CTkLabel(self, text=title, font=FONT_SECTION, text_color=TEXT_PRIMARY, anchor="w").pack(
            fill="x", padx=16, pady=(20, 2)
        )
        ctk.CTkFrame(self, height=1, fg_color=ACCENT).pack(fill="x", padx=16, pady=(0, 8))

    def _field(self, label: str, attr: str, show: str = "", browse: bool = False, browse_file: bool = False) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=3)
        ctk.CTkLabel(row, text=label, width=190, anchor="w", font=FONT_MEDIUM, text_color=TEXT_PRIMARY).pack(
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
        ctk.CTkLabel(row, text=label, width=190, anchor="w", font=FONT_MEDIUM, text_color=TEXT_PRIMARY).pack(
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
        self._field("Max Concurrent Downloads", "max_concurrent")
        self._field("Default Chunk Size", "chunk_size")

        self._section("Google Drive")
        self._checkbox("Enable Google Drive Sync", "use_gdrive")
        self._field("credentials.json Path", "gdrive_credentials_path", browse_file=True)
        self._field("Folder ID (optional)", "gdrive_folder_id")

        ctk.CTkButton(
            self, text="Save Settings", fg_color=HIGHLIGHT, hover_color=HIGHLIGHT_HOVER,
            command=self._save,
        ).pack(pady=20)

    def _save(self) -> None:
        try:
            self._config.max_concurrent = int(self._config.max_concurrent)
        except (ValueError, TypeError):
            self._config.max_concurrent = 4
        try:
            self._config.chunk_size = int(self._config.chunk_size)
        except (ValueError, TypeError):
            self._config.chunk_size = 50
        self._config.save()
        messagebox.showinfo("Sub-Scraper", "Settings saved.")


# ---------------------------------------------------------------------------
# Root application
# ---------------------------------------------------------------------------

class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Sub-Scraper")
        self.geometry("1120x720")
        self.minsize(900, 580)
        self.configure(fg_color=DARK_BG)

        self._config = Config.load()
        self._manager = DownloadManager(max_workers=int(self._config.max_concurrent))
        self._manager.start()

        self._panels: dict[str, ctk.CTkFrame] = {}
        self._nav_btns: dict[str, ctk.CTkButton] = {}

        self._build()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build(self) -> None:
        sidebar = ctk.CTkFrame(self, width=200, fg_color=SIDEBAR_BG, corner_radius=0)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        ctk.CTkLabel(sidebar, text="Sub-Scraper", font=FONT_TITLE, text_color=HIGHLIGHT).pack(pady=(28, 36))

        for name in ("Library", "Settings"):
            btn = ctk.CTkButton(
                sidebar, text=name, anchor="w", width=168,
                fg_color="transparent", hover_color=ACCENT,
                font=FONT_MEDIUM, text_color=TEXT_PRIMARY,
                command=lambda n=name: self._show(n),
            )
            btn.pack(pady=3, padx=16)
            self._nav_btns[name] = btn

        content = ctk.CTkFrame(self, fg_color=DARK_BG, corner_radius=0)
        content.pack(side="left", fill="both", expand=True)

        self._panels["Library"] = LibraryPanel(content, self._config, self._manager)
        self._panels["Settings"] = SettingsPanel(content, self._config)

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
            btn.configure(fg_color=ACCENT if n == name else "transparent")

    def _on_close(self) -> None:
        self._manager.stop()
        self.destroy()
