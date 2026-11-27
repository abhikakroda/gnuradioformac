#!/usr/bin/env python3
# Copyright 2026 Free Software Foundation, Inc.
#
# This file is part of GNU Radio
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Build the macOS application icon (.icns) for GNU Radio Companion.

The artwork is the GNU Radio logo from
grc/scripts/freedesktop/gnuradio_logo_icon-square.svg, drawn as a glossy orange
glyph on a dark superellipse ("squircle") body that follows the macOS icon grid
(824 px body on a 1024 px canvas, with contact and ambient shadows, a top
highlight and a rim light) so it sits next to current system icons in the Dock.
A matching document icon (page with a folded corner, app glyph and a GRC label)
is written for .grc files.

The .icns container is written directly, so this runs on any platform and does
not need `iconutil`.  It is a developer tool: the generated file is committed
as release/resources/icons/gnuradio-companion.icns and the build only copies it.

    pip install cairosvg pillow
    python3 release/resources/macos/make_icon.py
"""

import argparse
import io
import math
import os
import struct
import sys

import cairosvg
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEFAULT_SVG = os.path.join(ROOT, "grc", "scripts", "freedesktop", "gnuradio_logo_icon-square.svg")
DEFAULT_OUT = os.path.join(ROOT, "release", "resources", "icons", "gnuradio-companion.icns")
DEFAULT_DOC_OUT = os.path.join(ROOT, "release", "resources", "icons", "gnuradio-companion-document.icns")

CANVAS = 1024
BODY = 824
SQUIRCLE_N = 5.0
LOGO_HEIGHT = 560
SVG_SIZE = 150.0

# (icns OSType, pixel size)
ICNS_ENTRIES = [
    (b"icp4", 16),
    (b"icp5", 32),
    (b"icp6", 64),
    (b"ic07", 128),
    (b"ic08", 256),
    (b"ic09", 512),
    (b"ic10", 1024),
    (b"ic11", 32),    # 16x16@2x
    (b"ic12", 64),    # 32x32@2x
    (b"ic13", 256),   # 128x128@2x
    (b"ic14", 512),   # 256x256@2x
]

BG_TOP = (52, 78, 138)
BG_BOTTOM = (12, 20, 48)
GLYPH_TOP = (255, 178, 92)
GLYPH_BOTTOM = (255, 94, 0)


def render_logo(svg_path, height=LOGO_HEIGHT):
    scale = 8
    png = cairosvg.svg2png(url=svg_path, output_width=int(SVG_SIZE * scale),
                           output_height=int(SVG_SIZE * scale))
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    img = img.crop(img.getchannel("A").getbbox())
    ratio = height / img.height
    return img.resize((round(img.width * ratio), height), Image.LANCZOS)


def squircle_mask(size, n=SQUIRCLE_N, supersample=4):
    """Superellipse (continuous-corner) mask, the shape of current macOS icons."""
    big = size * supersample
    half = big / 2.0
    steps = 720
    pts = []
    for i in range(steps):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        x = half + half * math.copysign(abs(c) ** (2.0 / n), c)
        y = half + half * math.copysign(abs(s) ** (2.0 / n), s)
        pts.append((x, y))
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).polygon(pts, fill=255)
    return mask.resize((size, size), Image.LANCZOS)


def gradient(size, top, bottom, width=None):
    width = width or size
    grad = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        grad.putpixel((0, y), tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    return grad.resize((width, size))


def radial_glow(size, center, radius, color, strength):
    layer = Image.new("RGBA", (size, size), color + (0,))
    alpha = Image.new("L", (size, size), 0)
    ImageDraw.Draw(alpha).ellipse((center[0] - radius, center[1] - radius,
                                   center[0] + radius, center[1] + radius), fill=int(255 * strength))
    layer.putalpha(alpha.filter(ImageFilter.GaussianBlur(radius / 2.2)))
    return layer


def shadow_of(alpha_image, offset, blur, opacity):
    layer = Image.new("RGBA", alpha_image.size, (0, 0, 0, 0))
    a = Image.new("L", alpha_image.size, 0)
    a.paste(alpha_image, offset)
    layer.putalpha(a.filter(ImageFilter.GaussianBlur(blur)).point(lambda v: int(v * opacity)))
    return layer


def tinted_glyph(logo, top, bottom):
    fill = gradient(logo.height, top, bottom, logo.width)
    fill.putalpha(logo.getchannel("A"))
    return fill


def build_master(svg_path):
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    offset = (CANVAS - BODY) // 2
    mask = squircle_mask(BODY)

    full_mask = Image.new("L", (CANVAS, CANVAS), 0)
    full_mask.paste(mask, (offset, offset))
    canvas = Image.alpha_composite(canvas, shadow_of(full_mask, (0, 14), 22, 0.45))
    canvas = Image.alpha_composite(canvas, shadow_of(full_mask, (0, 3), 4, 0.35))

    body = gradient(BODY, BG_TOP, BG_BOTTOM)
    body = Image.alpha_composite(body, radial_glow(BODY, (BODY // 2, int(BODY * 0.44)), 300, (255, 120, 30), 0.10))
    body = Image.alpha_composite(body, radial_glow(BODY, (BODY // 2, -40), 520, (170, 200, 255), 0.22))

    logo = render_logo(svg_path)
    lx, ly = (BODY - logo.width) // 2, (BODY - logo.height) // 2 + 6
    body = Image.alpha_composite(body, shadow_of_at(logo, (BODY, BODY), (lx, ly + 16), 18, 0.55))
    glyph = tinted_glyph(logo, GLYPH_TOP, GLYPH_BOTTOM)
    glyph_layer = Image.new("RGBA", (BODY, BODY), (0, 0, 0, 0))
    glyph_layer.paste(glyph, (lx, ly), glyph)
    body = Image.alpha_composite(body, glyph_layer)

    gloss = Image.new("RGBA", (BODY, BODY), (255, 255, 255, 0))
    gloss_alpha = gradient(BODY, (255, 255, 255), (0, 0, 0)).getchannel("R")
    gloss_alpha = gloss_alpha.point(lambda v: max(0, int((v - 170) * 0.40)))
    gloss.putalpha(gloss_alpha)
    body = Image.alpha_composite(body, gloss)

    rim_outer = mask
    rim_inner = Image.new("L", (BODY, BODY), 0)
    rim_inner.paste(mask, (0, 3))
    rim = ImageChops.subtract(rim_outer, rim_inner)
    top_fade = gradient(BODY, (255, 255, 255), (0, 0, 0)).getchannel("R")
    rim_layer = Image.new("RGBA", (BODY, BODY), (255, 255, 255, 0))
    rim_layer.putalpha(ImageChops.multiply(rim, top_fade).point(lambda v: int(v * 0.55)))
    body = Image.alpha_composite(body, rim_layer)

    dark_rim = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(3)))
    dark_layer = Image.new("RGBA", (BODY, BODY), (0, 0, 0, 0))
    dark_layer.putalpha(dark_rim.point(lambda v: int(v * 0.35)))
    body = Image.alpha_composite(body, dark_layer)

    body.putalpha(mask)
    canvas.alpha_composite(body, (offset, offset))
    return canvas


def shadow_of_at(img, size, pos, blur, opacity):
    a = Image.new("L", size, 0)
    a.paste(img.getchannel("A"), pos)
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    layer.putalpha(a.filter(ImageFilter.GaussianBlur(blur)).point(lambda v: int(v * opacity)))
    return layer


DOC_W = 700
DOC_H = 880
DOC_FOLD = 190
DOC_RADIUS = 56


def page_polygon(w, h, fold, radius, supersample=4):
    """Page with rounded corners and a folded top-right corner."""
    W, H, F, R = w * supersample, h * supersample, fold * supersample, radius * supersample
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle((0, 0, W - 1, H - 1), radius=R, fill=255)
    cut = Image.new("L", (W, H), 0)
    ImageDraw.Draw(cut).polygon([(W - F, -1), (W + 1, -1), (W + 1, F)], fill=255)
    mask = ImageChops.subtract(mask, cut)
    fold_shape = Image.new("L", (W, H), 0)
    ImageDraw.Draw(fold_shape).polygon([(W - F, 0), (W - F, F), (W, F)], fill=255)
    return (mask.resize((w, h), Image.LANCZOS), fold_shape.resize((w, h), Image.LANCZOS))


def build_document_master(svg_path):
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    ox, oy = (CANVAS - DOC_W) // 2, (CANVAS - DOC_H) // 2
    page, fold = page_polygon(DOC_W, DOC_H, DOC_FOLD, DOC_RADIUS)

    full = Image.new("L", (CANVAS, CANVAS), 0)
    full.paste(page, (ox, oy))
    canvas = Image.alpha_composite(canvas, shadow_of(full, (0, 14), 20, 0.40))
    canvas = Image.alpha_composite(canvas, shadow_of(full, (0, 3), 4, 0.30))

    body = gradient(DOC_H, (255, 255, 255), (225, 230, 240), DOC_W)
    top_band = Image.new("RGBA", (DOC_W, DOC_H), (255, 255, 255, 0))
    body = Image.alpha_composite(body, top_band)

    edge = ImageChops.subtract(page, page.filter(ImageFilter.MinFilter(3)))
    edge_layer = Image.new("RGBA", (DOC_W, DOC_H), (60, 70, 90, 0))
    edge_layer.putalpha(edge.point(lambda v: int(v * 0.35)))
    body = Image.alpha_composite(body, edge_layer)

    fold_fill = gradient(DOC_H, (205, 212, 226), (232, 236, 244), DOC_W)
    fold_fill.putalpha(fold)
    body = Image.alpha_composite(body, fold_fill)
    fold_edge = ImageChops.subtract(fold, fold.filter(ImageFilter.MinFilter(3)))
    fold_edge_layer = Image.new("RGBA", (DOC_W, DOC_H), (60, 70, 90, 0))
    fold_edge_layer.putalpha(fold_edge.point(lambda v: int(v * 0.30)))
    body = Image.alpha_composite(body, fold_edge_layer)

    logo = render_logo(svg_path, 330)
    lx, ly = (DOC_W - logo.width) // 2, 250
    body = Image.alpha_composite(body, shadow_of_at(logo, (DOC_W, DOC_H), (lx, ly + 8), 8, 0.30))
    glyph = tinted_glyph(logo, GLYPH_TOP, GLYPH_BOTTOM)
    layer = Image.new("RGBA", (DOC_W, DOC_H), (0, 0, 0, 0))
    layer.paste(glyph, (lx, ly), glyph)
    body = Image.alpha_composite(body, layer)

    label = "GRC"
    font = None
    for candidate in ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Helvetica.ttc",
                      "/Library/Fonts/Arial Bold.ttf"):
        if os.path.isfile(candidate):
            try:
                font = ImageFont.truetype(candidate, 112)
                if "SFNS" in candidate:
                    font.set_variation_by_name("Bold")
                break
            except Exception:
                font = None
    if font is not None:
        d = ImageDraw.Draw(body)
        box = d.textbbox((0, 0), label, font=font)
        tw = box[2] - box[0]
        d.text(((DOC_W - tw) // 2 - box[0], 650), label, font=font, fill=(52, 64, 96, 255))

    body.putalpha(page)
    canvas.alpha_composite(body, (ox, oy))
    return canvas


def write_icns(master, path):
    chunks = []
    cache = {}
    for ostype, px in ICNS_ENTRIES:
        if px not in cache:
            buf = io.BytesIO()
            master.resize((px, px), Image.LANCZOS).save(buf, format="PNG", optimize=True)
            cache[px] = buf.getvalue()
        data = cache[px]
        chunks.append(ostype + struct.pack(">I", len(data) + 8) + data)
    body = b"".join(chunks)
    with open(path, "wb") as f:
        f.write(b"icns" + struct.pack(">I", len(body) + 8) + body)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--svg", default=DEFAULT_SVG)
    parser.add_argument("-o", "--output", default=DEFAULT_OUT)
    parser.add_argument("--document-output", default=DEFAULT_DOC_OUT)
    parser.add_argument("--png", help="Also write the 1024 px application icon master PNG here")
    parser.add_argument("--document-png", help="Also write the 1024 px document icon master PNG here")
    args = parser.parse_args()

    master = build_master(args.svg)
    if args.png:
        master.save(args.png)
    write_icns(master, args.output)
    print("wrote", args.output)

    document = build_document_master(args.svg)
    if args.document_png:
        document.save(args.document_png)
    write_icns(document, args.document_output)
    print("wrote", args.document_output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
