#!/usr/bin/env python3
"""Generate PagerAmp skin background PNGs (480x222 RGB565-friendly)."""

import os
from PIL import Image, ImageDraw

W, H = 480, 222
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "skins")
DOWNLOADS = os.path.expanduser("~/Downloads")


def gen_classic():
    """Winamp Classic — real Winamp screenshot with composited button sprites."""
    base_path = os.path.join(DOWNLOADS, "winamp.png")
    if not os.path.exists(base_path):
        print("  ERROR: %s not found" % base_path)
        return

    raw = Image.open(base_path).convert("RGBA")
    if raw.size != (W, H):
        raw = raw.resize((W, H), Image.LANCZOS)

    # Flatten alpha onto black background — removes semi-transparent edge
    # pixels that cause dark spots on the Pager display
    img = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    img.paste(raw, (0, 0), raw)

    # Scale factor for sprites — 80% keeps buttons clear of pineapple logo
    SCALE = 0.8

    # Sprite positions — centered inside highlight boxes
    sprites = [
        # Transport buttons (step=42, +5px right)
        ("previous.png", 17, 176),
        ("play.png",     59, 176),
        ("pause.png",   101, 176),
        ("stop.png",    143, 176),
        ("next.png",    185, 176),
        # Eject, shuffle, repeat
        ("eject.png",   229, 178),
        ("shuffle.png", 279, 178),
        ("repeat.png",  369, 178),
        # NOTE: slider.png and slider2.png NOT baked in — drawn dynamically
    ]

    for fname, x, y in sprites:
        spath = os.path.join(DOWNLOADS, fname)
        if not os.path.exists(spath):
            print("  WARNING: %s not found, skipping" % fname)
            continue
        sprite = Image.open(spath).convert("RGBA")
        # Threshold alpha to eliminate anti-aliasing fringes
        _clean_alpha(sprite)
        # Scale transport/control sprites to 80% (sliders stay full size)
        if fname not in ("slider.png", "slider2.png"):
            sw = int(sprite.width * SCALE)
            sh = int(sprite.height * SCALE)
            sprite = sprite.resize((sw, sh), Image.LANCZOS)
            _clean_alpha(sprite)
        img.paste(sprite, (x, y), sprite)

    out = img.convert("RGB")
    path = os.path.join(OUT_DIR, "classic_bg.png")
    out.save(path)
    print("  Saved %s" % path)

    # Save seek slider knob separately for dynamic positioning
    slider_path = os.path.join(DOWNLOADS, "slider.png")
    if os.path.exists(slider_path):
        slider = Image.open(slider_path).convert("RGBA")
        _clean_alpha(slider)
        # Composite on bg patch at center of seek groove
        sx, sy = 240, 133
        patch = img.crop((sx, sy, sx + slider.width, sy + slider.height))
        patch = patch.convert("RGBA")
        patch.paste(slider, (0, 0), slider)
        knob_path = os.path.join(OUT_DIR, "slider-knob.png")
        patch.convert("RGB").save(knob_path)
        print("  Saved seek knob %s (%dx%d)" % (knob_path,
              slider.width, slider.height))

    # Save vol/bal slider knob (slider2.png) separately
    slider2_path = os.path.join(DOWNLOADS, "slider2.png")
    if os.path.exists(slider2_path):
        s2 = Image.open(slider2_path).convert("RGBA")
        _clean_alpha(s2)
        # Composite on bg patch at center of orange volume groove
        vx, vy = 230, 117
        patch = img.crop((vx, vy, vx + s2.width, vy + s2.height))
        patch = patch.convert("RGBA")
        patch.paste(s2, (0, 0), s2)
        vol_path = os.path.join(OUT_DIR, "vol-knob.png")
        patch.convert("RGB").save(vol_path)
        print("  Saved vol/bal knob %s (%dx%d)" % (vol_path,
              s2.width, s2.height))

    # Active button sprites (pre-composited on bg patch for no-alpha pagerctl)
    active_sprites = [
        ("previous-active.png", 17, 176),
        ("play-active.png",     59, 176),
        ("pause-active.png",   101, 176),
        ("stop-active.png",    143, 176),
        ("next-active.png",    185, 176),
        ("eject-active.png",   229, 178),
        ("shuffle-active.png", 279, 178),
        ("repeat-active.png",  369, 178),
    ]
    _save_sprite_patches(img, active_sprites, SCALE, "active")

    # Toggled sprites (shuffle/repeat ON state)
    toggled_sprites = [
        ("shuffle-toggled.png",        279, 178),
        ("shuffle-active-toggled.png", 279, 178),
        ("repeat-toggled.png",         369, 178),
        ("repeat-active-toggled.png",  369, 178),
    ]
    _save_sprite_patches(img, toggled_sprites, SCALE, "toggled")


