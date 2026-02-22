#!/usr/bin/env python3
"""
PagerAmp — Winamp-inspired music player for WiFi Pineapple Pager.

Main application: screen state machine, render loop, input dispatch.
Uses mpg123 in remote mode for audio playback.
"""

import os
import sys
import json
import time
import signal
import subprocess

# Add lib directory to path for pagerctl
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(SCRIPT_DIR, "lib"))

from pagerctl import Pager

from player.client import Mpg123Client
from player.playlist import Playlist
from ui.skin import SkinManager
from ui.screens import (StartScreen, NowPlayingScreen, PlaylistScreen,
                        FileBrowserScreen, SettingsScreen, MenuOverlay,
                        SCREEN_W, SCREEN_H)
from ui.bluetooth import BluetoothScreen

# Settings file
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
SETTINGS_FILE = os.path.join(DATA_DIR, "settings.json")
MUSIC_DIR = "/mmc/music"

# Target frame rate — low to save CPU for BT audio on MIPS
TARGET_FPS = 5
FRAME_TIME = 1.0 / TARGET_FPS

# Auto-dim after inactivity (seconds)
DIM_TIMEOUT = 120
DIM_BRIGHTNESS = 10


def load_settings():
    """Load settings from JSON file."""
    defaults = {
        "theme": "Winamp Classic",
        "volume": 80,
        "shuffle": False,
        "repeat": 0,
        "bt_device_mac": "",
        "bt_device_name": "",
        "brightness": 100,
    }
    try:
        with open(SETTINGS_FILE, "r") as f:
            saved = json.load(f)
            defaults.update(saved)
    except (IOError, json.JSONDecodeError, ValueError):
        pass
    return defaults


