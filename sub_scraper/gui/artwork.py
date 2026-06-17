"""Asynchronous, cached track-artwork loader for the library view.

Downloading and decoding cover images must never touch the UI thread (we just
removed a 1000-track freeze), so this loader does all network + Pillow work on a
small bounded thread pool and hands finished images back via a callback. The
caller is responsible for marshalling that callback onto the main thread (tkinter
is not thread-safe) and for building the actual ``CTkImage`` there.

Caching is two-tier:
  * in-memory de-dup of *in-flight* URLs, so the same album art is fetched once
    even when dozens of tracks share it;
  * on-disk thumbnails under ``~/.sub_scraper/artwork`` so restarts are instant
    and we never re-hit the CDN for art we've already seen.

Everything is guarded: any failure (no Pillow, dead URL, decode error) simply
yields no image and the row keeps its placeholder — artwork is decorative and
must never break the app.
"""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Optional

_CACHE_DIR = Path.home() / ".sub_scraper" / "artwork"

# Palette (kept in sync with gui/styles.py) for the placeholder disc.
_CARD_ALT = (228, 234, 242, 255)   # #e4eaf2
_NAVY = (11, 37, 69, 255)          # #0b2545
_ORANGE = (255, 122, 24, 255)      # #ff7a18


class ArtworkLoader:
    """Fetches + decodes cover art off the UI thread, with on-disk caching."""

    def __init__(self, *, store_size: int = 96, workers: int = 6) -> None:
        # We store/decode at 2x the display size so thumbnails stay crisp on
        # HiDPI screens (the Steam Deck included).
        self._store = store_size
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="art")
        self._inflight: set[str] = set()
        self._lock = threading.Lock()
        self._closed = False
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    # ------------------------------------------------------------------

    def request(self, url: str, on_ready: Callable[[str, object], None]) -> None:
        """Fetch+decode ``url`` off-thread; call ``on_ready(url, pil_image)`` on a
        worker thread when done. No-op if the URL is already being fetched."""
        if not url:
            return
        with self._lock:
            if self._closed or url in self._inflight:
                return
            self._inflight.add(url)
        try:
            self._pool.submit(self._work, url, on_ready)
        except RuntimeError:  # pool already shut down
            with self._lock:
                self._inflight.discard(url)

    def close(self) -> None:
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------------

    def _work(self, url: str, on_ready: Callable[[str, object], None]) -> None:
        try:
            img = self._load_pil(url)
            if img is not None and not self._closed:
                on_ready(url, img)
        except Exception:  # noqa: BLE001 - artwork is never fatal
            pass
        finally:
            with self._lock:
                self._inflight.discard(url)

    def _disk_path(self, url: str) -> Path:
        h = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return _CACHE_DIR / f"{h}.png"

    def _load_pil(self, url: str):
        from PIL import Image

        path = self._disk_path(url)
        if path.exists():
            try:
                return Image.open(path).convert("RGBA")
            except Exception:  # noqa: BLE001 - corrupt cache entry, refetch
                pass

        data = self._download(url)
        if not data:
            return None

        from io import BytesIO
        try:
            img = Image.open(BytesIO(data)).convert("RGBA")
        except Exception:  # noqa: BLE001
            return None

        img = _fit_square(img, self._store)
        try:
            img.save(path, "PNG")
        except OSError:
            pass
        return img

    @staticmethod
    def _download(url: str) -> Optional[bytes]:
        import urllib.request

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                return resp.read()
        except Exception:  # noqa: BLE001 - network/SSL/etc; just no art
            return None


# ---------------------------------------------------------------------------
# Pillow helpers (run on worker threads)
# ---------------------------------------------------------------------------

def _fit_square(img, size: int):
    """Centre-crop to a square, resize to ``size``, and round the corners so the
    thumbnail matches the rounded track rows."""
    from PIL import Image, ImageDraw

    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)

    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=int(size * 0.18), fill=255,
    )
    img.putalpha(mask)
    return img


def make_placeholder(size: int = 96):
    """A small vinyl-disc placeholder (PIL image), drawn at 4x then downscaled.

    Returns ``None`` if Pillow is unavailable so the caller can skip artwork
    entirely without crashing."""
    try:
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001
        return None

    S = size * 4
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=S * 0.18, fill=_CARD_ALT)

    cx = cy = S / 2
    r = S * 0.34
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=_NAVY)               # disc
    rr = S * 0.23
    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
              outline=(255, 255, 255, 45), width=int(S * 0.013))          # groove
    lr = S * 0.11
    d.ellipse([cx - lr, cy - lr, cx + lr, cy + lr], fill=_ORANGE)         # label
    hr = S * 0.025
    d.ellipse([cx - hr, cy - hr, cx + hr, cy + hr], fill=_CARD_ALT)       # spindle hole

    return img.resize((size, size), Image.LANCZOS)
