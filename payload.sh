#!/bin/bash
# Title: PagerAmp
# Description: Winamp-inspired Bluetooth music player with skinnable UI, playlist management, and web upload
# Author: brAinphreAk
# Version: 1.0
# Category: Utilities
# Library: libpagerctl.so (pagerctl)

# Payload directory (standard Pager installation path)
PAYLOAD_DIR="/root/payloads/user/utilities/pageramp"
DATA_DIR="$PAYLOAD_DIR/data"
NEXT_PAYLOAD_FILE="$DATA_DIR/.next_payload"

cd "$PAYLOAD_DIR" || {
    LOG "red" "ERROR: $PAYLOAD_DIR not found"
    exit 1
}

# ===================================================
# 1. Find pagerctl dependencies
# ===================================================
PAGERCTL_FOUND=false
PAGERCTL_SEARCH_PATHS=(
    "$PAYLOAD_DIR/lib"
    "$PAYLOAD_DIR"
    "/mmc/root/payloads/user/utilities/PAGERCTL"
)

for dir in "${PAGERCTL_SEARCH_PATHS[@]}"; do
    if [ -f "$dir/libpagerctl.so" ] && [ -f "$dir/pagerctl.py" ]; then
        PAGERCTL_DIR="$dir"
        PAGERCTL_FOUND=true
        break
    fi
done

if [ "$PAGERCTL_FOUND" = false ]; then
    LOG ""
    LOG "red" "=== MISSING DEPENDENCY ==="
    LOG ""
    LOG "red" "libpagerctl.so and pagerctl.py not found!"
    LOG ""
    LOG "Install PAGERCTL payload or copy files to:"
    LOG "  $PAYLOAD_DIR/lib/"
    LOG ""
    LOG "Press any button to exit..."
    WAIT_FOR_INPUT >/dev/null 2>&1
    exit 1
fi

# Copy pagerctl to local lib if from elsewhere
if [ "$PAGERCTL_DIR" != "$PAYLOAD_DIR/lib" ]; then
    mkdir -p "$PAYLOAD_DIR/lib" 2>/dev/null
    cp "$PAGERCTL_DIR/libpagerctl.so" "$PAYLOAD_DIR/lib/" 2>/dev/null
    cp "$PAGERCTL_DIR/pagerctl.py" "$PAYLOAD_DIR/lib/" 2>/dev/null
fi

# ===================================================
# 2. Set environment
# ===================================================
export PATH="$PAYLOAD_DIR/bin:/mmc/usr/bin:$PATH"
export PYTHONPATH="$PAYLOAD_DIR:$PAYLOAD_DIR/lib:${PYTHONPATH:-}"
export LD_LIBRARY_PATH="$PAYLOAD_DIR/lib:$PAYLOAD_DIR/bt/lib:/mmc/usr/lib:${LD_LIBRARY_PATH:-/usr/lib}"
export ALSA_PLUGIN_DIR="$PAYLOAD_DIR/bt/lib"
export ALSA_CONFIG_PATH="$PAYLOAD_DIR/config/asound.conf"

# ===================================================
# 3. Check Python3 and mpg123
# ===================================================
NEED_INSTALL=false
MISSING=""

if ! command -v python3 >/dev/null 2>&1; then
    NEED_INSTALL=true
    MISSING="python3 python3-ctypes"
elif ! python3 -c "import ctypes" 2>/dev/null; then
    NEED_INSTALL=true
    MISSING="python3-ctypes"
fi

if ! command -v mpg123 >/dev/null 2>&1; then
    NEED_INSTALL=true
    MISSING="$MISSING mpg123"
fi

# Ensure mpg123 library symlinks exist
for lib in libmpg123 libout123 libsyn123 libltdl libasound; do
    if [ ! -e "/usr/lib/${lib}.so" ] && ls /mmc/usr/lib/${lib}.so* >/dev/null 2>&1; then
        ln -sf /mmc/usr/lib/${lib}.so* /usr/lib/ 2>/dev/null
    fi
done

