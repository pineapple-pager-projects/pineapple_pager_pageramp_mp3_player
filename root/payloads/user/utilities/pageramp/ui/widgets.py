"""
PagerAmp UI widgets — drawing primitives for the Pager display.

All widgets draw using pagerctl Pager object methods.
Display is 480x222 RGB565 landscape.
"""

import time
import os

# Font path (resolved at import)
FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "fonts")
FONT_PATH = os.path.join(FONT_DIR, "DejaVuSansMono.ttf")


class ScrollText:
    """Horizontally scrolling text for long titles."""

    def __init__(self, x, y, max_width, font_size=16, speed=2):
        self.x = x
        self.y = y
        self.max_width = max_width
        self.font_size = font_size
        self.speed = speed  # pixels per frame
        self.text = ""
        self.text_width = 0
        self.offset = 0
        self.pause_frames = 0
        self.PAUSE_AT_START = 30  # frames to pause at start
        self.PAUSE_AT_END = 20
        self._needs_scroll = False
        self._last_text = None

    def set_text(self, text, pager):
        """Update text and recalculate width."""
        if text == self._last_text:
            return
        self._last_text = text
        self.text = text
        self.offset = 0
        self.pause_frames = self.PAUSE_AT_START
        self.text_width = pager.ttf_width(text, FONT_PATH, self.font_size)
        self._needs_scroll = self.text_width > self.max_width

    def update(self):
        """Advance scroll animation."""
        if not self._needs_scroll:
            return

        if self.pause_frames > 0:
            self.pause_frames -= 1
            return

        self.offset += self.speed
        gap = 60  # pixel gap before text repeats
        if self.offset >= self.text_width + gap:
            self.offset = 0
            self.pause_frames = self.PAUSE_AT_START

    def _fit_text(self, pager, text, max_px):
        """Truncate text to fit within max_px pixels."""
        if pager.ttf_width(text, FONT_PATH, self.font_size) <= max_px:
            return text
        # Binary search for the longest substring that fits
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if pager.ttf_width(text[:mid], FONT_PATH, self.font_size) <= max_px:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo]

    def _draw_clipped(self, pager, text, tx, color):
        """Draw text at tx, clipped to [self.x, self.x + max_width]."""
        right_edge = self.x + self.max_width
        # Off-screen entirely
        if tx >= right_edge or tx + self.text_width <= self.x:
            return
        # Left clipping: skip leading characters that are off-screen
        draw_text = text
        draw_x = tx
        if tx < self.x:
            # Find how many pixels are off the left edge
            skip_px = self.x - tx
            # Find how many characters to skip
            lo, hi = 0, len(text)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if pager.ttf_width(text[:mid], FONT_PATH,
                                   self.font_size) <= skip_px:
                    lo = mid
                else:
                    hi = mid - 1
            draw_text = text[lo:]
            draw_x = self.x
        # Right clipping: truncate to fit within remaining width
        avail = right_edge - draw_x
        draw_text = self._fit_text(pager, draw_text, avail)
        if draw_text:
            pager.draw_ttf(draw_x, self.y, draw_text, color,
                          FONT_PATH, self.font_size)

    def draw(self, pager, color):
        """Draw the text, clipped to the title area."""
        if not self.text:
            return

        if not self._needs_scroll:
            # Static text — truncate to fit
            clipped = self._fit_text(pager, self.text, self.max_width)
            pager.draw_ttf(self.x, self.y, clipped, color,
                          FONT_PATH, self.font_size)
            return

        # Scrolling text with proper clipping
        gap = 60
        total = self.text_width + gap

        # First copy
        tx = self.x - self.offset
        self._draw_clipped(pager, self.text, tx, color)

        # Second copy (wrapping)
        tx2 = tx + total
        self._draw_clipped(pager, self.text, tx2, color)


