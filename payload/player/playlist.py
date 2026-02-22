"""
Playlist management — M3U parsing, directory scanning, shuffle, repeat.
"""

import os
import random

MUSIC_DIR = "/mmc/music"
SUPPORTED_EXT = (".mp3", ".wav")


class Playlist:
    """Manages a list of tracks with shuffle and repeat modes."""

    REPEAT_OFF = 0
    REPEAT_ALL = 1
    REPEAT_ONE = 2

    def __init__(self):
        self.tracks = []        # list of absolute paths
        self.order = []         # playback order (indices into tracks)
        self.position = 0       # current index in order
        self.shuffle = False
        self.repeat = self.REPEAT_OFF

    def clear(self):
        self.tracks = []
        self.order = []
        self.position = 0

    def load_m3u(self, path):
        """Load playlist from .m3u file."""
        self.tracks = []
        base_dir = os.path.dirname(path)
        try:
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    # Resolve relative paths
                    if not os.path.isabs(line):
                        line = os.path.join(base_dir, line)
                    if os.path.isfile(line):
                        self.tracks.append(line)
        except (IOError, OSError):
            return False
        self._rebuild_order()
        return len(self.tracks) > 0

    def load_directory(self, path=None):
        """Scan directory for music files."""
        if path is None:
            path = MUSIC_DIR
        self.tracks = []
        if not os.path.isdir(path):
            return False
        for name in sorted(os.listdir(path)):
            if name.lower().endswith(SUPPORTED_EXT):
                self.tracks.append(os.path.join(path, name))
        self._rebuild_order()
        return len(self.tracks) > 0

    def load_files(self, files):
        """Load from a list of file paths."""
        self.tracks = list(files)
        self._rebuild_order()

    def add(self, path):
        """Add a track to the end."""
        self.tracks.append(path)
        self.order.append(len(self.tracks) - 1)

    def _rebuild_order(self):
        """Rebuild playback order based on shuffle setting."""
        self.order = list(range(len(self.tracks)))
        if self.shuffle:
            random.shuffle(self.order)
        self.position = 0

    def set_shuffle(self, enabled):
        """Toggle shuffle, preserving current track if possible."""
        if self.shuffle == enabled:
            return
        current_track = self.current_track_index()
        self.shuffle = enabled
        self._rebuild_order()
        # Put current track at current position
        if current_track is not None and current_track in self.order:
            idx = self.order.index(current_track)
            self.order[idx], self.order[self.position] = \
                self.order[self.position], self.order[idx]

    def cycle_repeat(self):
        """Cycle through repeat modes: off → all → one → off."""
        self.repeat = (self.repeat + 1) % 3
        return self.repeat

    def current_track_index(self):
        """Get the real track index for current position."""
        if not self.order or self.position >= len(self.order):
            return None
        return self.order[self.position]

    def current_track(self):
        """Get the path of the current track."""
        idx = self.current_track_index()
        if idx is None:
            return None
        return self.tracks[idx]

    def current_name(self):
        """Get display name of current track (filename without extension)."""
        track = self.current_track()
        if not track:
            return ""
        name = os.path.basename(track)
        name, _ = os.path.splitext(name)
        return name.replace("_", " ").replace("-", " - ")

    def next(self):
        """Advance to next track. Returns path or None if end."""
        if not self.order:
            return None

        if self.repeat == self.REPEAT_ONE:
            return self.current_track()

        self.position += 1
        if self.position >= len(self.order):
            if self.repeat == self.REPEAT_ALL:
                self.position = 0
                if self.shuffle:
                    self._rebuild_order()
            else:
                self.position = len(self.order) - 1  # stay on last
                return None

        return self.current_track()

    def prev(self):
        """Go to previous track. Returns path or None."""
        if not self.order:
            return None
        self.position -= 1
        if self.position < 0:
            if self.repeat == self.REPEAT_ALL:
                self.position = len(self.order) - 1
            else:
                self.position = 0
        return self.current_track()

    def jump_to(self, index):
        """Jump to a specific track index (in original order)."""
        if index < 0 or index >= len(self.tracks):
            return None
        # Find this track in playback order
        if index in self.order:
            self.position = self.order.index(index)
        else:
            self.position = 0
        return self.tracks[index]

    def jump_to_order(self, pos):
        """Jump to a position in the playback order."""
        if pos < 0 or pos >= len(self.order):
            return None
        self.position = pos
        return self.current_track()

    @property
    def length(self):
        return len(self.tracks)

    @property
    def is_empty(self):
        return len(self.tracks) == 0

    def track_name(self, index):
        """Get display name for track at given index."""
        if index < 0 or index >= len(self.tracks):
            return ""
        name = os.path.basename(self.tracks[index])
        name, _ = os.path.splitext(name)
        return name.replace("_", " ").replace("-", " - ")

    def export_m3u(self, path):
        """Save playlist to .m3u file."""
        try:
            with open(path, "w") as f:
                f.write("#EXTM3U\n")
                for track in self.tracks:
                    f.write(track + "\n")
            return True
        except (IOError, OSError):
            return False

    @property
    def repeat_label(self):
        return ("Off", "All", "One")[self.repeat]
