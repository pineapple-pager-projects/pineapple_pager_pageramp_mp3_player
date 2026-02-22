"""
Bluetooth pairing wizard for PagerAmp.

Graphical reimplementation of bt-pair.sh with state machine UI.
States: CHECK_ADAPTER → SCAN → SELECT_DEVICE → PAIR → CONNECT → TEST → DONE
"""

import os
import subprocess
import time

from ui.widgets import FONT_PATH

SCREEN_W = 480
SCREEN_H = 222

# BT adapter detection
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class BluetoothScreen:
    """Bluetooth pairing wizard with graphical UI."""

    # States
    CHECK_ADAPTER = 0
    SCAN = 1
    SELECT_DEVICE = 2
    PAIR = 3
    CONNECT = 4
    TEST = 5
    DONE = 6
    ERROR = 7

    STATE_LABELS = [
        "Checking adapter...",
        "Scanning...",
        "Select device",
        "Pairing...",
        "Connecting...",
        "Testing audio...",
        "Connected!",
        "Error",
    ]

    def __init__(self, settings):
        self.settings = settings
        self.state = self.CHECK_ADAPTER
        self.hci = None
        self.adapter_mac = None
        self.devices = []       # list of (mac, name)
        self.selected = 0
        self.scroll_offset = 0
        self.message = ""
        self.error_msg = ""
        self.font_size = 12
        self.line_height = 18
        self.visible_count = (SCREEN_H - 60) // self.line_height
        self._scan_start = 0
        self._scan_duration = 12
        self._pair_pending = None  # (mac, name) when pairing requested
        self.return_screen = "settings"

    def enter(self):
        """Called when screen becomes active."""
        self.state = self.CHECK_ADAPTER
        self.devices = []
        self.message = ""
        self.error_msg = ""
        self._check_adapter()

    def _run(self, cmd, timeout=10):
        """Run a shell command and return output."""
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True,
                                    text=True, timeout=timeout)
            return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return ""

    def _check_adapter(self):
        """Find USB Bluetooth adapter (skip built-in MT7961)."""
        self.message = "Looking for USB BT dongle..."
        for hci in ("hci0", "hci1"):
            info = self._run("hciconfig -a %s 2>/dev/null" % hci)
            if "Bus: USB" not in info:
                continue
            # Skip MT7961 — broken ACL data path
            if "MediaTek" in info:
                continue
            if info:
                self.hci = hci
                # Extract MAC
                for line in info.split("\n"):
                    if "BD Address" in line:
                        parts = line.split()
                        idx = parts.index("Address:") if "Address:" in parts else -1
                        if idx >= 0 and idx + 1 < len(parts):
                            self.adapter_mac = parts[idx + 1]
                        break
                self._run("hciconfig %s up" % hci)
                self._run("hciconfig %s auth encrypt" % hci)
                self._run('hciconfig %s name "Pineapple Pager"' % hci)
                self.message = "Found: %s (%s)" % (hci, self.adapter_mac or "?")
                self.state = self.SCAN
                self._start_scan()
                return

        self.state = self.ERROR
        self.error_msg = "No USB BT dongle found.\nPlug in a dongle and try again."

    def _ensure_bluealsad(self):
        """Ensure bluealsad is running on the correct adapter."""
        bluealsad = os.path.join(SCRIPT_DIR, "bin", "bluealsad")
        if not os.path.isfile(bluealsad):
            return

        # Check if already running on the right adapter
        ps = self._run("ps w | grep bluealsad | grep -v grep")
        if ps and ("-i %s" % self.hci) in ps:
            return  # Already on correct adapter

        # Kill if running on wrong adapter
        if ps:
            self._run("killall bluealsad", timeout=3)
            time.sleep(1)

        # Start on correct adapter with library path
        lib_path = ":".join([
            os.path.join(SCRIPT_DIR, "lib"),
            os.path.join(SCRIPT_DIR, "bt", "lib"),
            "/mmc/usr/lib", "/usr/lib",
        ])
        env = dict(os.environ, LD_LIBRARY_PATH=lib_path)
        subprocess.Popen(
            [bluealsad, "-i", self.hci, "-p", "a2dp-source",
             "-p", "a2dp-sink", "-S"],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        time.sleep(3)  # Wait for profile registration

    def _start_scan(self):
        """Begin scanning for Bluetooth devices.

        Runs bluetoothctl scan and hcitool scan simultaneously.
        hcitool reliably finds classic BR/EDR audio devices, and
        bluetoothd caches them when its scan is also active.
        """
        self.message = "Scanning... Put speaker in pairing mode!"
        self.devices = []
        self._scan_start = time.time()

        # Ensure bluetoothd is running
        if not self._run("pidof bluetoothd"):
            self._run("bluetoothd -n &", timeout=3)
            time.sleep(2)

        self._run("bluetoothctl pairable on")
        if self.adapter_mac:
            self._run("bluetoothctl select %s" % self.adapter_mac)

        # Both scans run simultaneously — bluetoothd caches devices
        # found by hcitool inquiry when its own scan is active
        subprocess.Popen(
            "timeout %d bluetoothctl scan on 2>/dev/null" % self._scan_duration,
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        subprocess.Popen(
            "hcitool -i %s scan --length=8 2>/dev/null" % self.hci,
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def _poll_scan(self):
        """Check scan results from both bluetoothctl cache and hcitool."""
        elapsed = time.time() - self._scan_start
        if elapsed < self._scan_duration:
            self.message = "Scanning... %ds remaining" % int(
                self._scan_duration - elapsed)
            return

        self.devices = []
        seen = set()

        # 1. Paired devices (always show)
        paired = self._run("bluetoothctl devices Paired 2>/dev/null")
        for line in paired.split("\n"):
            if line.startswith("Device "):
                parts = line.split(None, 2)
                if len(parts) >= 3:
                    mac = parts[1]
                    name = parts[2]
                    if mac not in seen:
                        self.devices.append((mac, name + " [paired]"))
                        seen.add(mac)

        # 2. Classic BR/EDR devices from hcitool (speakers, headphones)
        #    This is the only reliable source — no BLE noise
        hci_output = self._run(
            "hcitool -i %s scan --flush --length=4 2>/dev/null" % self.hci,
            timeout=10)
        for line in hci_output.split("\n"):
            line = line.strip()
            if not line or "Scanning" in line:
                continue
            parts = line.split(None, 1)
            if len(parts) >= 1 and ":" in parts[0]:
                mac = parts[0]
                name = parts[1] if len(parts) > 1 else "Unknown"
                if mac not in seen:
                    self.devices.append((mac, name))
                    seen.add(mac)

        # Saved device as fallback
        saved = self.settings.get("bt_device_mac")
        if saved and saved not in seen:
            saved_name = self.settings.get("bt_device_name", "Saved Device")
            self.devices.insert(0, (saved, saved_name + " [saved]"))

        if self.devices:
            self.state = self.SELECT_DEVICE
            self.message = "Found %d device(s)" % len(self.devices)
            self.selected = 0
        else:
            self.message = "No devices found. Scan again?"

    def _pair_device(self, mac, name):
        """Pair with selected device."""
        self.state = self.PAIR
        self.message = "Pairing with %s..." % name

        if self.adapter_mac:
            self._run("bluetoothctl select %s" % self.adapter_mac)

        # Disconnect if currently connected (but don't remove from cache)
        self._run("bluetoothctl disconnect %s" % mac, timeout=3)
        time.sleep(0.5)

        # Force-discover device into bluetoothctl cache:
        # Run both scans simultaneously — hcitool reliably finds BR/EDR
        # devices, bluetoothd caches them when its scan is also active
        subprocess.Popen(
            "timeout 8 bluetoothctl scan on 2>/dev/null",
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        subprocess.Popen(
            "hcitool -i %s scan --length=4 2>/dev/null" % self.hci,
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        # Wait for device to appear in cache
        for _ in range(10):
            time.sleep(1)
            info = self._run("bluetoothctl info %s 2>/dev/null" % mac,
                             timeout=3)
            if "Name:" in info or "Alias:" in info:
                break

        # Pair (AlreadyExists is fine — means already paired)
        result = self._run("bluetoothctl pair %s" % mac, timeout=15)
        if "Failed" in result and "AlreadyExists" not in result:
            self.state = self.ERROR
            self.error_msg = "Pairing failed:\n%s" % result[:100]
            return

        # Trust
        self._run("bluetoothctl trust %s" % mac, timeout=5)
        time.sleep(0.5)

        # Update asound.conf
        asound_path = os.path.join(SCRIPT_DIR, "config", "asound.conf")
        if os.path.isfile(asound_path):
            self._run("sed -i 's/device \".*\"/device \"%s\"/' %s" %
                      (mac, asound_path))

        # Ensure bluealsad is running on the correct adapter
        self._ensure_bluealsad()

        # Connect
        self.state = self.CONNECT
        self.message = "Connecting..."
        result = self._run("bluetoothctl connect %s" % mac, timeout=10)
        time.sleep(3)

        # Check connection
        info = self._run("bluetoothctl info %s" % mac, timeout=5)
        if "Connected: yes" in info:
            self.state = self.DONE
            self.message = "Connected to %s!" % name
            # Save for auto-reconnect
            self.settings["bt_device_mac"] = mac
            self.settings["bt_device_name"] = name
        else:
            self.state = self.ERROR
            self.error_msg = "Connection failed.\nTry putting speaker in\npairing mode again."

    def handle_input(self, button, event_type, pager):
        BTN_A = 0x10
        BTN_B = 0x20
        BTN_UP = 0x01
        BTN_DOWN = 0x02

        if event_type != 1:
            return None

        if self.state == self.SELECT_DEVICE:
            if button == BTN_UP:
                self.selected = max(0, self.selected - 1)
                if self.selected < self.scroll_offset:
                    self.scroll_offset = self.selected
            elif button == BTN_DOWN:
                self.selected = min(len(self.devices) - 1, self.selected + 1)
                if self.selected >= self.scroll_offset + self.visible_count:
                    self.scroll_offset = (self.selected -
                                          self.visible_count + 1)
            elif button == BTN_A:
                if self.devices:
                    mac, name = self.devices[self.selected]
                    # Strip tags
                    name = name.replace(" [paired]", "").replace(" [saved]", "")
                    # Defer to update() so "Pairing..." draws first
                    self.state = self.PAIR
                    self.message = "Pairing with %s..." % name
                    self._pair_pending = (mac, name)
            elif button == BTN_B:
                return self.return_screen

        elif self.state == self.SCAN:
            if button == BTN_B:
                return self.return_screen
            elif button == BTN_A:
                self._start_scan()

        elif self.state == self.ERROR:
            if button == BTN_A:
                # Retry
                self.state = self.CHECK_ADAPTER
                self._check_adapter()
            elif button == BTN_B:
                return self.return_screen

        elif self.state == self.DONE:
            if button == BTN_A or button == BTN_B:
                return self.return_screen

        elif self.state == self.CHECK_ADAPTER:
            if button == BTN_B:
                return self.return_screen

        return None

    def update(self, status):
        """Called each frame — advance async operations."""
        if self._pair_pending:
            mac, name = self._pair_pending
            self._pair_pending = None
            self._pair_device(mac, name)
        elif self.state == self.SCAN:
            self._poll_scan()

    def draw(self, pager, skin):
        c = skin.color
        pager.clear(c("bg"))

        # Header
        pager.fill_rect(0, 0, SCREEN_W, 22, c("title_bar_bg"))
        state_label = self.STATE_LABELS[self.state]
        pager.draw_ttf(6, 2, "Bluetooth: " + state_label,
                      c("title_bar_text"), FONT_PATH, skin.font("title"))

        if self.state == self.SELECT_DEVICE:
            self._draw_device_list(pager, skin)
        elif self.state == self.ERROR:
            self._draw_error(pager, skin)
        elif self.state == self.DONE:
            self._draw_done(pager, skin)
        else:
            self._draw_status(pager, skin)

        # Bottom hint bar
        self._draw_hints(pager, skin)

    def _draw_device_list(self, pager, skin):
        c = skin.color
        y = 26
        for i in range(self.visible_count):
            idx = self.scroll_offset + i
            if idx >= len(self.devices):
                break

            mac, name = self.devices[idx]
            is_sel = (idx == self.selected)

            if is_sel:
                pager.fill_rect(0, y, SCREEN_W, self.line_height - 1,
                               c("track_highlight"))

            tc = c("track_highlight_text") if is_sel else c("track_text")
            display = "%s  %s" % (name, mac)
            # Truncate
            max_w = SCREEN_W - 16
            while (pager.ttf_width(display, FONT_PATH, self.font_size) >
                   max_w and len(display) > 5):
                display = display[:-1]
            pager.draw_ttf(4, y + 1, display, tc, FONT_PATH, self.font_size)
            y += self.line_height

    def _draw_error(self, pager, skin):
        c = skin.color
        y = 50
        for line in self.error_msg.split("\n"):
            pager.draw_ttf(20, y, line, c("warning"), FONT_PATH, 14)
            y += 20

    def _draw_done(self, pager, skin):
        c = skin.color
        pager.draw_ttf(20, 60, self.message, c("accent"), FONT_PATH, 16)

        mac = self.settings.get("bt_device_mac", "")
        if mac:
            pager.draw_ttf(20, 90, mac, c("text_dim"), FONT_PATH, 12)

    def _draw_status(self, pager, skin):
        c = skin.color
        pager.draw_ttf(20, 60, self.message, c("text"), FONT_PATH, 14)

        # Scanning animation
        if self.state == self.SCAN:
            elapsed = time.time() - self._scan_start
            bar_w = int((SCREEN_W - 40) * min(1.0,
                        elapsed / self._scan_duration))
            pager.fill_rect(20, 100, SCREEN_W - 40, 6, c("progress_bg"))
            if bar_w > 0:
                pager.fill_rect(20, 100, bar_w, 6, c("progress_fill"))

    def _draw_hints(self, pager, skin):
        c = skin.color
        y = SCREEN_H - 16

        if self.state == self.SELECT_DEVICE:
            hints = "[A] Select  [B] Back  [UP/DN] Navigate"
        elif self.state == self.SCAN:
            hints = "[A] Rescan  [B] Back"
        elif self.state == self.ERROR:
            hints = "[A] Retry  [B] Back"
        elif self.state == self.DONE:
            hints = "[A/B] Done"
        else:
            hints = "[B] Back"

        pager.draw_ttf(8, y, hints, c("text_dim"), FONT_PATH, 10)
