"""
Mpg123 remote-mode client — wraps mpg123 --remote for audio playback.

Sends commands via stdin, reads status from stdout.
Replaces the old FIFO-based PagerAmpClient (pagerampd is no longer used).
"""

import os
import subprocess
import fcntl
import errno


class Mpg123Client:
    """Non-blocking mpg123 --remote client."""

    def __init__(self, script_dir=None):
        self._proc = None
        self._buf = ""
        self._script_dir = script_dir or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))
        self._last_status = {
            "state": "stopped",
            "file": "",
            "pos": 0.0,
            "dur": 0.0,
            "vol": 80,
            "rate": 0,
        }
        self._volume = 80
        self._was_playing = False
        self._track_finished = False
        self._manual_stop = False

    def start(self):
        """Launch mpg123 in remote mode."""
        env = dict(os.environ)
        bt_lib = os.path.join(self._script_dir, "bt", "lib")
        env.setdefault("ALSA_PLUGIN_DIR", bt_lib)
        env.setdefault("ALSA_CONFIG_PATH",
                       os.path.join(self._script_dir, "config", "asound.conf"))
        # mpg123 output module directory (output_alsa.so)
        env.setdefault("MPG123_MODDIR",
                       os.path.join(self._script_dir, "lib", "mpg123"))
        # Ensure our libs are on LD_LIBRARY_PATH
        ld = env.get("LD_LIBRARY_PATH", "")
        our_ld = "%s:%s/lib" % (bt_lib, self._script_dir)
        if our_ld not in ld:
            env["LD_LIBRARY_PATH"] = our_ld + (":" + ld if ld else "")

        mpg123 = os.path.join(self._script_dir, "bin", "mpg123")
        cmd = [mpg123, "-R", "--stereo", "-a", "btmix"]
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        # Set stdout to non-blocking
        fd = self._proc.stdout.fileno()
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    def restart(self):
        """Kill and restart mpg123 (e.g. after BT reconnect)."""
        self.cleanup()
        self._buf = ""
        self._was_playing = False
        self._track_finished = False
        self.start()

    def _ensure_running(self):
        """Restart mpg123 if it died."""
        if not self._proc or self._proc.poll() is not None:
            # Reap zombie if any
            if self._proc:
                try:
                    self._proc.wait(timeout=0)
                except Exception:
                    pass
                self._proc = None
            self._buf = ""
            self.start()

    def _send(self, cmd):
        """Send a command to mpg123 stdin."""
        self._ensure_running()
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.write((cmd + "\n").encode())
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

    def play(self, path):
        self._track_finished = False
        self._manual_stop = False
        self._was_playing = False
        self._send("LOAD " + path)

    def pause(self):
        self._send("PAUSE")

    def resume(self):
        self._send("PAUSE")

    def toggle(self):
        self._send("PAUSE")

    def stop(self):
        self._manual_stop = True
        self._send("STOP")

    def seek(self, value):
        """Seek to absolute position in seconds."""
        self._send("JUMP %ds" % int(value))

    def seek_relative(self, offset):
        """Seek relative to current position in seconds."""
        if offset >= 0:
            self._send("JUMP +%ds" % int(offset))
        else:
            self._send("JUMP %ds" % int(offset))

    def set_volume(self, vol):
        vol = max(0, min(100, int(vol)))
        self._volume = vol
        self._send("VOLUME %d" % vol)
        self._last_status["vol"] = vol

    def adjust_volume(self, delta):
        self.set_volume(self._volume + delta)

    def poll_status(self):
        """Non-blocking read and parse of mpg123 output. Returns status dict."""
        if not self._proc or self._proc.poll() is not None:
            return self._last_status

        try:
            data = self._proc.stdout.read(4096)
            if data:
                self._buf += data.decode("utf-8", errors="replace")
        except (IOError, OSError) as e:
            if e.errno != errno.EAGAIN:
                pass

        # Parse complete lines
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.strip()
            if line:
                self._parse_line(line)

        return self._last_status

    def _parse_line(self, line):
        """Parse a single mpg123 output line."""
        if line.startswith("@F "):
            # @F <current_frame> <frames_left> <current_secs> <secs_left>
            parts = line.split()
            if len(parts) >= 5:
                try:
                    pos = float(parts[3])
                    left = float(parts[4])
                    self._last_status["pos"] = pos
                    self._last_status["dur"] = pos + left
                    self._last_status["state"] = "playing"
                    self._was_playing = True
                    self._track_finished = False
                except (ValueError, IndexError):
                    pass

        elif line.startswith("@P "):
            code = line[3:].strip()
            if code == "0":
                self._last_status["state"] = "stopped"
                if self._was_playing:
                    self._track_finished = True
                    self._was_playing = False
            elif code == "1":
                self._last_status["state"] = "paused"
            elif code == "2":
                self._last_status["state"] = "playing"
                self._was_playing = True
                self._track_finished = False

        elif line.startswith("@S "):
            # @S <mpeg_type> <layer> <samplerate> ...
            parts = line.split()
            if len(parts) >= 4:
                try:
                    self._last_status["rate"] = int(parts[3])
                except (ValueError, IndexError):
                    pass

        elif line.startswith("@I "):
            info = line[3:].strip()
            if not info.startswith("ID3:"):
                self._last_status["file"] = info

    @property
    def track_finished(self):
        """True if the current track finished playing naturally."""
        return self._track_finished

    def clear_track_finished(self):
        """Clear the track_finished flag after handling auto-advance."""
        self._track_finished = False

    @property
    def status(self):
        """Latest cached status dict."""
        return self._last_status

    def quit(self):
        """Send QUIT and terminate mpg123."""
        self._send("QUIT")
        if self._proc:
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._proc.kill()
            self._proc = None

    def cleanup(self):
        """Clean up subprocess."""
        if self._proc:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except (subprocess.TimeoutExpired, OSError):
                try:
                    self._proc.kill()
                    self._proc.wait(timeout=1)
                except OSError:
                    pass
            self._proc = None
