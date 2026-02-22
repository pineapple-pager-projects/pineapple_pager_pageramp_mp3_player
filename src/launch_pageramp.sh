#!/bin/bash
# Title: PagerAmp
# Requires: /root/payloads/user/utilities/pageramp
#
# Pagergotchi handoff launcher for PagerAmp.
# This script is discovered by Pagergotchi's launch_*.sh scanner.
# Exit code 42 returns control to Pagergotchi.

PAGERAMP_DIR="/root/payloads/user/utilities/pageramp"

if [ ! -f "$PAGERAMP_DIR/payload.sh" ]; then
    echo "[PagerAmp] Not installed at $PAGERAMP_DIR"
    exit 1
fi

cd "$PAGERAMP_DIR"
bash payload.sh
exit $?