class ProgressBar:
    """Track progress bar with elapsed/remaining time."""

    def __init__(self, x, y, width, height=6):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.position = 0  # 0.0 - 1.0
        self.elapsed = 0   # seconds
        self.duration = 0  # seconds

    def set_progress(self, position, duration):
        self.elapsed = position
        self.duration = duration
        if duration > 0:
            self.position = min(1.0, position / duration)
        else:
            self.position = 0

    def draw(self, pager, bg_color, fill_color, knob_color=None,
             time_color=None, font_size=10):
        """Draw progress bar with optional time labels."""
        # Background
        pager.fill_rect(self.x, self.y, self.width, self.height, bg_color)

        # Fill
        fill_w = int(self.width * self.position)
        if fill_w > 0:
            pager.fill_rect(self.x, self.y, fill_w, self.height, fill_color)

        # Knob
        if knob_color is not None and fill_w > 0:
            kx = self.x + fill_w - 2
            pager.fill_rect(kx, self.y - 1, 4, self.height + 2, knob_color)

        # Time labels
        if time_color is not None:
            elapsed_str = _format_time(self.elapsed)
            remain = self.duration - self.elapsed
            if remain < 0:
                remain = 0
            remain_str = "-" + _format_time(remain)

            ty = self.y + self.height + 2
            pager.draw_ttf(self.x, ty, elapsed_str, time_color,
                          FONT_PATH, font_size)
            rw = pager.ttf_width(remain_str, FONT_PATH, font_size)
            pager.draw_ttf(self.x + self.width - rw, ty, remain_str,
                          time_color, FONT_PATH, font_size)


class VolumeBar:
    """Volume indicator bar."""

    def __init__(self, x, y, width, height=4):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.level = 80  # 0-100

    def draw(self, pager, bg_color, fill_color, label_color=None,
             font_size=10):
        # Background
        pager.fill_rect(self.x, self.y, self.width, self.height, bg_color)

        # Fill
        fill_w = int(self.width * self.level / 100)
        if fill_w > 0:
            pager.fill_rect(self.x, self.y, fill_w, self.height, fill_color)

        # Label
        if label_color is not None:
            label = "VOL:%d" % self.level
            pager.draw_ttf(self.x + self.width + 4, self.y - 2, label,
                          label_color, FONT_PATH, font_size)


class TrackList:
    """Scrollable track list with highlighted current track."""

    def __init__(self, x, y, width, height, font_size=12, line_height=None):
        self.x = x
        self.y = y
        self.width = width
        self.height = height
        self.font_size = font_size
        self.line_height = line_height or (font_size + 6)
        self.tracks = []          # list of display names
        self.selected = 0         # highlighted index
        self.current_playing = -1  # currently playing index
        self.scroll_offset = 0    # first visible index
        self.visible_count = height // self.line_height

    def set_tracks(self, names):
        if names == self.tracks:
            return
        self.tracks = names
        self.selected = max(0, min(self.selected, len(names) - 1))
        if self.scroll_offset > self.selected:
            self.scroll_offset = self.selected

    def navigate(self, delta):
        """Move selection by delta (+1/-1). Returns new index."""
        if not self.tracks:
            return 0
        self.selected = max(0, min(len(self.tracks) - 1,
                                    self.selected + delta))
        # Scroll to keep selected visible
        if self.selected < self.scroll_offset:
            self.scroll_offset = self.selected
        elif self.selected >= self.scroll_offset + self.visible_count:
            self.scroll_offset = self.selected - self.visible_count + 1
        return self.selected

    def page_up(self):
        self.navigate(-self.visible_count)

    def page_down(self):
        self.navigate(self.visible_count)

    def draw(self, pager, text_color, highlight_bg, highlight_text,
             number_color, playing_color=None):
        """Draw the visible portion of the track list."""
        for i in range(self.visible_count):
            idx = self.scroll_offset + i
            if idx >= len(self.tracks):
                break

            ty = self.y + i * self.line_height
            is_selected = (idx == self.selected)
            is_playing = (idx == self.current_playing)

            # Highlight background for selected
            if is_selected:
                pager.fill_rect(self.x, ty, self.width,
                               self.line_height - 1, highlight_bg)

            # Track number
            num_str = "%2d." % (idx + 1)
            nc = highlight_text if is_selected else number_color
            pager.draw_ttf(self.x + 2, ty + 1, num_str, nc,
                          FONT_PATH, self.font_size)

            # Track name
            name = self.tracks[idx]
            # Truncate if too long
            max_name_w = self.width - 40
            while pager.ttf_width(name, FONT_PATH, self.font_size) > max_name_w and len(name) > 1:
                name = name[:-1]

            if is_selected:
                tc = highlight_text
            elif is_playing and playing_color:
                tc = playing_color
            else:
                tc = text_color

            pager.draw_ttf(self.x + 30, ty + 1, name, tc,
                          FONT_PATH, self.font_size)

            # Playing indicator
            if is_playing:
                marker = ">"
                mc = playing_color or highlight_text
                pager.draw_ttf(self.x + self.width - 14, ty + 1, marker, mc,
                              FONT_PATH, self.font_size)


