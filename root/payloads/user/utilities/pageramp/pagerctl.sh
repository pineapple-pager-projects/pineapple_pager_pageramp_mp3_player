#!/bin/sh
# Title: PagerAmp
# Description: Winamp-inspired Bluetooth music player with skinnable UI, playlist management, and web upload
# Author: brAinphreAk
# Version: 1.0
# Category: Utilities
# Library: libpagerctl.so (pagerctl)
#
# Pagerctl-native launcher. pagerctl_home has already torn the pager
# down and stopped pineapplepager — we skip the duckyscript splash
# and the pineapplepager stop/start. Bluetooth setup, bluealsad, and
# the web upload server are preserved from payload.sh.

PAYLOAD_DIR="/root/payloads/user/utilities/pageramp"
DATA_DIR="$PAYLOAD_DIR/data"
NEXT_PAYLOAD_FILE="$DATA_DIR/.next_payload"

cd "$PAYLOAD_DIR" || exit 1

export PATH="$PAYLOAD_DIR/bin:/mmc/usr/bin:$PATH"
export PYTHONPATH="$PAYLOAD_DIR:$PAYLOAD_DIR/lib:${PYTHONPATH:-}"
export LD_LIBRARY_PATH="$PAYLOAD_DIR/lib:$PAYLOAD_DIR/bt/lib:/mmc/usr/lib:${LD_LIBRARY_PATH:-/usr/lib}"
export ALSA_PLUGIN_DIR="$PAYLOAD_DIR/bt/lib"
export ALSA_CONFIG_PATH="$PAYLOAD_DIR/config/asound.conf"

command -v python3 >/dev/null 2>&1 || exit 1
python3 -c "import ctypes" 2>/dev/null || exit 1
command -v mpg123 >/dev/null 2>&1 || exit 1

# Ensure mpg123 library symlinks exist
for lib in libmpg123 libout123 libsyn123 libltdl libasound; do
    if [ ! -e "/usr/lib/${lib}.so" ] && ls /mmc/usr/lib/${lib}.so* >/dev/null 2>&1; then
        ln -sf /mmc/usr/lib/${lib}.so* /usr/lib/ 2>/dev/null
    fi
done

# Kill any leftover PagerAmp processes from previous runs
killall -q pageramp.py mpg123 upload_server.py 2>/dev/null
sleep 1
killall -q -9 pageramp.py mpg123 upload_server.py 2>/dev/null

BLUEALSAD_STARTED=0
BLUEALSAD_PID=""
DBUS_INSTALLED=0
HCI=""
WEB_PID=""

cleanup() {
    killall -q pageramp.py mpg123 2>/dev/null
    [ -n "$WEB_PID" ] && kill "$WEB_PID" 2>/dev/null
    bluetoothctl power off 2>/dev/null
    for h in hci0 hci1; do
        hciconfig "$h" down 2>/dev/null
    done
    killall -q bluealsad 2>/dev/null
    sleep 1
    killall -q -9 pageramp.py mpg123 bluealsad 2>/dev/null
    [ -n "$WEB_PID" ] && kill -9 "$WEB_PID" 2>/dev/null
    if [ "$DBUS_INSTALLED" = "1" ]; then
        rm -f /etc/dbus-1/system.d/bluealsa.conf
    fi
}
trap cleanup EXIT TERM INT HUP

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
    if ! hciconfig "$HCI" up 2>/dev/null; then
        hciconfig "$HCI" down 2>/dev/null
        hciconfig "$HCI" reset 2>/dev/null
        sleep 2
        hciconfig "$HCI" up 2>/dev/null
    fi
    hciconfig "$HCI" auth encrypt 2>/dev/null

    DBUS_CONF="/etc/dbus-1/system.d/bluealsa.conf"
    if [ ! -f "$DBUS_CONF" ]; then
        cp "$PAYLOAD_DIR/config/bluealsa-dbus.conf" "$DBUS_CONF"
        DBUS_INSTALLED=1
        if [ -x /etc/init.d/dbus ]; then
            /etc/init.d/dbus restart 2>/dev/null
        else
            killall dbus-daemon 2>/dev/null
            sleep 1
            dbus-daemon --system 2>/dev/null
        fi
        sleep 2
    fi

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

    if ! pidof bluealsad >/dev/null 2>&1; then
        "$PAYLOAD_DIR/bin/bluealsad" -i "$HCI" -p a2dp-source -p a2dp-sink --keep-alive=30 -S &
        BLUEALSAD_PID=$!
        BLUEALSAD_STARTED=1
        sleep 3
        if ! kill -0 "$BLUEALSAD_PID" 2>/dev/null; then
            BLUEALSAD_STARTED=0
        fi
    else
        BLUEALSAD_PID=$(pidof bluealsad)
    fi

    SAVED_MAC=$(python3 -c "
import json
try:
    d = json.load(open('$DATA_DIR/settings.json'))
    print(d.get('bt_device_mac', ''))
except: pass
" 2>/dev/null)

    if [ -n "$SAVED_MAC" ]; then
        INFO=$(bluetoothctl info "$SAVED_MAC" 2>/dev/null)
        if echo "$INFO" | grep -q "AddressType: le"; then
            bluetoothctl remove "$SAVED_MAC" 2>/dev/null
            sleep 1
        fi
        hcitool -i "$HCI" cc "$SAVED_MAC" 2>/dev/null
        sleep 1
        bluetoothctl select "$ADAPTER_MAC" 2>/dev/null
        bluetoothctl trust "$SAVED_MAC" 2>/dev/null
        bluetoothctl connect "$SAVED_MAC" 2>/dev/null &
        sleep 5
    fi
fi

MMC_MUSIC="/mmc/root/payloads/user/utilities/pageramp/music"
mkdir -p "$MMC_MUSIC"
if [ -z "$(ls "$MMC_MUSIC"/*.mp3 2>/dev/null)" ]; then
    cp "$PAYLOAD_DIR/music/"*.mp3 "$MMC_MUSIC/" 2>/dev/null
fi

python3 "$PAYLOAD_DIR/web/upload_server.py" --port 1337 --dir "$MMC_MUSIC" &
WEB_PID=$!

mkdir -p "$DATA_DIR"
CRASH_LOG="/tmp/pageramp_crash.log"

while true; do
    cd "$PAYLOAD_DIR"
    python3 pageramp.py 2>"$CRASH_LOG"
    EXIT_CODE=$?

    if [ "$EXIT_CODE" -eq 42 ] && [ -f "$NEXT_PAYLOAD_FILE" ]; then
        NEXT_SCRIPT=$(cat "$NEXT_PAYLOAD_FILE")
        rm -f "$NEXT_PAYLOAD_FILE"
        if [ -f "$NEXT_SCRIPT" ]; then
            sh "$NEXT_SCRIPT"
            [ $? -eq 42 ] && continue
        fi
    fi
    break
done

exit 0