if [ "$NEED_INSTALL" = true ]; then
    LOG ""
    LOG "red" "=== MISSING DEPENDENCIES ==="
    LOG ""
    LOG "Required packages: $MISSING"
    LOG ""
    LOG "green" "GREEN = Install (requires internet)"
    LOG "red" "RED   = Exit"
    LOG ""

    while true; do
        BUTTON=$(WAIT_FOR_INPUT 2>/dev/null)
        case "$BUTTON" in
            "GREEN"|"A")
                LOG ""
                LOG "Updating package lists..."
                opkg update 2>&1 | while IFS= read -r line; do LOG "  $line"; done
                LOG ""
                LOG "Installing dependencies to MMC..."
                for pkg in $MISSING; do
                    LOG "  Installing $pkg..."
                    opkg -d mmc install --force-depends "$pkg" 2>&1 | while IFS= read -r line; do LOG "    $line"; done
                done
                # Create library symlinks
                for lib in libmpg123 libout123 libsyn123 libltdl libasound libpython3; do
                    ln -sf /mmc/usr/lib/${lib}.so* /usr/lib/ 2>/dev/null
                done
                LOG ""
                if command -v python3 >/dev/null 2>&1 && command -v mpg123 >/dev/null 2>&1; then
                    LOG "green" "Dependencies installed successfully!"
                    sleep 1
                else
                    LOG "red" "Installation failed. Check internet connection."
                    LOG ""
                    LOG "Press any button to exit..."
                    WAIT_FOR_INPUT >/dev/null 2>&1
                    exit 1
                fi
                break
                ;;
            "RED"|"B")
                exit 0
                ;;
        esac
    done
fi

# ===================================================
# 4. Kill any leftover PagerAmp processes from previous runs
# ===================================================
kill_pageramp_procs() {
    killall -q pageramp.py mpg123 upload_server.py 2>/dev/null
    sleep 1
    killall -q -9 pageramp.py mpg123 upload_server.py 2>/dev/null
}
kill_pageramp_procs

# ===================================================
# 5. Cleanup handler
# ===================================================
cleanup() {
    # Kill all PagerAmp processes
    killall -q pageramp.py mpg123 upload_server.py 2>/dev/null

    for pid in $WEB_PID; do
        [ -n "$pid" ] && kill "$pid" 2>/dev/null
    done

    # Kill bluealsad if we started it
    if [ "$BLUEALSAD_STARTED" = "1" ] && [ -n "$BLUEALSAD_PID" ]; then
        kill "$BLUEALSAD_PID" 2>/dev/null
    fi

    sleep 1

    # Force kill stragglers
    killall -q -9 pageramp.py mpg123 upload_server.py 2>/dev/null

    # Remove D-Bus config if we installed it
    if [ "$DBUS_INSTALLED" = "1" ]; then
        rm -f /etc/dbus-1/system.d/bluealsa.conf
    fi

    # Restart pager service if not running
    if ! pgrep -x pineapple >/dev/null; then
        /etc/init.d/pineapplepager start 2>/dev/null
    fi
}
trap cleanup EXIT

# ===================================================
# 6. Splash screen and start confirmation
# ===================================================
LOG ""
LOG "green" "PagerAmp - Music Player"
LOG "cyan" "Winamp-inspired BT audio player"
LOG ""
LOG "cyan" "Features:"
LOG "cyan" "  - MP3/WAV playback via Bluetooth"
LOG "cyan" "  - Skinnable Winamp-style UI"
LOG "cyan" "  - Web upload at http://172.16.52.1:1337"
LOG "cyan" "  - Playlist management"
LOG ""
LOG "green" "GREEN = Start"
LOG "red" "RED = Exit"
LOG ""

while true; do
    BUTTON=$(WAIT_FOR_INPUT 2>/dev/null)
    case "$BUTTON" in
        "GREEN"|"A")
            break
            ;;
        "RED"|"B")
            exit 0
            ;;
    esac
done

# ===================================================
# 7. Stop pager service and set up Bluetooth
# ===================================================
SPINNER_ID=$(START_SPINNER "Starting PagerAmp...")
/etc/init.d/pineapplepager stop 2>/dev/null
sleep 0.5
STOP_SPINNER "$SPINNER_ID" 2>/dev/null

BLUEALSAD_STARTED=0
BLUEALSAD_PID=""
DBUS_INSTALLED=0
HCI=""