def _clean_alpha(img):
    """Threshold alpha channel to eliminate anti-aliasing fringes."""
    if img.mode != "RGBA":
        return
    alpha = img.split()[3]
    alpha = alpha.point(lambda p: 255 if p > 128 else 0)
    img.putalpha(alpha)


def _save_sprite_patches(bg_img, sprite_list, scale, label):
    """Scale sprites, composite on bg patches, save to skins dir."""
    for fname, ax, ay in sprite_list:
        spath = os.path.join(DOWNLOADS, fname)
        if not os.path.exists(spath):
            print("  WARNING: %s not found, skipping" % fname)
            continue
        sprite = Image.open(spath).convert("RGBA")
        _clean_alpha(sprite)
        sw = int(sprite.width * scale)
        sh = int(sprite.height * scale)
        sprite = sprite.resize((sw, sh), Image.LANCZOS)
        _clean_alpha(sprite)
        # Composite onto background patch (pagerctl has no alpha)
        patch = bg_img.crop((ax, ay, ax + sw, ay + sh)).convert("RGBA")
        patch.paste(sprite, (0, 0), sprite)
        apath = os.path.join(OUT_DIR, fname)
        patch.convert("RGB").save(apath)
        print("  Saved %s sprite %s (%dx%d)" % (label, fname, sw, sh))


def gen_retro():
    """Retro CRT terminal — black with amber borders and scanlines."""
    img = Image.new("RGB", (W, H), (0, 0, 0))
    d = ImageDraw.Draw(img)

    amber = (0xFF, 0xB0, 0x00)
    amber_dim = (0x80, 0x58, 0x00)
    scanline = (0x08, 0x04, 0x00)

    # Double border — outer at 2px, inner at 5px
    d.rectangle([0, 0, W - 1, H - 1], outline=amber)
    d.rectangle([1, 1, W - 2, H - 2], outline=amber)
    d.rectangle([4, 4, W - 5, H - 5], outline=amber_dim)
    d.rectangle([5, 5, W - 6, H - 6], outline=amber_dim)

    # Recessed display panel (y 8-96)
    panel_bg = (0x06, 0x03, 0x00)
    d.rectangle([8, 8, W - 9, 96], fill=panel_bg)
    d.rectangle([8, 8, W - 9, 96], outline=amber_dim)

    # Scanlines across entire image
    for y in range(0, H, 2):
        d.line([(0, y), (W - 1, y)], fill=scanline)

    # Amber corner accents
    corner_size = 8
    for cx, cy in [(7, 7), (W - 8 - corner_size, 7),
                   (7, H - 8 - corner_size), (W - 8 - corner_size, H - 8 - corner_size)]:
        d.line([(cx, cy), (cx + corner_size, cy)], fill=amber)
        d.line([(cx, cy), (cx, cy + corner_size)], fill=amber)

    path = os.path.join(OUT_DIR, "retro_bg.png")
    img.save(path)
    print("  Saved %s" % path)


def gen_modern():
    """Modern minimal — dark with subtle cyan accents."""
    img = Image.new("RGB", (W, H), (0x0A, 0x0A, 0x0F))
    d = ImageDraw.Draw(img)

    cyan = (0x00, 0xD4, 0xFF)
    panel_light = (0x0E, 0x0E, 0x16)
    panel_lighter = (0x12, 0x12, 0x1C)

    # Thin cyan accent line across top (y=18)
    d.line([(0, 18), (W - 1, 18)], fill=cyan)

    # Very subtle panel shading — content areas slightly lighter
    # Track title area
    d.rectangle([4, 20, W - 5, 55], fill=panel_light)
    # Time / progress area
    d.rectangle([4, 60, W - 5, 145], fill=panel_light)
    # Transport area
    d.rectangle([4, 148, W - 5, 185], fill=panel_lighter)

    # Thin cyan dots at bottom corners (subtle accents)
    for x in range(0, W, 60):
        d.point((x, H - 2), fill=(0x00, 0x44, 0x55))

    # Subtle gradient at top (just a few rows)
    for y in range(4):
        alpha = 20 - y * 5
        c = (alpha // 4, alpha // 4, alpha // 2)
        d.line([(0, y), (W - 1, y)], fill=c)

    path = os.path.join(OUT_DIR, "modern_bg.png")
    img.save(path)
    print("  Saved %s" % path)


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    print("Generating PagerAmp skin backgrounds (%dx%d)..." % (W, H))
    gen_classic()
    gen_retro()
    gen_modern()
    print("Done!")
