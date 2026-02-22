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
        self.hci_index = "0"
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
        self._pair_draw_wait = 0  # frames to wait before starting pair
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

    def _run_btmgmt(self, args, timeout=15):
        """Run btmgmt command with output to file.

        btmgmt hangs when stdout/stdin are pipes (its event loop needs
        real file descriptors). Redirect output to a temp file and
        inherit stdin from parent process.
        """
        outfile = "/tmp/pageramp_btmgmt.txt"
        try:
            # Shell handles redirection; Python doesn't pipe anything
            proc = subprocess.Popen(
                "btmgmt %s >%s 2>&1" % (args, outfile),
                shell=True,
            )
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        except Exception:
            return ""
        try:
            with open(outfile, "r") as f:
                return f.read().strip()
        except (IOError, OSError):
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
                # Extract HCI index for btmgmt
                self.hci_index = hci.replace("hci", "")
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
                # Power on in bluetoothd (required for bluetoothctl operations)
                if self.adapter_mac:
                    self._run("bluetoothctl select %s" % self.adapter_mac)
                self._run("bluetoothctl power on")
                self._run("bluetoothctl pairable on")
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

        Uses hcitool scan for reliable BR/EDR classic device discovery.
        CSR8510 dongles have MGMT issues where bluetoothctl scan misses
        BR/EDR devices, but hcitool's legacy HCI inquiry works fine.
        Output is captured to a temp file and read when the scan finishes.
        """
        self.message = "Scanning... Put device in pairing mode!"
        self.devices = []
        self._scan_start = time.time()
        self._scan_file = "/tmp/pageramp_bt_scan.txt"

        # Ensure bluetoothd is running
        if not self._run("pidof bluetoothd"):
            self._run("bluetoothd -n &", timeout=3)
            time.sleep(2)

        # hcitool scan captures output to file for _poll_scan to read
        subprocess.Popen(
            "hcitool -i %s scan --length=8 >%s 2>/dev/null" % (
                self.hci, self._scan_file),
            shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

    def _poll_scan(self):
        """Check scan results when scan timer expires."""
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

        # 2. Read hcitool scan results from temp file
        scan_file = getattr(self, "_scan_file", "/tmp/pageramp_bt_scan.txt")
        hci_output = ""
        try:
            with open(scan_file, "r") as f:
                hci_output = f.read()
        except (IOError, OSError):
            pass

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

    def _log(self, msg):
        """Append debug line to /tmp/pageramp_bt.log."""
        try:
            with open("/tmp/pageramp_bt.log", "a") as f:
                f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Bluetooth connection helpers
    # ------------------------------------------------------------------

    def _update_asound(self, mac):
        """Update asound.conf with device MAC for BlueALSA output."""
        asound_path = os.path.join(SCRIPT_DIR, "config", "asound.conf")
        if os.path.isfile(asound_path):
            self._run("sed -i 's/device \".*\"/device \"%s\"/' %s"
                      % (mac, asound_path))

    def _do_pair(self, mac):
        """Pair with btmgmt. Returns True on success.

        btmgmt pair works reliably on CSR8510 where bluetoothctl pair
        fails due to MGMT discovery cache issues.
        """
        args = ("--index %s pair -c NoInputNoOutput -t 0 %s"
                % (self.hci_index, mac))
        self._log("btmgmt pair: %s" % args)
        result = self._run_btmgmt(args, timeout=20)
        self._log("btmgmt result: [%s]" % result[:300])

        # Success indicators
        if "status 0x00" in result:
            time.sleep(2)
            return True

        # "Already Paired" is also fine
        if "Already Paired" in result:
            return True

        # Explicit failure
        if "failed" in result.lower():
            self._log("btmgmt pair FAILED")
            return False

        # Ambiguous — check bluetoothd state
        time.sleep(2)
        info = self._run("bluetoothctl info %s 2>/dev/null" % mac, timeout=5)
        return "Paired: yes" in info

    def _try_connect(self, mac):
        """Attempt bluetoothctl connect. Returns (connected, auth_fail)."""
        result = self._run("bluetoothctl connect %s 2>&1" % mac, timeout=15)
        self._log("connect result: [%s]" % result[:300])
        time.sleep(3)

        info = self._run("bluetoothctl info %s" % mac, timeout=5)
        connected = "Connected: yes" in info
        auth_fail = ("key-missing" in result or "AuthenticationFailed" in result
                     or "auth failed" in result.lower()
                     or "status 0x05" in result or "status 0x06" in result)
        self._log("connected=%s auth_fail=%s" % (connected, auth_fail))
        return connected, auth_fail

    def _remove_device(self, mac):
        """Remove a device from bluetoothd (clears stored bond/keys)."""
        self._log("removing device %s" % mac)
        self._run("bluetoothctl disconnect %s" % mac, timeout=3)
        time.sleep(0.5)
        self._run("bluetoothctl remove %s" % mac, timeout=5)
        time.sleep(1)

    def _pair_device(self, mac, name):
        """Connect to a Bluetooth device with robust error recovery.

        Handles three scenarios without requiring factory reset:

        1. Already paired + keys valid → connect directly (fast path)
        2. Already paired + keys stale → remove bond, re-pair, connect
        3. New device → pair with btmgmt, trust, connect

        Each step retries on failure. The device should be in pairing
        mode for new pairing; already-bonded devices just need to be on.
        """
        self._log("=== START pair_device mac=%s name=%s ===" % (mac, name))

        if self.adapter_mac:
            self._run("bluetoothctl select %s" % self.adapter_mac)

        # Prepare audio path early (asound.conf + bluealsad)
        self._update_asound(mac)
        self._ensure_bluealsad()

        # Check current device state in bluetoothd
        info = self._run("bluetoothctl info %s 2>/dev/null" % mac, timeout=5)
        already_paired = "Paired: yes" in info
        already_connected = "Connected: yes" in info
        self._log("state: paired=%s connected=%s" % (
            already_paired, already_connected))

        # ── Already connected ─────────────────────────────
        if already_connected:
            self._log("already connected — done")
            self._finish_connect(mac, name)
            return

        # ── Fast path: already paired → try connect ───────
        if already_paired:
            self.state = self.CONNECT
            self.message = "Connecting to %s..." % name

            connected, auth_fail = self._try_connect(mac)
            if connected:
                self._finish_connect(mac, name)
                return

            # Stale keys — clear bond and fall through to fresh pair
            self._log("paired but connect failed (auth_fail=%s) — "
                      "clearing stale bond" % auth_fail)
            self._remove_device(mac)
            # Fall through to fresh pair below

        # ── Fresh pair with btmgmt ────────────────────────
        self.state = self.PAIR
        self.message = "Pairing with %s..." % name

        # Remove any unpaired leftover entry (can have stale cache)
        devs = self._run("bluetoothctl devices 2>/dev/null")
        if "Device %s" % mac in devs:
            self._log("removing leftover unpaired entry")
            self._run("bluetoothctl remove %s" % mac, timeout=5)
            time.sleep(1)

        # Try pairing up to 2 times
        paired = False
        for pair_attempt in range(2):
            self._log("pair attempt %d" % (pair_attempt + 1))
            if self._do_pair(mac):
                paired = True
                break
            # Brief pause before retry
            self._log("pair attempt %d failed" % (pair_attempt + 1))
            time.sleep(2)

        if not paired:
            self.state = self.ERROR
            self.error_msg = ("Pairing failed.\nPut device in pairing\n"
                              "mode and try again.")
            self._log("PAIR FAILED after retries")
            return

        # Trust the device so it can auto-reconnect
        self._run("bluetoothctl trust %s" % mac, timeout=5)
        time.sleep(0.5)

        # ── Connect after fresh pair ──────────────────────
        self.state = self.CONNECT
        self.message = "Connecting to %s..." % name

        for attempt in range(3):
            self._log("post-pair connect attempt %d" % (attempt + 1))
            connected, auth_fail = self._try_connect(mac)

            if connected:
                self._finish_connect(mac, name)
                return

            if auth_fail and attempt < 2:
                # Keys went stale even after fresh pair (rare but possible
                # if device stored a different key). Remove and re-pair.
                self._log("auth fail after fresh pair — re-pair")
                self._remove_device(mac)
                if self._do_pair(mac):
                    self._run("bluetoothctl trust %s" % mac, timeout=5)
                    time.sleep(0.5)
            else:
                # Non-auth failure — just wait and retry
                time.sleep(2)

        self.state = self.ERROR
        self.error_msg = ("Connection failed.\nPut device in pairing\n"
                          "mode and try again.")
        self._log("FINAL: connection failed after all attempts")

    def _finish_connect(self, mac, name):
        """Finalize a successful connection."""
        self.state = self.DONE
        self.message = "Connected to %s!" % name
        self.settings["bt_device_mac"] = mac
        self.settings["bt_device_name"] = name
        self._log("SUCCESS: connected to %s (%s)" % (name, mac))

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
            # Wait one frame so "Pairing..." message draws before blocking
            self._pair_draw_wait += 1
            if self._pair_draw_wait < 2:
                return
            mac, name = self._pair_pending
            self._pair_pending = None
            self._pair_draw_wait = 0
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