class TransportIcons:
    """Transport control icons drawn with fill_rect/line primitives."""

    def __init__(self, x, y, size=16, spacing=8):
        self.x = x
        self.y = y
        self.size = size
        self.spacing = spacing
        self.active = "stop"  # play, pause, stop
        self.selected = 1     # 0=prev, 1=play, 2=pause, 3=stop, 4=next

    BUTTONS = ["prev", "play", "pause", "stop", "next"]

    def select_left(self):
        self.selected = max(0, self.selected - 1)

    def select_right(self):
        self.selected = min(len(self.BUTTONS) - 1, self.selected + 1)

    @property
    def selected_name(self):
        return self.BUTTONS[self.selected]

    def _draw_prev(self, pager, x, color):
        """Draw |<< icon."""
        s = self.size
        # Vertical bar
        pager.fill_rect(x, self.y, 2, s, color)
        # Triangle pointing left
        for i in range(s // 2):
            pager.fill_rect(x + s - i - 1, self.y + s // 2 - i,
                           1, i * 2 + 1, color)

    def _draw_play(self, pager, x, color):
        """Draw > (play triangle)."""
        s = self.size
        for i in range(s):
            h = s - abs(s // 2 - i) * 2
            if h > 0:
                pager.fill_rect(x + abs(s // 2 - i), self.y + i, 1, 1, color)
        # Simplified: just draw a right-pointing triangle
        half = s // 2
        for i in range(half):
            pager.fill_rect(x + i, self.y + half - i,
                           1, i * 2 + 1, color)

    def _draw_pause(self, pager, x, color):
        """Draw || (two bars)."""
        s = self.size
        bar_w = max(s // 4, 2)
        gap = max(s // 4, 2)
        pager.fill_rect(x, self.y, bar_w, s, color)
        pager.fill_rect(x + bar_w + gap, self.y, bar_w, s, color)

    def _draw_stop(self, pager, x, color):
        """Draw [] (square)."""
        s = self.size
        pager.fill_rect(x, self.y, s, s, color)

    def _draw_next(self, pager, x, color):
        """Draw >>| icon."""
        s = self.size
        half = s // 2
        # Triangle pointing right
        for i in range(half):
            pager.fill_rect(x + i, self.y + half - i,
                           1, i * 2 + 1, color)
        # Vertical bar
        pager.fill_rect(x + s - 2, self.y, 2, s, color)

    def draw(self, pager, color, active_color, selected_color=None,
             bg_has_buttons=False):
        """Draw all transport icons with optional selection highlight."""
        s = self.size
        sp = self.spacing
        x = self.x
        draw_fns = [self._draw_prev, self._draw_play, self._draw_pause,
                     self._draw_stop, self._draw_next]

        for i, (name, fn) in enumerate(zip(self.BUTTONS, draw_fns)):
            is_selected = selected_color is not None and i == self.selected
            if bg_has_buttons:
                # Sprite buttons in background — active sprite swap
                # handles highlighting, no outlines needed
                pass
            else:
                if is_selected:
                    pager.fill_rect(x - 3, self.y - 3, s + 6, s + 6,
                                   selected_color)
                    fn(pager, x, 0xFFFF)
                elif name == self.active:
                    fn(pager, x, active_color)
                else:
                    fn(pager, x, color)
            x += s + sp

    @property
    def total_width(self):
        return self.size * 5 + self.spacing * 4


class TimeDisplay:
    """Large MM:SS time readout."""

    def __init__(self, x, y, font_size=28):
        self.x = x
        self.y = y
        self.font_size = font_size
        self.seconds = 0

    def draw(self, pager, color):
        text = _format_time(self.seconds)
        pager.draw_ttf(self.x, self.y, text, color,
                      FONT_PATH, self.font_size)


class StatusIndicator:
    """Small text indicator for shuffle/repeat mode."""

    def __init__(self, x, y, font_size=10):
        self.x = x
        self.y = y
        self.font_size = font_size

    def draw(self, pager, label, on, on_color, off_color):
        color = on_color if on else off_color
        pager.draw_ttf(self.x, self.y, label, color,
                      FONT_PATH, self.font_size)


def _format_time(seconds):
    """Format seconds as M:SS or H:MM:SS."""
    if seconds < 0:
        seconds = 0
    seconds = int(seconds)
    if seconds >= 3600:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return "%d:%02d:%02d" % (h, m, s)
    m = seconds // 60
    s = seconds % 60
    return "%d:%02d" % (m, s)