def save_settings(settings):
    """Save settings to JSON file."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(settings, f, indent=2)
    except (IOError, OSError):
        pass


class PagerAmp:
    """Main PagerAmp application."""

    def __init__(self):
        self.pager = None
        self._bt_keepalive = None
        self.client = Mpg123Client(SCRIPT_DIR)
        self.playlist = Playlist()
        self.settings = load_settings()
        self.skin_manager = SkinManager()
        self.running = True
        self.exit_code = 0

        # Set saved theme
        theme = self.settings.get("theme", "Winamp Classic")
        self.skin_manager.set_skin(theme)

        # Restore playlist settings
        self.playlist.set_shuffle(self.settings.get("shuffle", False))
        self.playlist.repeat = self.settings.get("repeat", 0)

        # Screens
        self.screens = {}
        self.current_screen = "start"
        self.menu_active = False
        self.menu = MenuOverlay()
        self._prev_state = "stopped"

        # Frame timing
        self._last_frame = 0

        # Auto-dim state
        self._last_activity = time.time()
        self._dimmed = False

    def init_display(self):
        """Initialize the Pager display."""
        self.pager = Pager()
        self.pager.init()
        self.pager.set_rotation(270)
        self.pager.set_brightness(self.settings.get("brightness", 100))

    def init_screens(self):
        """Create all screen instances."""
        self.screens = {
            "start": StartScreen(self.skin_manager, self.settings),
            "now_playing": NowPlayingScreen(self.client, self.playlist),
            "playlist": PlaylistScreen(self.client, self.playlist),
            "browser": FileBrowserScreen(self.client, self.playlist, MUSIC_DIR),
            "settings": SettingsScreen(self.skin_manager, self.playlist,
                                       self.settings),
            "bluetooth": BluetoothScreen(self.settings),
        }

        # Give SettingsScreen access to pager for brightness control
        self.screens["settings"].set_pager(self.pager)

        # Layout now_playing for initial skin
        self.screens["now_playing"].layout(self.skin_manager.current)

    def _start_bt_keepalive(self):
        """Start silence process to hold dmix/bluealsa open across tracks."""
        env = dict(os.environ)
        bt_lib = os.path.join(SCRIPT_DIR, "bt", "lib")
        env.setdefault("ALSA_PLUGIN_DIR", bt_lib)
        env.setdefault("ALSA_CONFIG_PATH",
                       os.path.join(SCRIPT_DIR, "config", "asound.conf"))
        ld = env.get("LD_LIBRARY_PATH", "")
        our_ld = "%s:%s/lib" % (bt_lib, SCRIPT_DIR)
        if our_ld not in ld:
            env["LD_LIBRARY_PATH"] = our_ld + (":" + ld if ld else "")
        aplay = os.path.join(SCRIPT_DIR, "bin", "aplay")
        if not os.path.exists(aplay):
            return
        try:
            self._bt_keepalive = subprocess.Popen(
                [aplay, "-D", "btmix", "-f", "S16_LE", "-r", "44100",
                 "-c", "2", "-t", "raw", "-q", "/dev/zero"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        except OSError:
            self._bt_keepalive = None

    def _stop_bt_keepalive(self):
        """Stop the silence keepalive process."""
        if self._bt_keepalive:
            try:
                self._bt_keepalive.terminate()
                self._bt_keepalive.wait(timeout=2)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self._bt_keepalive.kill()
                    self._bt_keepalive.wait(timeout=1)
                except OSError:
                    pass
            self._bt_keepalive = None

    def init_audio(self):
        """Start mpg123 and set initial volume."""
        self._start_bt_keepalive()
        self.client.start()
        self.client.set_volume(self.settings.get("volume", 80))

        # Auto-load music directory if it exists
        if os.path.isdir(MUSIC_DIR):
            self.playlist.load_directory(MUSIC_DIR)

    def handle_input(self):
        """Poll input and dispatch to current screen."""
        self.pager.poll_input()

        while True:
            event = self.pager.get_input_event()
            if not event:
                break

            button, event_type, timestamp = event

            # Any input resets dim timer
            self._last_activity = time.time()
            if self._dimmed:
                brightness = self.settings.get("brightness", 100)
                self.pager.set_brightness(brightness)
                self._dimmed = False

            # Menu overlay takes priority
            if self.menu_active:
                result = self.menu.handle_input(button, event_type, self.pager)
                if result == "close_menu":
                    self.menu_active = False
                elif result:
                    self.menu_active = False
                    self._handle_screen_result(result)
                continue

            # Dispatch to current screen
            screen = self.screens.get(self.current_screen)
            if screen:
                result = screen.handle_input(button, event_type, self.pager)
                if result:
                    self._handle_screen_result(result)

    def _switch_screen(self, name):
        """Switch to a named screen."""
        if name in self.screens:
            self.current_screen = name
            # Call enter() if screen has it
            screen = self.screens[name]
            if hasattr(screen, "enter"):
                screen.enter()
            # Re-layout now_playing when skin changes
            if name == "now_playing":
                screen.layout(self.skin_manager.current)

    def _handle_screen_result(self, result):
        """Handle screen transition results."""
        if result == "menu":
            self.menu_active = True
            self.menu.selected = 0
        elif result == "exit":
            self.running = False
            self.exit_code = 0
        elif result == "exit_handoff":
            self.running = False
            self.exit_code = 42
        elif result in self.screens:
            # Restart mpg123 when leaving BT screen (ALSA config may have changed)
            if self.current_screen == "bluetooth" and result != "bluetooth":
                self.client.restart()
                self.client.set_volume(self.settings.get("volume", 80))
            if result == "bluetooth":
                self.screens["bluetooth"].return_screen = self.current_screen
            self._switch_screen(result)

    def update(self):
        """Update state — poll mpg123 status, auto-advance, auto-dim."""
        # Auto-dim after inactivity
        if (not self._dimmed and
                time.time() - self._last_activity > DIM_TIMEOUT):
            self.pager.set_brightness(DIM_BRIGHTNESS)
            self._dimmed = True

        status = self.client.poll_status()

        screen = self.screens.get(self.current_screen)
        if screen:
            screen.update(status)

        # Auto-advance: when a track finishes naturally, play the next one
        # (skip if user pressed stop manually)
        cur_state = status.get("state", "stopped")
        advance = False
        if self.client.track_finished:
            self.client.clear_track_finished()
            if not self.client._manual_stop:
                advance = True
        elif (self._prev_state == "playing" and cur_state == "stopped"
              and not self.client._manual_stop):
            advance = True
        self._prev_state = cur_state

        if advance and not self.playlist.is_empty:
            track = self.playlist.next()
            if track:
                self.client.play(track)

    def draw(self):
        """Render current screen."""
        skin = self.skin_manager.current

        screen = self.screens.get(self.current_screen)
        if screen:
            screen.draw(self.pager, skin)

        # Draw menu overlay on top
        if self.menu_active:
            self.menu.draw(self.pager, skin)

        self.pager.flip()

    def run(self):
        """Main application loop."""
        self.init_display()
        self.init_screens()
        self.init_audio()

        while self.running:
            frame_start = time.time()

            self.handle_input()
            self.update()
            self.draw()

            # Frame rate limiting
            elapsed = time.time() - frame_start
            sleep_time = FRAME_TIME - elapsed
            if sleep_time > 0:
                time.sleep(sleep_time)

        self.shutdown()
        return self.exit_code

    def shutdown(self):
        """Clean shutdown."""
        # Save settings
        self.settings["volume"] = self.client._volume
        self.settings["theme"] = self.skin_manager.current_name
        self.settings["shuffle"] = self.playlist.shuffle
        self.settings["repeat"] = self.playlist.repeat
        save_settings(self.settings)

        # Stop audio
        self.client.quit()
        self.client.cleanup()
        self._stop_bt_keepalive()

        # Cleanup display
        if self.pager:
            self.pager.clear(0x0000)
            self.pager.flip()
            self.pager.cleanup()


def main():
    app = PagerAmp()

    def sig_handler(sig, frame):
        app.running = False

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    try:
        exit_code = app.run()
    except Exception as e:
        sys.stderr.write("PagerAmp error: %s\n" % str(e))
        import traceback
        traceback.print_exc()
        exit_code = 1
    finally:
        if app.pager:
            try:
                app.pager.cleanup()
            except Exception:
                pass

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
