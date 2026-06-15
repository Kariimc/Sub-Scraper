"""Sub-Scraper visual identity — a clean, professional blue / orange / white theme.

A light workspace (soft blue-white surfaces, white cards) keeps the library
readable, a deep-navy sidebar anchors the brand, blue is the default action
colour, and orange is reserved for the primary call-to-action so the eye always
knows where to click.
"""

# --- Brand ----------------------------------------------------------------
BLUE = "#1565c0"
BLUE_HOVER = "#0d47a1"
NAVY = "#0b2545"          # sidebar / brand band
NAVY_LIGHT = "#15325c"    # active nav item / subtle accents on navy
ORANGE = "#ff7a18"        # primary call-to-action
ORANGE_HOVER = "#e96a0a"
WHITE = "#ffffff"

# --- Surfaces -------------------------------------------------------------
SURFACE = "#eef2f8"       # window / content background
CARD = "#ffffff"          # panels, rows, inputs
CARD_ALT = "#f4f7fc"      # list backdrop / subtle zones
BORDER = "#d7e0ee"

# --- Text -----------------------------------------------------------------
TEXT = "#16233a"          # primary text on light surfaces
TEXT_MUTED = "#5d6b85"    # secondary text on light surfaces
TEXT_ON_NAVY = "#eaf1fb"  # text on the navy sidebar
TEXT_ON_NAVY_MUTED = "#9fb3d4"

# --- Status ---------------------------------------------------------------
SUCCESS = "#1f9d57"
ERROR = "#e5484d"
WARNING = "#ff8a00"
INFO = BLUE

# --- Backwards-compatible / semantic aliases used across the GUI ----------
DARK_BG = SURFACE
SIDEBAR_BG = NAVY
PANEL_BG = CARD
ACCENT = NAVY_LIGHT
HIGHLIGHT = ORANGE            # primary call-to-action
HIGHLIGHT_HOVER = ORANGE_HOVER
TEXT_PRIMARY = TEXT
TEXT_SECONDARY = TEXT_MUTED

# --- Typography -----------------------------------------------------------
FONT_BRAND = ("Segoe UI Semibold", 22, "bold")
FONT_TITLE = ("Segoe UI Semibold", 20, "bold")
FONT_SECTION = ("Segoe UI Semibold", 15, "bold")
FONT_MEDIUM = ("Segoe UI", 13)
FONT_SMALL = ("Segoe UI", 11)
FONT_MONO = ("Cascadia Mono", 11)