# Install RTL8761B firmware if bundled
if [ -d "$PAYLOAD_DIR/firmware/rtl_bt" ] && [ ! -f /lib/firmware/rtl_bt/rtl8761b_fw.bin ]; then
    mkdir -p /lib/firmware/rtl_bt
    cp "$PAYLOAD_DIR/firmware/rtl_bt/"*.bin /lib/firmware/rtl_bt/ 2>/dev/null
fi

# Find USB BT adapter (skip built-in MT7961 MediaTek)
for h in hci0 hci1; do
    INFO=$(hciconfig -a "$h" 2>/dev/null)
    echo "$INFO" | grep -q "Bus: USB" || continue
    echo "$INFO" | grep -q "MediaTek" && continue
    HCI="$h"
    ADAPTER_MAC=$(echo "$INFO" | grep "BD Address" | awk '{print $3}')
    break
done

if [ -n "$HCI" ]; then
    hciconfig "$HCI" up 2>/dev/null
    hciconfig "$HCI" auth encrypt 2>/dev/null

    # Install D-Bus config for BlueALSA
    DBUS_CONF="/etc/dbus-1/system.d/bluealsa.conf"
    if [ ! -f "$DBUS_CONF" ]; then
        cp "$PAYLOAD_DIR/config/bluealsa-dbus.conf" "$DBUS_CONF"
        DBUS_INSTALLED=1
        if [ -x /etc/init.d/dbus ]; then
            /etc/init.d/dbus restart 2>/dev/null
            sleep 2
        fi
    fi

    # Start bluetoothd
    if ! pidof bluetoothd >/dev/null 2>&1; then
        bluetoothd -n &
        sleep 2
    fi
    if [ -n "$ADAPTER_MAC" ]; then
        bluetoothctl select "$ADAPTER_MAC" 2>/dev/null
    fi
    bluetoothctl power on 2>/dev/null
    bluetoothctl pairable on 2>/dev/null
    hciconfig "$HCI" name "Pineapple Pager" 2>/dev/null
    bluetoothctl system-alias "Pineapple Pager" 2>/dev/null

    # Start bluealsad
    if ! pidof bluealsad >/dev/null 2>&1; then
        "$PAYLOAD_DIR/bin/bluealsad" -i "$HCI" -p a2dp-source -p a2dp-sink -S &
        BLUEALSAD_PID=$!
        BLUEALSAD_STARTED=1
        sleep 3
        if ! kill -0 "$BLUEALSAD_PID" 2>/dev/null; then
            BLUEALSAD_STARTED=0
        fi
    else
        BLUEALSAD_PID=$(pidof bluealsad)
    fi

    # Auto-reconnect to saved device
    SAVED_MAC=$(python3 -c "
import json
try:
    d = json.load(open('$DATA_DIR/settings.json'))
    print(d.get('bt_device_mac', ''))
except: pass
" 2>/dev/null)

    if [ -n "$SAVED_MAC" ]; then
        bluetoothctl connect "$SAVED_MAC" 2>/dev/null &
        sleep 3
    fi
fi

# ===================================================
# 8. Prepare music directory
# ===================================================
mkdir -p /mmc/music

# Copy bundled demo track if music directory is empty
if [ -z "$(ls /mmc/music/*.mp3 2>/dev/null)" ]; then
    cp "$PAYLOAD_DIR/music/"*.mp3 /mmc/music/ 2>/dev/null
fi

# ===================================================
# 9. Start web upload server
# ===================================================
python3 "$PAYLOAD_DIR/web/upload_server.py" --port 1337 --dir /mmc/music &
WEB_PID=$!

# ===================================================
# 10. Main loop with handoff support
# ===================================================
mkdir -p "$DATA_DIR"

while true; do
    cd "$PAYLOAD_DIR"
    python3 pageramp.py
    EXIT_CODE=$?

    # Exit code 42 = hand off to another payload
    if [ "$EXIT_CODE" -eq 42 ] && [ -f "$NEXT_PAYLOAD_FILE" ]; then
        NEXT_SCRIPT=$(cat "$NEXT_PAYLOAD_FILE")
        rm -f "$NEXT_PAYLOAD_FILE"
        if [ -f "$NEXT_SCRIPT" ]; then
            bash "$NEXT_SCRIPT"
            [ $? -eq 42 ] && continue
        fi
    fi

    break
done

exit 0
