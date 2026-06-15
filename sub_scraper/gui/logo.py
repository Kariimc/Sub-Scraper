"""The Sub-Scraper brand mark, rendered programmatically with Pillow.

The logo is an app-icon-style rounded badge with a blue->orange gradient,
carrying a white audio-equalizer that flows down into a download arrow — i.e.
"download this audio". It is drawn at 4x supersample and downscaled with LANCZOS
so it stays razor-sharp at any size, with no binary asset to ship or get stale.

Everything is guarded: if Pillow is unavailable the GUI falls back to a text
wordmark, so the app never fails to launch over a missing logo.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# Brand colours (kept in sync with gui/styles.py).
_BLUE = (21, 101, 192)     # #1565c0
_ORANGE = (255, 122, 24)   # #ff7a18
_WHITE = (255, 255, 255)

_SS = 4  # supersampling factor


def _lerp(a: tuple, b: tuple, f: float) -> tuple:
    return tuple(int(round(a[i] + (b[i] - a[i]) * f)) for i in range(3))


def _draw_mark(draw, S: int, fill) -> None:
    """Draw the white equalizer-into-arrow mark on a canvas of side ``S``."""
    cx = S / 2

    # --- Equalizer bars (the "audio") ---------------------------------
    heights = [0.16, 0.24, 0.34, 0.22, 0.15]
    bar_w = S * 0.072
    gap = S * 0.040
    n = len(heights)
    total_w = n * bar_w + (n - 1) * gap
    x = cx - total_w / 2
    base_y = S * 0.46
    for h in heights:
        bh = S * h
        draw.rounded_rectangle(
            [x, base_y - bh, x + bar_w, base_y],
            radius=bar_w / 2, fill=fill,
        )
        x += bar_w + gap

    # --- Download arrow (the "download"): a rectangular shaft that
    #     overlaps a wide arrowhead, so the two read as a single glyph.
    shaft_w = S * 0.085
    shaft_top = S * 0.50
    shaft_bot = S * 0.64
    draw.rounded_rectangle(
        [cx - shaft_w / 2, shaft_top, cx + shaft_w / 2, shaft_bot],
        radius=shaft_w * 0.30, fill=fill,
    )
    head_w = S * 0.28
    head_top = S * 0.62  # slight overlap with the shaft
    head_bot = S * 0.80
    draw.polygon(
        [(cx - head_w / 2, head_top), (cx + head_w / 2, head_top), (cx, head_bot)],
        fill=fill,
    )


@lru_cache(maxsize=8)
def make_logo_image(size: int = 256):
    """Return a crisp RGBA :class:`PIL.Image.Image` of the logo badge."""
    from PIL import Image, ImageDraw, ImageFilter

    S = size * _SS

    # Vertical blue->orange gradient, built as a 1px column then stretched.
    column = Image.new("RGB", (1, S))
    col_px = column.load()
    for y in range(S):
        col_px[0, y] = _lerp(_BLUE, _ORANGE, y / max(1, S - 1))
    gradient = column.resize((S, S))

    # Rounded-square mask -> the app-icon badge silhouette.
    mask = Image.new("L", (S, S), 0)
    md = ImageDraw.Draw(mask)
    margin = S * 0.05
    md.rounded_rectangle(
        [margin, margin, S - margin, S - margin],
        radius=S * 0.225, fill=255,
    )
    badge = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    badge.paste(gradient, (0, 0), mask)

    # Soft drop shadow beneath the white mark for depth.
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    _draw_mark(ImageDraw.Draw(shadow), S, (0, 20, 50, 110))
    shadow = shadow.filter(ImageFilter.GaussianBlur(S * 0.012))
    shadow = _offset(shadow, 0, int(S * 0.012))

    # The white foreground mark.
    mark = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    _draw_mark(ImageDraw.Draw(mark), S, _WHITE + (255,))

    composed = Image.alpha_composite(badge, shadow)
    composed = Image.alpha_composite(composed, mark)
    return composed.resize((size, size), Image.LANCZOS)


def _offset(img, dx: int, dy: int):
    from PIL import Image
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.paste(img, (dx, dy))
    return out


def save_logo(path: str | Path, size: int = 256) -> Path:
    """Render and write the logo PNG to ``path``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    make_logo_image(size).save(path)
    return path


def get_ctk_image(size: int = 40):
    """Return a ``customtkinter.CTkImage`` of the logo, or ``None`` if rendering
    is unavailable (missing Pillow / customtkinter)."""
    try:
        import customtkinter as ctk
        img = make_logo_image(size)
        return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
    except Exception:  # noqa: BLE001 - logo is decorative, never fatal
        return None


def set_window_icon(window, size: int = 64) -> None:
    """Best-effort: set the OS window/taskbar icon to the logo."""
    try:
        from PIL import ImageTk
        photo = ImageTk.PhotoImage(make_logo_image(size))
        window.iconphoto(True, photo)
        # Keep a reference so Tk doesn't garbage-collect the image.
        window._logo_icon = photo  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


if __name__ == "__main__":  # render brand assets on demand
    import sys
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("assets/logo.png")
    save_logo(target, 512)
    save_logo(target.with_name("icon.png"), 128)
    print(f"wrote {target} and {target.with_name('icon.png')}")
