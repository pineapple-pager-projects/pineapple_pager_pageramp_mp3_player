#!/bin/bash
# Cross-compile pagerampd for MIPS (WiFi Pineapple Pager)
# Uses the OpenWrt SDK Docker container
#
# Usage: bash build_pagerampd.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="/tmp/pagerampd-build"
DOCKER_IMAGE="openwrt/sdk:mipsel_24kc-22.03.5"

echo "=== Building pagerampd for MIPS ==="

# Create build directory
mkdir -p "$BUILD_DIR"
cp "$SCRIPT_DIR/pagerampd.c" "$BUILD_DIR/"

# Download minimp3 header if not present
if [ ! -f "$BUILD_DIR/minimp3.h" ]; then
    echo "[+] Downloading minimp3.h..."
    curl -sL "https://raw.githubusercontent.com/lieff/minimp3/master/minimp3.h" \
        -o "$BUILD_DIR/minimp3.h"
fi

echo "[+] Cross-compiling pagerampd..."

docker run --rm \
    -v "$BUILD_DIR:/tmp/pagerampd-build" \
    "$DOCKER_IMAGE" \
    sh -c '
        export PATH=/builder/staging_dir/toolchain-mipsel_24kc_gcc-11.2.0_musl/bin:$PATH
        export STAGING_DIR=/builder/staging_dir
        CC=mipsel-openwrt-linux-musl-gcc
        STRIP=mipsel-openwrt-linux-musl-strip

        cd /tmp/pagerampd-build

        echo "[+] Compiling..."
        $CC -O2 -static \
            -DMINIMP3_NO_SIMD \
            -DMINIMP3_ONLY_MP3 \
            -o pagerampd pagerampd.c -lm

        echo "[+] Stripping..."
        $STRIP pagerampd

        echo "[+] Done!"
        ls -la pagerampd
        file pagerampd
    '

# Copy result to bin/
cp "$BUILD_DIR/pagerampd" "$SCRIPT_DIR/bin/pagerampd"

echo ""
echo "[+] Binary copied to bin/pagerampd"
ls -la "$SCRIPT_DIR/bin/pagerampd"
file "$SCRIPT_DIR/bin/pagerampd"
