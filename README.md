# PagerAmp

A Winamp-inspired Bluetooth music player for the [WiFi Pineapple Pager](https://shop.hak5.org/products/wifi-pineapple-pager) by Hak5.

![PagerAmp UI](https://raw.githubusercontent.com/brainphreak/pineapple_pager_pageramp_mp3_player/main/skins/classic_bg.png)

## Features

- MP3 playback via Bluetooth A2DP (speaker/headphones)
- Skinnable Winamp-style UI with 3 built-in skins (Classic, Modern, Retro)
- Web upload interface at `http://172.16.52.1:1337` for adding music from your phone/laptop
- Playlist management with shuffle and repeat modes
- Bluetooth device pairing and management from the on-screen menu
- Auto-reconnect to previously paired Bluetooth speakers
- Low CPU usage (~5%) using mpg123 as the audio backend

## Requirements

- WiFi Pineapple Pager (PAGERCTL library is bundled in `lib/`)
- External USB Bluetooth adapter (CSR8510 or RTL8761B recommended — the built-in MT7961 has a broken ACL data path)
- Internet connection for first-run dependency installation (python3, mpg123)

## Installation

1. Copy this entire repo to the Pager:

```bash
scp -r . root@172.16.52.1:/root/payloads/user/utilities/pageramp/
```

2. On the Pager, the payload will appear in the utilities menu. On first launch, it will prompt to install dependencies (python3 and mpg123) — press GREEN to install.

3. Plug in a USB Bluetooth adapter and pair your speaker from the Bluetooth menu.

## Usage

### Controls

| Button | Action |
|--------|--------|
| GREEN | Start / Confirm / Play-Pause |
| RED | Back / Exit |
| UP / DOWN | Navigate menus / Adjust volume |
| LEFT / RIGHT | Seek backward/forward (on now playing screen) |

### Screens

- **Now Playing** — Shows current track, seek bar, volume, transport controls
- **Playlist** — Browse and select tracks from the current playlist
- **File Browser** — Browse `/mmc/music/` and load songs
- **Bluetooth** — Scan, pair, and connect Bluetooth audio devices
- **Skin Select** — Switch between Classic, Modern, and Retro skins

### Web Upload

With PagerAmp running, navigate to `http://172.16.52.1:1337` from any device on the Pager's network to upload MP3 files. Uploaded files appear in the file browser immediately.

## Known Issues

- **Bluetooth disconnect between tracks with mixed sample rates:** If your music library contains MP3 files with different sample rates (e.g., 44100 Hz and 48000 Hz), the speaker may briefly announce "disconnected" when switching between them. This is because the ALSA audio device must be reopened with new parameters when the sample rate changes. To avoid this, ensure all MP3 files use the same sample rate (44100 Hz recommended). You can re-encode with: `ffmpeg -i input.mp3 -ar 44100 -b:a 192k output.mp3`

## Bluetooth Setup

The built-in MediaTek MT7961 Bluetooth on the Pineapple Pager has a firmware bug that prevents audio streaming (ACL data path is broken). You need an external USB Bluetooth adapter:

- **CSR8510** — Works out of the box
- **RTL8761B** — Firmware files are bundled in `firmware/rtl_bt/`

Plug in the adapter before launching PagerAmp. The payload automatically detects the external adapter and skips the built-in MT7961.

## Architecture

PagerAmp uses **mpg123** in remote mode (`--remote`) as the audio backend, sending decoded audio directly to a Bluetooth speaker via **BlueALSA**. The Python UI communicates with mpg123 through stdin/stdout — no FIFOs or custom daemons needed.

```
┌─────────────┐    stdin     ┌────────┐    ALSA     ┌───────────┐    A2DP    ┌─────────┐
│ pageramp.py │ ──────────── │ mpg123 │ ──────────── │ bluealsad │ ────────── │ Speaker │
│   (GUI)     │   commands   │  (-R)  │   PCM data  │ (BlueALSA)│  Bluetooth │         │
└─────────────┘    stdout    └────────┘             └───────────┘           └─────────┘
                   status
```

## Project Structure

```
pageramp/                    # Clone/copy to /root/payloads/user/utilities/pageramp/
├── payload.sh               # Entry point (bash launcher)
├── pageramp.py              # Main Python application
├── player/
│   ├── client.py            # mpg123 remote-mode client
│   └── playlist.py          # Playlist manager
├── ui/
│   ├── screens.py           # UI screens (now playing, playlist, file browser, etc.)
│   ├── widgets.py           # UI widgets (buttons, sliders, lists)
│   ├── bluetooth.py         # Bluetooth pairing/connection screen
│   └── skin.py              # Skin loader
├── web/
│   ├── upload_server.py     # HTTP upload server (port 1337)
│   └── templates/           # HTML templates
├── bin/
│   ├── bluealsad            # BlueALSA daemon (MIPS binary)
│   ├── mpg123               # MP3 decoder/player (MIPS binary)
│   └── aplay                # ALSA audio player (MIPS binary)
├── bt/lib/                  # BlueALSA + ALSA shared libraries
├── lib/                     # pagerctl library (libpagerctl.so + pagerctl.py)
├── config/                  # ALSA and D-Bus configuration
├── skins/                   # Skin assets (backgrounds, buttons, knobs)
├── fonts/                   # DejaVuSansMono.ttf
├── firmware/rtl_bt/         # RTL8761B Bluetooth firmware
├── data/                    # Runtime data (settings.json, created at runtime)
├── music/                   # Default demo tracks + user uploads
└── src/                     # Development tools (not needed on the Pager)
```

## Author

**brAinphreAk**

## License

MIT
