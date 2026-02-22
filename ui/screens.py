"""
PagerAmp screen classes.

Each screen has:
  - handle_input(button, event_type) → optional screen transition string
  - update(status) → update state from daemon status
  - draw(pager, skin) → render to display
"""

import os
import time

from ui.widgets import (ScrollText, ProgressBar, VolumeBar, TrackList,
                        TransportIcons, TimeDisplay, FONT_PATH,
                        _format_time)

# Screen dimensions (landscape 270)
SCREEN_W = 480
SCREEN_H = 222


class StartScreen:
    """Start menu screen — shown on app launch."""

    MENU_ITEMS = [
        ("Connect Bluetooth", "bluetooth"),
        ("Start Player", "now_playing"),
        ("Theme", "cycle_theme"),
        ("Settings", "settings"),
        ("Exit", "exit"),
    ]

    def __init__(self, skin_manager, settings):
        self.skin_manager = skin_manager
        self.settings = settings
        self.selected = 1  # default to "Start Player"
        self.font_size = 14
        self.line_height = 24

    def handle_input(self, button, event_type, pager):
        BTN_A = 0x10
        BTN_B = 0x20
        BTN_UP = 0x01
        BTN_DOWN = 0x02
        BTN_LEFT = 0x04
        BTN_RIGHT = 0x08

        if event_type != 1:
            return None

        if button == BTN_UP:
            self.selected = (self.selected - 1) % len(self.MENU_ITEMS)
        elif button == BTN_DOWN:
            self.selected = (self.selected + 1) % len(self.MENU_ITEMS)
        elif button == BTN_A:
            _, action = self.MENU_ITEMS[self.selected]
            if action == "cycle_theme":
                self.skin_manager.next_skin()
                self.settings["theme"] = self.skin_manager.current_name
            else:
                return action
        elif button == BTN_LEFT:
            if self.MENU_ITEMS[self.selected][1] == "cycle_theme":
                self.skin_manager.prev_skin()
                self.settings["theme"] = self.skin_manager.current_name
        elif button == BTN_RIGHT:
            if self.MENU_ITEMS[self.selected][1] == "cycle_theme":
                self.skin_manager.next_skin()
                self.settings["theme"] = self.skin_manager.current_name
        elif button == BTN_B:
            return "exit"

        return None

    def update(self, status):
        pass

    def draw(self, pager, skin):
        c = skin.color
        pager.clear(c("bg"))

        if skin.style == "classic":
            # Winamp-style metallic title bar
            pager.fill_rect(0, 0, SCREEN_W, 22, c("title_bar_bg"))
            pager.draw_ttf(6, 2, "PagerAmp v1.0", c("title_bar_text"),
                          FONT_PATH, skin.font("title"))
            pager.hline(0, 22, SCREEN_W, c("separator"))
            # Beveled edges
            pager.hline(0, 0, SCREEN_W, c("separator"))
            pager.draw_ttf_centered(32, "PagerAmp", c("accent"),
                                    FONT_PATH, 24)
            sub_y = 60

        elif skin.style == "retro":
            # Retro double-line border frame
            pager.rect(2, 2, SCREEN_W - 4, SCREEN_H - 4, c("accent"))
            pager.rect(5, 5, SCREEN_W - 10, SCREEN_H - 10, c("text_dim"))
            pager.draw_ttf_centered(16, "PAGERAMP", c("accent"),
                                    FONT_PATH, 26)
            sub_y = 46

        else:
            # Modern — clean with accent line
            pager.draw_ttf_centered(16, "PagerAmp", c("accent"),
                                    FONT_PATH, 28)
            line_w = 120
            pager.hline((SCREEN_W - line_w) // 2, 48, line_w, c("accent"))
            sub_y = 54

        pager.draw_ttf_centered(sub_y, "Winamp for Pager", c("text_dim"),
                                FONT_PATH, 11)

        # BT status
        bt_name = self.settings.get("bt_device_name", "")
        if bt_name:
            pager.draw_ttf_centered(sub_y + 16, "BT: " + bt_name,
                                    c("info"), FONT_PATH, 10)

        # Menu items
        y = 88
        for i, (label, action) in enumerate(self.MENU_ITEMS):
            is_sel = (i == self.selected)

            if action == "cycle_theme":
                name = self.skin_manager.current_name
                if is_sel:
                    display = "Theme: < " + name + " >"
                else:
                    display = "Theme: " + name
            else:
                display = label

            if is_sel:
                tw = pager.ttf_width(display, FONT_PATH, self.font_size)
                hx = (SCREEN_W - tw) // 2 - 8
                pager.fill_rect(hx, y, tw + 16, self.line_height - 2,
                               c("track_highlight"))

            tc = c("menu_selected") if is_sel else c("menu_text")
            pager.draw_ttf_centered(y + 4, display, tc,
                                    FONT_PATH, self.font_size)

            y += self.line_height

        # Bottom hints
        pager.draw_ttf(8, SCREEN_H - 14, "[A] Select  [B] Exit",
                      c("text_dim"), FONT_PATH, 10)


class NowPlayingScreen:
    """Main playback screen — Winamp-inspired now playing view."""

    # Focus row constants
    FOCUS_TRANSPORT = 0  # transport buttons + shuffle + repeat
    FOCUS_SEEK = 1       # seek/progress bar
    FOCUS_VOLUME = 2     # volume bar
    FOCUS_BALANCE = 3    # balance bar

    # Extended: 0-4 TransportIcons, 5=eject, 6=shuffle, 7=repeat
    _BTN_COUNT = 8

    def __init__(self, client, playlist):
        self.client = client
        self.playlist = playlist

        # Widgets — positioned by layout()
        self.title_scroll = ScrollText(0, 0, 0, speed=2)
        self.time_display = TimeDisplay(0, 0, 28)
        self.progress = ProgressBar(0, 0, 0, 6)
        self.volume = VolumeBar(0, 0, 0, 4)
        self.transport = TransportIcons(0, 0, 14, 10)

        # Balance (0-100, 50=center)
        self._balance = 50

        # Navigation state
        self._focus = self.FOCUS_TRANSPORT
        self._btn_index = 1  # default to play

        # Shuffle/repeat sprite hit-boxes (x, y, w, h)
        self._shuffle_box = (0, 0, 0, 0)
        self._repeat_box = (0, 0, 0, 0)

        # Classic groove positions for dynamic knobs
        # (x_start, x_end, y) — knob drawn within this range
        self._vol_groove = (190, 294, 112)   # orange groove (up 5)
        self._bal_groove = (311, 370, 112)   # green groove (up 5)

        # Active button sprite handles (indexed by _btn_index 0-7)
        self._active_handles = [None] * self._BTN_COUNT
        self._active_loaded = False

        # Active sprite info: (filename, x, y) matching gen_skins.py
        self._active_sprite_info = [
            ("previous-active.png", 17, 176),
            ("play-active.png",     59, 176),
            ("pause-active.png",   101, 176),
            ("stop-active.png",    143, 176),
            ("next-active.png",    185, 176),
            ("eject-active.png",   229, 178),
            ("shuffle-active.png", 279, 178),
            ("repeat-active.png",  369, 178),
        ]

        # Toggle sprite handles: shuffle/repeat ON state overlays
        self._toggle_handles = {}  # filename → image handle
        self._toggle_loaded = False
        self._toggle_sprite_info = [
            ("shuffle-toggled.png",        279, 178),
            ("shuffle-active-toggled.png", 279, 178),
            ("repeat-toggled.png",         369, 178),
            ("repeat-active-toggled.png",  369, 178),
        ]

        self._layout_done = False
        self._layout_style = None
        self._last_state = "stopped"
        self._bg_handle = None
        self._bg_loaded_path = None

        # Dynamic knob image handles (not baked into bg)
        self._slider_handle = None   # seek knob (slider.png)
        self._vol_knob_handle = None  # vol/bal knob (slider2.png)
        self._slider_active_handle = None   # brightened seek knob
        self._vol_knob_active_handle = None  # brightened vol/bal knob
        self._knobs_loaded = False

        # Temporary value overlay timers
        self._vol_show_until = 0
        self._bal_show_until = 0
        self._VALUE_DISPLAY_SECS = 2

    def layout(self, skin):
        """Calculate widget positions based on skin style."""
        style = skin.style
        pad = 8

        if style == "modern":
            title_fs = skin.font("track")
            self.title_scroll = ScrollText(pad, 20, SCREEN_W - pad * 2,
                                           title_fs, speed=1)
            time_fs = skin.font("time")
            self.time_display = TimeDisplay(0, 70, time_fs)
            self.progress = ProgressBar(pad, 130, SCREEN_W - pad * 2, 4)
            tw = TransportIcons(0, 0, 14, 12).total_width
            self.transport = TransportIcons((SCREEN_W - tw) // 2, 155,
                                           14, 12)
            self.volume = VolumeBar(SCREEN_W - 140, 195, 100, 4)
            # Shuffle/repeat text positions after transport row
            tx = self.transport.x + self.transport.total_width + 16
            ty = self.transport.y
            self._shuffle_box = (tx, ty - 2, 36,
                                 self.transport.size + 4)
            self._repeat_box = (tx + 44, ty - 2, 36,
                                self.transport.size + 4)

        elif style == "retro":
            title_fs = skin.font("track")
            self.title_scroll = ScrollText(pad, 8, SCREEN_W - pad * 2,
                                           title_fs, speed=3)
            time_fs = skin.font("time")
            self.time_display = TimeDisplay(pad, 40, time_fs)
            self.progress = ProgressBar(pad, 80, SCREEN_W - pad * 2, 10)
            tw = TransportIcons(0, 0, 18, 10).total_width
            self.transport = TransportIcons((SCREEN_W - tw) // 2, 120,
                                           18, 10)
            self.volume = VolumeBar(pad, 170, 120, 8)
            tx = self.transport.x + self.transport.total_width + 16
            ty = self.transport.y
            self._shuffle_box = (tx, ty - 2, 36,
                                 self.transport.size + 4)
            self._repeat_box = (tx + 44, ty - 2, 36,
                                self.transport.size + 4)

        else:
            # Classic — positions match Winamp background per placement.png
            # Left LCD area (x=20-178, y=45-109)
            time_fs = skin.font("time")
            self.time_display = TimeDisplay(24, 48, time_fs)
            # Right info panel (x=191-463, y=46-67) — scrolling title
            title_fs = skin.font("track")
            self.title_scroll = ScrollText(195, 50, 265, title_fs,
                                           speed=2)
            # Seek bar — full width in groove (y≈140, spans entire width)
            self.progress = ProgressBar(16, 140, 448, 4)
            # Volume — on the orange stripe (y≈119, x=191-295)
            # (bg_buttons mode: only fill drawn, no bg rect)
            self.volume = VolumeBar(191, 119, 104, 5)
            # Transport — 5 icon buttons matching background sprites
            self.transport = TransportIcons(20, 175, 30, 12)
            # Shuffle/repeat sprite areas in background
            self._shuffle_box = (279, 178, 72, 20)
            self._repeat_box = (369, 178, 43, 20)

        self._layout_done = True
        self._layout_style = skin.style

    def handle_input(self, button, event_type, pager):
        """Handle d-pad navigation between focus rows."""
        BTN_A = 0x10
        BTN_B = 0x20
        BTN_UP = 0x01
        BTN_DOWN = 0x02
        BTN_LEFT = 0x04
        BTN_RIGHT = 0x08

        if event_type != 1:
            return None

        if self._focus == self.FOCUS_TRANSPORT:
            if button == BTN_LEFT:
                self._btn_index = max(0, self._btn_index - 1)
                self._sync_transport_sel()
            elif button == BTN_RIGHT:
                self._btn_index = min(self._BTN_COUNT - 1,
                                      self._btn_index + 1)
                self._sync_transport_sel()
            elif button == BTN_UP:
                self._focus = self.FOCUS_SEEK
            elif button == BTN_A:
                result = self._execute_action()
                if result:
                    return result
            elif button == BTN_B:
                return "menu"

        elif self._focus == self.FOCUS_SEEK:
            if button == BTN_LEFT:
                self.client.seek_relative(-10)
            elif button == BTN_RIGHT:
                self.client.seek_relative(10)
            elif button == BTN_UP:
                self._focus = self.FOCUS_VOLUME
            elif button == BTN_DOWN:
                self._focus = self.FOCUS_TRANSPORT
            elif button == BTN_B:
                self._focus = self.FOCUS_TRANSPORT

        elif self._focus == self.FOCUS_VOLUME:
            if button == BTN_LEFT:
                self.client.adjust_volume(-5)
                self._vol_show_until = time.time() + self._VALUE_DISPLAY_SECS
            elif button == BTN_RIGHT:
                self.client.adjust_volume(5)
                self._vol_show_until = time.time() + self._VALUE_DISPLAY_SECS
            elif button == BTN_UP:
                self._focus = self.FOCUS_BALANCE
            elif button == BTN_DOWN:
                self._focus = self.FOCUS_SEEK
            elif button == BTN_B:
                self._focus = self.FOCUS_TRANSPORT

        elif self._focus == self.FOCUS_BALANCE:
            if button == BTN_LEFT:
                self._balance = max(0, self._balance - 5)
                self._bal_show_until = time.time() + self._VALUE_DISPLAY_SECS
            elif button == BTN_RIGHT:
                self._balance = min(100, self._balance + 5)
                self._bal_show_until = time.time() + self._VALUE_DISPLAY_SECS
            elif button == BTN_DOWN:
                self._focus = self.FOCUS_VOLUME
            elif button == BTN_B:
                self._focus = self.FOCUS_TRANSPORT

        return None

    def _sync_transport_sel(self):
        """Keep TransportIcons.selected in sync with _btn_index."""
        if self._btn_index <= 4:
            self.transport.selected = self._btn_index
        else:
            self.transport.selected = -1

    def _execute_action(self):
        """Execute action for the current _btn_index."""
        if self._btn_index <= 4:
            self._execute_transport()
        elif self._btn_index == 5:
            return "browser"
        elif self._btn_index == 6:
            self.playlist.set_shuffle(not self.playlist.shuffle)
        elif self._btn_index == 7:
            self.playlist.cycle_repeat()
        return None

    def _execute_transport(self):
        """Execute the currently selected transport action."""
        name = self.transport.selected_name
        if name == "prev":
            track = self.playlist.prev()
            if track:
                self.client.play(track)
        elif name == "play":
            if self._last_state == "stopped" and not self.playlist.is_empty:
                track = self.playlist.current_track()
                if track:
                    self.client.play(track)
            else:
                self.client.resume()
        elif name == "pause":
            self.client.pause()
        elif name == "stop":
            self.client.stop()
        elif name == "next":
            track = self.playlist.next()
            if track:
                self.client.play(track)

    def update(self, status):
        """Update widgets from daemon status."""
        self._last_state = status.get("state", "stopped")
        if self._last_state == "playing":
            self.transport.active = "play"
        elif self._last_state == "paused":
            self.transport.active = "pause"
        else:
            self.transport.active = "stop"
        self.progress.set_progress(status.get("pos", 0),
                                   status.get("dur", 0))
        self.time_display.seconds = status.get("pos", 0)
        self.volume.level = status.get("vol", 80)

    def draw(self, pager, skin):
        """Render the now playing screen."""
        if not self._layout_done or skin.style != self._layout_style:
            self.layout(skin)

        c = skin.color
        has_bg = False

        # Background image
        bg = skin.bg_path
        if bg:
            if bg != self._bg_loaded_path:
                # Free all cached image handles
                if self._bg_handle:
                    pager.free_image(self._bg_handle)
                for h in self._active_handles:
                    if h is not None:
                        pager.free_image(h)
                self._active_handles = [None] * self._BTN_COUNT
                self._active_loaded = False
                if self._slider_handle:
                    pager.free_image(self._slider_handle)
                self._slider_handle = None
                if self._vol_knob_handle:
                    pager.free_image(self._vol_knob_handle)
                self._vol_knob_handle = None
                if self._slider_active_handle:
                    pager.free_image(self._slider_active_handle)
                self._slider_active_handle = None
                if self._vol_knob_active_handle:
                    pager.free_image(self._vol_knob_active_handle)
                self._vol_knob_active_handle = None
                self._knobs_loaded = False
                for h in self._toggle_handles.values():
                    if h is not None:
                        pager.free_image(h)
                self._toggle_handles = {}
                self._toggle_loaded = False
                self._bg_handle = pager.load_image(bg)
                self._bg_loaded_path = bg
            if self._bg_handle:
                pager.draw_image(0, 0, self._bg_handle)
                has_bg = True
            else:
                pager.clear(c("bg"))
        else:
            pager.clear(c("bg"))

        bg_buttons = has_bg and skin.bg_has_buttons

        # --- Load sprites once (after bg is loaded) ---
        if bg_buttons:
            skin_dir = (os.path.dirname(skin.bg_path)
                        if skin.bg_path else "")
            if not self._active_loaded:
                for i, (fname, _, _) in enumerate(
                        self._active_sprite_info):
                    apath = os.path.join(skin_dir, fname)
                    if os.path.exists(apath):
                        self._active_handles[i] = pager.load_image(
                            apath)
                self._active_loaded = True
            if not self._knobs_loaded:
                knob_path = os.path.join(skin_dir, "slider-knob.png")
                if os.path.exists(knob_path):
                    self._slider_handle = pager.load_image(knob_path)
                vol_path = os.path.join(skin_dir, "vol-knob.png")
                if os.path.exists(vol_path):
                    self._vol_knob_handle = pager.load_image(vol_path)
                knob_a = os.path.join(skin_dir, "slider-knob-active.png")
                if os.path.exists(knob_a):
                    self._slider_active_handle = pager.load_image(knob_a)
                vol_a = os.path.join(skin_dir, "vol-knob-active.png")
                if os.path.exists(vol_a):
                    self._vol_knob_active_handle = pager.load_image(vol_a)
                self._knobs_loaded = True
            if not self._toggle_loaded:
                for fname, _, _ in self._toggle_sprite_info:
                    tpath = os.path.join(skin_dir, fname)
                    if os.path.exists(tpath):
                        self._toggle_handles[fname] = \
                            pager.load_image(tpath)
                self._toggle_loaded = True

        # --- Classic skin: LCD info ---
        if skin.style == "classic":
            if not has_bg:
                pager.fill_rect(0, 0, SCREEN_W, 22, c("title_bar_bg"))
                pager.draw_ttf(6, 2, "PagerAmp", c("title_bar_text"),
                              FONT_PATH, skin.font("title"))

            # Left LCD area (x=20-178, y=45-109)
            # Large time display (top-left)
            time_text = _format_time(self.time_display.seconds)
            pager.draw_ttf(22, 46, time_text, c("text_dim"),
                          FONT_PATH, 40)

            # State indicator (PLAY/STOP/PAUS) — top-right of LCD box
            state_str = self._last_state[:4].upper()
            sw = pager.ttf_width(state_str, FONT_PATH, 14)
            pager.draw_ttf(172 - sw, 48, state_str, c("text_dim"),
                          FONT_PATH, 14)

            # Bottom of LCD: bitrate | track/total | duration (doubled size)
            rate = self.client.status.get("rate", 44100)
            rate_str = "%dk" % (rate // 1000) if rate else ""

            track_num = self.client.status.get("track", 0)
            track_total = self.client.status.get("total", 0)
            if track_total > 0:
                counter_str = "%d/%d" % (track_num, track_total)
            else:
                pos = (self.playlist.position + 1
                       if self.playlist.length else 0)
                counter_str = "%d/%d" % (pos, self.playlist.length)

            dur = self.client.status.get("dur", 0)
            dur_str = _format_time(dur) if dur > 0 else ""

            info_parts = [s for s in [rate_str, counter_str, dur_str]
                          if s]
            info_line = "  ".join(info_parts)
            pager.draw_ttf(22, 98, info_line, c("text_dim"),
                          FONT_PATH, 18)

        elif skin.style == "modern":
            state_text = self._last_state.upper()
            pager.draw_ttf(8, 4, state_text, c("text_dim"),
                          FONT_PATH, skin.font("label"))
            track_info = "%d / %d" % (
                self.client.status.get("track", 0),
                self.client.status.get("total", 0))
            tiw = pager.ttf_width(track_info, FONT_PATH,
                                  skin.font("label"))
            pager.draw_ttf(SCREEN_W - tiw - 8, 4, track_info,
                          c("text_dim"), FONT_PATH, skin.font("label"))
            time_str = _format_time(self.time_display.seconds)
            tiw = pager.ttf_width(time_str, FONT_PATH,
                                  skin.font("time"))
            self.time_display.x = (SCREEN_W - tiw) // 2
            self.time_display.draw(pager, c("time"))

        else:
            # Retro
            self.time_display.draw(pager, c("time"))
            dur_str = _format_time(self.client.status.get("dur", 0))
            dur_w = pager.ttf_width(dur_str, FONT_PATH,
                                    skin.font("time"))
            pager.draw_ttf(SCREEN_W - dur_w - 8, self.time_display.y,
                          dur_str, c("text_dim"), FONT_PATH,
                          skin.font("time"))

        # --- Scrolling track name ---
        track_name = self.playlist.current_name()
        if not track_name:
            fname = self.client.status.get("file", "")
            if fname:
                track_name = os.path.splitext(
                    os.path.basename(fname))[0]
            else:
                track_name = "No track loaded"
        self.title_scroll.set_text(track_name, pager)
        self.title_scroll.update()
        self.title_scroll.draw(pager, c("text_dim"))

        # --- Seek knob (slider-knob.png on groove, no color fill) ---
        if bg_buttons:
            if self._slider_handle:
                groove_x0 = 26   # left edge of seek groove
                groove_x1 = 420  # right edge of seek groove
                knob_w = 29
                knob_range = groove_x1 - groove_x0 - knob_w
                kx = groove_x0 + int(knob_range * self.progress.position)
                if (self._focus == self.FOCUS_SEEK
                        and self._slider_active_handle):
                    pager.draw_image(kx, 139, self._slider_active_handle)
                else:
                    pager.draw_image(kx, 139, self._slider_handle)
        else:
            self.progress.draw(pager, c("progress_bg"),
                              c("progress_fill"), c("progress_knob"),
                              c("text_dim"), skin.font("label"))

        # --- Volume knob (slider2.png on orange groove) ---
        if bg_buttons and self._vol_knob_handle:
            vx0, vx1, vy = self._vol_groove
            knob_w = 28  # vol-knob.png width
            vol_range = vx1 - vx0 - knob_w
            vol_x = vx0 + int(vol_range * self.volume.level / 100)
            if (self._focus == self.FOCUS_VOLUME
                    and self._vol_knob_active_handle):
                pager.draw_image(vol_x, vy, self._vol_knob_active_handle)
            else:
                pager.draw_image(vol_x, vy, self._vol_knob_handle)
        elif not bg_buttons:
            self.volume.draw(pager, c("volume_bg"), c("volume_fill"),
                            c("text_dim"), skin.font("label"))

        # --- Balance knob (slider2.png on green groove) ---
        if bg_buttons and self._vol_knob_handle:
            bx0, bx1, by = self._bal_groove
            knob_w = 28
            bal_range = bx1 - bx0 - knob_w
            bal_x = bx0 + int(bal_range * self._balance / 100)
            if (self._focus == self.FOCUS_BALANCE
                    and self._vol_knob_active_handle):
                pager.draw_image(bal_x, by, self._vol_knob_active_handle)
            else:
                pager.draw_image(bal_x, by, self._vol_knob_handle)

        # --- Temporary value overlay (volume % / balance L-R) ---
        now = time.time()
        if now < self._vol_show_until:
            vol_text = "VOL: %d%%" % self.volume.level
            tw = pager.ttf_width(vol_text, FONT_PATH, 12)
            ox = (SCREEN_W - tw) // 2
            oy = 68
            pager.fill_rect(ox - 4, oy - 2, tw + 8, 18, c("menu_bg"))
            pager.draw_ttf(ox, oy, vol_text, c("accent"), FONT_PATH, 12)
        if now < self._bal_show_until:
            if self._balance == 50:
                bal_text = "BAL: CENTER"
            elif self._balance < 50:
                bal_text = "BAL: %dL" % (50 - self._balance)
            else:
                bal_text = "BAL: %dR" % (self._balance - 50)
            tw = pager.ttf_width(bal_text, FONT_PATH, 12)
            ox = (SCREEN_W - tw) // 2
            oy = 68
            pager.fill_rect(ox - 4, oy - 2, tw + 8, 18, c("menu_bg"))
            pager.draw_ttf(ox, oy, bal_text, c("accent"), FONT_PATH, 12)

        # --- Transport icons ---
        if bg_buttons:
            self.transport.draw(pager, c("transport"),
                               c("transport_active"), None,
                               bg_has_buttons=True)
        else:
            sel_color = (c("accent")
                         if self._focus == self.FOCUS_TRANSPORT
                         else None)
            self.transport.draw(pager, c("transport"),
                               c("transport_active"), sel_color,
                               bg_has_buttons=False)

        # --- Active/toggled button sprites ---
        if bg_buttons and self._focus == self.FOCUS_TRANSPORT:
            idx = self._btn_index
            if idx < len(self._active_sprite_info):
                fname, ax, ay = self._active_sprite_info[idx]
                if idx == 6 and self.playlist.shuffle:
                    th = self._toggle_handles.get(
                        "shuffle-active-toggled.png")
                    if th:
                        pager.draw_image(ax, ay, th)
                    elif self._active_handles[idx]:
                        pager.draw_image(ax, ay,
                                        self._active_handles[idx])
                elif idx == 7 and self.playlist.repeat != 0:
                    th = self._toggle_handles.get(
                        "repeat-active-toggled.png")
                    if th:
                        pager.draw_image(ax, ay, th)
                    elif self._active_handles[idx]:
                        pager.draw_image(ax, ay,
                                        self._active_handles[idx])
                elif self._active_handles[idx]:
                    pager.draw_image(ax, ay,
                                    self._active_handles[idx])

        # --- Shuffle/repeat toggle overlays ---
        if bg_buttons:
            is_shuf_sel = (self._focus == self.FOCUS_TRANSPORT
                           and self._btn_index == 6)
            is_rep_sel = (self._focus == self.FOCUS_TRANSPORT
                          and self._btn_index == 7)
            if self.playlist.shuffle and not is_shuf_sel:
                th = self._toggle_handles.get("shuffle-toggled.png")
                if th:
                    pager.draw_image(279, 178, th)
            if self.playlist.repeat != 0 and not is_rep_sel:
                th = self._toggle_handles.get("repeat-toggled.png")
                if th:
                    pager.draw_image(369, 178, th)
        else:
            sx, sy, sw, sh = self._shuffle_box
            sc = (c("shuffle_on") if self.playlist.shuffle
                  else c("shuffle_off"))
            pager.draw_ttf(sx + 2, sy + 2, "SHF", sc, FONT_PATH, 10)
            rx, ry, rw, rh = self._repeat_box
            rc = (c("repeat_on") if self.playlist.repeat != 0
                  else c("repeat_off"))
            rl = "RPT:" + self.playlist.repeat_label
            pager.draw_ttf(rx + 2, ry + 2, rl, rc, FONT_PATH, 10)

        # Separator (fallback when no bg)
        if skin.style == "classic" and not has_bg:
            pager.hline(0, 23, SCREEN_W, c("separator"))


class PlaylistScreen:
    """Playlist view — scrollable track list."""

    def __init__(self, client, playlist):
        self.client = client
        self.playlist = playlist
        self.track_list = TrackList(0, 24, SCREEN_W, SCREEN_H - 28, 12)

    def _sync_tracks(self):
        """Sync track list with playlist."""
        names = [self.playlist.track_name(i)
                 for i in range(self.playlist.length)]
        self.track_list.set_tracks(names)
        idx = self.playlist.current_track_index()
        if idx is not None:
            self.track_list.current_playing = idx

    def handle_input(self, button, event_type, pager):
        BTN_A = 0x10
        BTN_B = 0x20
        BTN_UP = 0x01
        BTN_DOWN = 0x02
        BTN_LEFT = 0x04
        BTN_RIGHT = 0x08

        if event_type != 1:
            return None

        if button == BTN_UP:
            self.track_list.navigate(-1)
        elif button == BTN_DOWN:
            self.track_list.navigate(1)
        elif button == BTN_LEFT:
            self.track_list.page_up()
        elif button == BTN_RIGHT:
            self.track_list.page_down()
        elif button == BTN_A:
            # Jump to selected track
            idx = self.track_list.selected
            track = self.playlist.jump_to(idx)
            if track:
                self.client.play(track)
            return "now_playing"
        elif button == BTN_B:
            return "now_playing"

        return None

    def update(self, status):
        self._sync_tracks()

    def draw(self, pager, skin):
        c = skin.color
        pager.clear(c("bg"))

        # Header
        pager.fill_rect(0, 0, SCREEN_W, 22, c("title_bar_bg"))
        pager.draw_ttf(6, 2, "Playlist", c("title_bar_text"),
                      FONT_PATH, skin.font("title"))

        count = "%d tracks" % self.playlist.length
        cw = pager.ttf_width(count, FONT_PATH, skin.font("title"))
        pager.draw_ttf(SCREEN_W - cw - 6, 2, count,
                      c("title_bar_text"), FONT_PATH, skin.font("title"))

        # Track list
        self.track_list.draw(pager, c("track_text"), c("track_highlight"),
                            c("track_highlight_text"), c("track_number"),
                            c("accent"))


class FileBrowserScreen:
    """File browser for /mmc/music/."""

    def __init__(self, client, playlist, root_dir="/mmc/music"):
        self.client = client
        self.playlist = playlist
        self.root_dir = root_dir
        self.current_dir = root_dir
        self.entries = []       # list of (name, is_dir, full_path)
        self.selected = 0
        self.scroll_offset = 0
        self.font_size = 12
        self.line_height = 18
        self.visible_count = (SCREEN_H - 28) // self.line_height
        self._scan_dir()

    def enter(self):
        """Rescan current directory (picks up new uploads)."""
        self._scan_dir()

    def _scan_dir(self):
        """Scan current directory for files and subdirectories."""
        self.entries = []
        self.selected = 0
        self.scroll_offset = 0

        # Add parent directory entry if not at root
        if self.current_dir != self.root_dir:
            self.entries.append(("..", True,
                                os.path.dirname(self.current_dir)))

        if not os.path.isdir(self.current_dir):
            return

        dirs = []
        files = []
        for name in sorted(os.listdir(self.current_dir)):
            full = os.path.join(self.current_dir, name)
            if os.path.isdir(full):
                dirs.append((name + "/", True, full))
            elif name.lower().endswith((".mp3", ".wav", ".m3u")):
                files.append((name, False, full))

        self.entries.extend(dirs)
        self.entries.extend(files)

    def handle_input(self, button, event_type, pager):
        BTN_A = 0x10
        BTN_B = 0x20
        BTN_UP = 0x01
        BTN_DOWN = 0x02
        BTN_LEFT = 0x04
        BTN_RIGHT = 0x08

        if event_type != 1:
            return None

        if button == BTN_UP:
            self.selected = max(0, self.selected - 1)
            if self.selected < self.scroll_offset:
                self.scroll_offset = self.selected
        elif button == BTN_DOWN:
            self.selected = min(len(self.entries) - 1, self.selected + 1)
            if self.selected >= self.scroll_offset + self.visible_count:
                self.scroll_offset = self.selected - self.visible_count + 1
        elif button == BTN_LEFT:
            # Page up
            self.selected = max(0, self.selected - self.visible_count)
            self.scroll_offset = max(0,
                                     self.scroll_offset - self.visible_count)
        elif button == BTN_RIGHT:
            # Page down
            self.selected = min(len(self.entries) - 1,
                               self.selected + self.visible_count)
            if self.selected >= self.scroll_offset + self.visible_count:
                self.scroll_offset = self.selected - self.visible_count + 1
        elif button == BTN_A:
            if not self.entries:
                return None
            name, is_dir, path = self.entries[self.selected]
            if is_dir:
                self.current_dir = path
                self._scan_dir()
            elif path.lower().endswith(".m3u"):
                # Load playlist and play first track
                self.playlist.load_m3u(path)
                track = self.playlist.current_track()
                if track:
                    self.client.play(track)
                return "now_playing"
            else:
                # Play file — load entire directory as playlist
                self._load_dir_playlist(path)
                return "now_playing"
        elif button == BTN_B:
            if self.current_dir != self.root_dir:
                self.current_dir = os.path.dirname(self.current_dir)
                self._scan_dir()
            else:
                return "now_playing"

        return None

    def _load_dir_playlist(self, selected_file):
        """Load all music files in current dir as playlist, starting at selected."""
        files = [e[2] for e in self.entries if not e[1] and
                 e[2].lower().endswith((".mp3", ".wav"))]
        if not files:
            return
        self.playlist.load_files(files)
        # Find and jump to selected file
        try:
            idx = files.index(selected_file)
            self.playlist.jump_to(idx)
        except ValueError:
            idx = 0
        self.client.play(files[idx])

    def update(self, status):
        pass

    def draw(self, pager, skin):
        c = skin.color
        pager.clear(c("bg"))

        # Header
        pager.fill_rect(0, 0, SCREEN_W, 22, c("title_bar_bg"))

        # Show relative path
        rel = self.current_dir
        if rel.startswith(self.root_dir):
            rel = rel[len(self.root_dir):]
        if not rel:
            rel = "/"
        header = "Browse: " + rel
        pager.draw_ttf(6, 2, header, c("title_bar_text"),
                      FONT_PATH, skin.font("title"))

        # File list
        for i in range(self.visible_count):
            idx = self.scroll_offset + i
            if idx >= len(self.entries):
                break

            name, is_dir, path = self.entries[idx]
            ty = 26 + i * self.line_height
            is_sel = (idx == self.selected)

            if is_sel:
                pager.fill_rect(0, ty, SCREEN_W, self.line_height - 1,
                               c("track_highlight"))

            tc = c("track_highlight_text") if is_sel else c("track_text")
            if is_dir:
                icon = "[D] " if name != ".." else " <- "
            else:
                icon = "    "

            display_name = icon + name
            # Truncate
            max_w = SCREEN_W - 16
            while (pager.ttf_width(display_name, FONT_PATH, self.font_size)
                   > max_w and len(display_name) > 5):
                display_name = display_name[:-1]

            pager.draw_ttf(4, ty + 1, display_name, tc,
                          FONT_PATH, self.font_size)


class SettingsScreen:
    """Settings and preferences screen."""

    BRIGHTNESS_STEPS = [20, 40, 60, 80, 100]

    def __init__(self, skin_manager, playlist, settings):
        self.skin_manager = skin_manager
        self.playlist = playlist
        self.settings = settings
        self.pager = None
        self.items = []
        self.selected = 0
        self.font_size = 14
        self.line_height = 24
        self._build_items()

    def set_pager(self, pager):
        """Store pager reference for brightness control."""
        self.pager = pager

    def _build_items(self):
        self.items = [
            ("Theme", self.skin_manager.current_name, self._cycle_theme),
            ("Brightness",
             lambda: "%d%%" % self.settings.get("brightness", 100),
             self._cycle_brightness),
            ("Shuffle", lambda: "On" if self.playlist.shuffle else "Off",
             self._toggle_shuffle),
            ("Repeat", lambda: self.playlist.repeat_label,
             self._cycle_repeat),
            ("Bluetooth", lambda: "Setup >>", None),
            ("Browse Files", lambda: ">>", None),
            ("Exit PagerAmp", lambda: "", None),
        ]

    def _cycle_brightness(self):
        cur = self.settings.get("brightness", 100)
        steps = self.BRIGHTNESS_STEPS
        try:
            idx = steps.index(cur)
            nxt = steps[(idx + 1) % len(steps)]
        except ValueError:
            nxt = steps[0]
        self.settings["brightness"] = nxt
        if self.pager:
            self.pager.set_brightness(nxt)

    def _cycle_theme(self):
        self.skin_manager.next_skin()
        self.settings["theme"] = self.skin_manager.current_name

    def _toggle_shuffle(self):
        self.playlist.set_shuffle(not self.playlist.shuffle)

    def _cycle_repeat(self):
        self.playlist.cycle_repeat()

    def handle_input(self, button, event_type, pager):
        BTN_A = 0x10
        BTN_B = 0x20
        BTN_UP = 0x01
        BTN_DOWN = 0x02

        if event_type != 1:
            return None

        if button == BTN_UP:
            self.selected = (self.selected - 1) % len(self.items)
        elif button == BTN_DOWN:
            self.selected = (self.selected + 1) % len(self.items)
        elif button == BTN_A:
            label = self.items[self.selected][0]
            action = self.items[self.selected][2]
            if action:
                action()
            elif label == "Bluetooth":
                return "bluetooth"
            elif label == "Browse Files":
                return "browser"
            elif label == "Exit PagerAmp":
                return "exit"
        elif button == BTN_B:
            return "now_playing"

        return None

    def update(self, status):
        self._build_items()

    def draw(self, pager, skin):
        c = skin.color
        pager.clear(c("bg"))

        # Header
        pager.fill_rect(0, 0, SCREEN_W, 22, c("title_bar_bg"))
        pager.draw_ttf(6, 2, "Settings", c("title_bar_text"),
                      FONT_PATH, skin.font("title"))

        # Menu items
        y = 28
        for i, (label, value_fn, _) in enumerate(self.items):
            is_sel = (i == self.selected)

            if is_sel:
                pager.fill_rect(0, y, SCREEN_W, self.line_height - 1,
                               c("track_highlight"))

            tc = c("track_highlight_text") if is_sel else c("menu_text")

            pager.draw_ttf(8, y + 3, label, tc, FONT_PATH, self.font_size)

            # Value on right side
            if callable(value_fn):
                val = value_fn()
            else:
                val = value_fn
            if val:
                vw = pager.ttf_width(val, FONT_PATH, self.font_size)
                vc = c("accent") if is_sel else c("text_dim")
                pager.draw_ttf(SCREEN_W - vw - 12, y + 3, val, vc,
                              FONT_PATH, self.font_size)

            y += self.line_height


class MenuOverlay:
    """Quick menu overlay accessible from any screen."""

    def __init__(self):
        self.items = [
            "Playlist",
            "Browse Files",
            "Settings",
            "Bluetooth",
            "Exit PagerAmp",
        ]
        self.selected = 0
        self.font_size = 14
        self.line_height = 26
        self._target_map = {
            "Playlist": "playlist",
            "Browse Files": "browser",
            "Settings": "settings",
            "Bluetooth": "bluetooth",
            "Exit PagerAmp": "exit",
        }

    def handle_input(self, button, event_type, pager):
        BTN_A = 0x10
        BTN_B = 0x20
        BTN_UP = 0x01
        BTN_DOWN = 0x02

        if event_type != 1:
            return None

        if button == BTN_UP:
            self.selected = (self.selected - 1) % len(self.items)
        elif button == BTN_DOWN:
            self.selected = (self.selected + 1) % len(self.items)
        elif button == BTN_A:
            return self._target_map.get(self.items[self.selected],
                                        "now_playing")
        elif button == BTN_B:
            return "close_menu"

        return None

    def draw(self, pager, skin):
        c = skin.color

        # Semi-transparent overlay effect — dark background
        menu_w = 240
        menu_h = len(self.items) * self.line_height + 10
        mx = (SCREEN_W - menu_w) // 2
        my = (SCREEN_H - menu_h) // 2

        # Background box
        pager.fill_rect(mx, my, menu_w, menu_h, c("menu_bg"))
        pager.rect(mx, my, menu_w, menu_h, c("separator"))

        # Items
        y = my + 5
        for i, item in enumerate(self.items):
            is_sel = (i == self.selected)
            if is_sel:
                pager.fill_rect(mx + 1, y, menu_w - 2,
                               self.line_height - 2, c("track_highlight"))
            tc = c("menu_selected") if is_sel else c("menu_text")
            pager.draw_ttf(mx + 16, y + 4, item, tc,
                          FONT_PATH, self.font_size)
            y += self.line_height
