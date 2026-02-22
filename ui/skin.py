"""
Skin/theme loader for PagerAmp.

Loads JSON skin files and provides color/layout lookups.
Colors stored as RGB565 for direct use with pagerctl.
"""

import os
import json


def _rgb_to_565(r, g, b):
    """Convert 8-bit RGB to RGB565."""
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def _hex_to_565(hex_color):
    """Convert '#RRGGBB' or '0xRRGGBB' to RGB565."""
    if isinstance(hex_color, int):
        r = (hex_color >> 16) & 0xFF
        g = (hex_color >> 8) & 0xFF
        b = hex_color & 0xFF
        return _rgb_to_565(r, g, b)
    s = hex_color.lstrip("#").lstrip("0x")
    if len(s) == 6:
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
        return _rgb_to_565(r, g, b)
    return 0x0000


# Default element-to-color mapping
DEFAULT_ELEMENTS = {
    "bg": "#000000",
    "text": "#FFFFFF",
    "text_dim": "#808080",
    "title_bar_bg": "#000080",
    "title_bar_text": "#FFFFFF",
    "time": "#00FF00",
    "progress_bg": "#333333",
    "progress_fill": "#00FF00",
    "progress_knob": "#FFFFFF",
    "volume_bg": "#333333",
    "volume_fill": "#00FF00",
    "transport": "#CCCCCC",
    "transport_active": "#00FF00",
    "track_highlight": "#000080",
    "track_highlight_text": "#FFFFFF",
    "track_text": "#CCCCCC",
    "track_number": "#808080",
    "shuffle_on": "#00FF00",
    "shuffle_off": "#555555",
    "repeat_on": "#00FF00",
    "repeat_off": "#555555",
    "menu_bg": "#111111",
    "menu_selected": "#00FF00",
    "menu_text": "#CCCCCC",
    "separator": "#333333",
    "accent": "#00FF00",
    "warning": "#FF0000",
    "info": "#00AAFF",
}


class Skin:
    """Theme/skin with color and font lookups."""

    def __init__(self, skin_data=None, skins_dir=None):
        self.name = "Default"
        self.style = "classic"  # classic, modern, retro
        self.bg_path = None
        self.bg_has_buttons = False
        self.font_sizes = {
            "title": 14,
            "track": 16,
            "time": 24,
            "label": 10,
            "menu": 14,
            "status": 12,
            "browser": 12,
        }
        self._colors = {}
        self._raw = {}

        if skin_data:
            self._load(skin_data, skins_dir)
        else:
            self._load_defaults()

    def _load_defaults(self):
        for name, hex_val in DEFAULT_ELEMENTS.items():
            self._colors[name] = _hex_to_565(hex_val)

    def _load(self, data, skins_dir=None):
        self.name = data.get("name", "Custom")
        self.style = data.get("style", "classic")
        self._raw = data

        # Load colors with fallback to defaults
        colors = data.get("colors", {})
        for name, default_hex in DEFAULT_ELEMENTS.items():
            if name in colors:
                self._colors[name] = _hex_to_565(colors[name])
            else:
                self._colors[name] = _hex_to_565(default_hex)

        # Load font sizes with fallback
        fonts = data.get("fonts", {})
        for name, default_size in self.font_sizes.items():
            if name in fonts:
                self.font_sizes[name] = fonts[name]

        # Load background image path
        bg = data.get("background")
        if bg and skins_dir:
            path = os.path.join(skins_dir, bg)
            if os.path.isfile(path):
                self.bg_path = path
        self.bg_has_buttons = data.get("bg_has_buttons", False)

    def color(self, name):
        """Get RGB565 color by element name."""
        return self._colors.get(name, 0xFFFF)

    def font(self, element):
        """Get font size for an element."""
        return self.font_sizes.get(element, 12)


class SkinManager:
    """Loads and manages available skins."""

    def __init__(self, skins_dir=None):
        if skins_dir is None:
            skins_dir = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "skins")
        self.skins_dir = skins_dir
        self.skins = {}  # name → Skin
        self.skin_names = []
        self.current_index = 0
        self._load_all()

    def _load_all(self):
        """Load all .json skin files from skins directory."""
        if not os.path.isdir(self.skins_dir):
            self.skins["Default"] = Skin()
            self.skin_names = ["Default"]
            return

        for name in sorted(os.listdir(self.skins_dir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.skins_dir, name)
            try:
                with open(path, "r") as f:
                    data = json.load(f)
                skin = Skin(data, skins_dir=self.skins_dir)
                self.skins[skin.name] = skin
                self.skin_names.append(skin.name)
            except (IOError, json.JSONDecodeError, ValueError):
                continue

        if not self.skins:
            self.skins["Default"] = Skin()
            self.skin_names = ["Default"]

    @property
    def current(self):
        """Get the currently active skin."""
        if self.current_index < len(self.skin_names):
            return self.skins[self.skin_names[self.current_index]]
        return Skin()

    def next_skin(self):
        """Cycle to next skin, returns new skin."""
        self.current_index = (self.current_index + 1) % len(self.skin_names)
        return self.current

    def prev_skin(self):
        """Cycle to previous skin."""
        self.current_index = (self.current_index - 1) % len(self.skin_names)
        return self.current

    def set_skin(self, name):
        """Set skin by name."""
        if name in self.skins:
            self.current_index = self.skin_names.index(name)
            return self.current
        return None

    @property
    def current_name(self):
        if self.current_index < len(self.skin_names):
            return self.skin_names[self.current_index]
        return "Default"
