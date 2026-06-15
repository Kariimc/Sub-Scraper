from __future__ import annotations

import tkinter as tk
import webbrowser
from tkinter import filedialog
from typing import Callable

import customtkinter as ctk

from ..core.config import Config
from .logo import get_ctk_image
from .styles import (
    BLUE, BLUE_HOVER, BORDER, FONT_BRAND, FONT_MEDIUM, FONT_SMALL, FONT_TITLE,
    HIGHLIGHT, HIGHLIGHT_HOVER, TEXT_PRIMARY, TEXT_SECONDARY, WHITE,
)

_STEPS = ["Spotify", "SoundCloud", "Output", "Ready"]


class SetupWizard(ctk.CTkFrame):
    def __init__(self, master, config: Config, on_complete: Callable[[], None]) -> None:
        super().__init__(master, fg_color="transparent")
        self._config = config
        self._on_complete = on_complete
        self._step = 0

        # Brand lockup at the top of onboarding.
        brand = ctk.CTkFrame(self, fg_color="transparent")
        brand.pack(pady=(36, 0))
        self._logo_img = get_ctk_image(48)
        if self._logo_img is not None:
            ctk.CTkLabel(brand, image=self._logo_img, text="").pack(side="left", padx=(0, 12))
        ctk.CTkLabel(brand, text="Sub-Scraper", font=FONT_BRAND, text_color=TEXT_PRIMARY).pack(side="left")

        # Progress bar area
        self._prog_frame = ctk.CTkFrame(self, fg_color="transparent")
        self._prog_frame.pack(fill="x", padx=60, pady=(20, 0))

        # Content area
        self._content = ctk.CTkFrame(self, fg_color="transparent")
        self._content.pack(fill="both", expand=True, padx=60, pady=24)

        # Nav buttons
        nav = ctk.CTkFrame(self, fg_color="transparent")
        nav.pack(fill="x", padx=60, pady=(0, 40))

        self._back_btn = ctk.CTkButton(
            nav, text="← Back", width=110,
            fg_color="transparent", border_width=1, border_color=BORDER,
            text_color=TEXT_PRIMARY, hover_color=BORDER,
            command=self._prev,
        )
        self._back_btn.pack(side="left")

        self._next_btn = ctk.CTkButton(
            nav, text="Next →", width=130,
            fg_color=HIGHLIGHT, hover_color=HIGHLIGHT_HOVER, text_color=WHITE,
            command=self._next,
        )
        self._next_btn.pack(side="right")

        self._render()

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _render(self) -> None:
        self._render_progress()
        for w in self._content.winfo_children():
            w.destroy()

        self._back_btn.configure(state="normal" if self._step > 0 else "disabled")
        is_last = self._step == len(_STEPS) - 1
        self._next_btn.configure(text="Launch →" if is_last else "Next →")

        getattr(self, f"_step_{self._step}")()

    def _render_progress(self) -> None:
        for w in self._prog_frame.winfo_children():
            w.destroy()
        for i, label in enumerate(_STEPS):
            color = HIGHLIGHT if i == self._step else (TEXT_PRIMARY if i < self._step else TEXT_SECONDARY)
            ctk.CTkLabel(
                self._prog_frame, text=f"{'●' if i <= self._step else '○'}  {label}",
                font=FONT_SMALL, text_color=color,
            ).pack(side="left", padx=16)

    def _prev(self) -> None:
        if self._step > 0:
            self._step -= 1
            self._render()

    def _next(self) -> None:
        if self._step == len(_STEPS) - 1:
            self._config.save()
            self._on_complete()
        else:
            self._step += 1
            self._render()

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _heading(self, title: str, sub: str) -> None:
        ctk.CTkLabel(
            self._content, text=title,
            font=FONT_TITLE, text_color=HIGHLIGHT, anchor="w",
        ).pack(fill="x", pady=(0, 4))
        ctk.CTkLabel(
            self._content, text=sub,
            font=FONT_SMALL, text_color=TEXT_SECONDARY, anchor="w",
        ).pack(fill="x", pady=(0, 20))

    def _note(self, text: str) -> None:
        ctk.CTkLabel(
            self._content, text=text,
            font=FONT_MEDIUM, text_color=TEXT_PRIMARY,
            anchor="w", wraplength=580, justify="left",
        ).pack(fill="x", pady=(0, 12))

    def _small_note(self, text: str) -> None:
        ctk.CTkLabel(
            self._content, text=text,
            font=FONT_SMALL, text_color=TEXT_SECONDARY,
            anchor="w", wraplength=580, justify="left",
        ).pack(fill="x", pady=(0, 8))

    def _field(self, label: str, attr: str, show: str = "") -> None:
        ctk.CTkLabel(
            self._content, text=label,
            font=FONT_MEDIUM, text_color=TEXT_PRIMARY, anchor="w",
        ).pack(fill="x", pady=(10, 2))
        var = tk.StringVar(value=str(getattr(self._config, attr, "")))
        var.trace_add("write", lambda *_: setattr(self._config, attr, var.get()))
        ctk.CTkEntry(self._content, textvariable=var, show=show, width=500, height=36).pack(anchor="w")

    def _link_btn(self, label: str, url: str) -> None:
        ctk.CTkButton(
            self._content, text=label, width=240, height=32,
            fg_color=BLUE, hover_color=BLUE_HOVER, text_color=WHITE,
            command=lambda: webbrowser.open(url),
        ).pack(anchor="w", pady=(4, 16))

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    def _step_0(self) -> None:
        self._heading("Step 1 — Spotify", "Create a free Spotify developer app (2 minutes)")
        self._note(
            "1. Click the button below — it opens the Spotify Developer Dashboard in your browser.\n"
            "2. Log in with your Spotify account.\n"
            "3. Click  Create App  and give it any name you like.\n"
            "4. Under  Redirect URIs  add exactly:  http://127.0.0.1:8888/callback\n"
            "5. Copy the Client ID and Client Secret here."
        )
        self._link_btn("Open Spotify Developer Dashboard →", "https://developer.spotify.com/dashboard")
        self._field("Client ID", "spotify_client_id")
        self._field("Client Secret (hidden)", "spotify_client_secret", show="*")

    def _step_1(self) -> None:
        self._heading("Step 2 — SoundCloud", "Enter your SoundCloud username")
        self._note(
            "Your username is the part after  soundcloud.com/  in your profile URL.\n\n"
            "Example:  soundcloud.com/john-doe  →  type  john-doe"
        )
        self._link_btn("Open SoundCloud Profile →", "https://soundcloud.com/you")
        self._field("SoundCloud Username", "soundcloud_username")
        self._small_note(
            "Your likes need to be set to Public in SoundCloud settings.\n"
            "If they are private, you can add an Auth Token below (optional)."
        )
        self._field("Auth Token — optional", "soundcloud_auth_token", show="*")

    def _step_2(self) -> None:
        self._heading("Step 3 — Output", "Where should your music be saved?")

        ctk.CTkLabel(
            self._content, text="Download Folder",
            font=FONT_MEDIUM, text_color=TEXT_PRIMARY, anchor="w",
        ).pack(fill="x", pady=(0, 2))

        path_row = ctk.CTkFrame(self._content, fg_color="transparent")
        path_row.pack(fill="x", pady=(0, 20))

        path_var = tk.StringVar(value=self._config.download_path)
        path_var.trace_add("write", lambda *_: setattr(self._config, "download_path", path_var.get()))
        ctk.CTkEntry(path_row, textvariable=path_var, width=400, height=36).pack(side="left")
        ctk.CTkButton(
            path_row, text="Browse", width=80,
            command=lambda: path_var.set(filedialog.askdirectory() or path_var.get()),
        ).pack(side="left", padx=8)

        opts = ctk.CTkFrame(self._content, fg_color="transparent")
        opts.pack(fill="x")

        ctk.CTkLabel(opts, text="Format", font=FONT_MEDIUM, text_color=TEXT_PRIMARY).pack(side="left")
        fmt_var = tk.StringVar(value=self._config.output_format)
        ctk.CTkOptionMenu(
            opts, values=["mp3", "flac", "m4a", "opus", "ogg"],
            variable=fmt_var,
            command=lambda v: setattr(self._config, "output_format", v),
        ).pack(side="left", padx=(8, 28))

        ctk.CTkLabel(opts, text="Quality", font=FONT_MEDIUM, text_color=TEXT_PRIMARY).pack(side="left")
        qual_var = tk.StringVar(value=self._config.audio_quality)
        ctk.CTkOptionMenu(
            opts, values=["128k", "192k", "256k", "320k"],
            variable=qual_var,
            command=lambda v: setattr(self._config, "audio_quality", v),
        ).pack(side="left", padx=8)

    def _step_3(self) -> None:
        self._heading("You're all set!", "Sub-Scraper is ready to use")
        self._note(
            "When you click  Launch →  the app will open.\n\n"
            "The first time you load your Spotify library, a browser window will open\n"
            "asking you to log in — that only happens once, then it's cached."
        )
        ctk.CTkLabel(
            self._content, text="Saving to:",
            font=FONT_SMALL, text_color=TEXT_SECONDARY, anchor="w",
        ).pack(fill="x")
        ctk.CTkLabel(
            self._content, text=self._config.download_path,
            font=FONT_MEDIUM, text_color=TEXT_PRIMARY, anchor="w",
        ).pack(fill="x", pady=(2, 0))
