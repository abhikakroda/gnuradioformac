#!/usr/bin/env python3
# Copyright 2026 Free Software Foundation, Inc.
#
# This file is part of GNU Radio
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
"""Build the macOS application icon (.icns) for GNU Radio Companion.

The artwork is the GNU Radio logo from
grc/scripts/freedesktop/gnuradio_logo_icon-square.svg, placed on a rounded
square following the macOS icon grid (824 px body on a 1024 px canvas, with a
soft drop shadow) so that it sits next to other apps in the Dock.

The .icns container is written directly, so this runs on any platform and does
not need `iconutil`.  It is a developer tool: the generated file is committed
as release/resources/icons/gnuradio-companion.icns and the build only copies it.

    pip install cairosvg pillow
    python3 release/resources/macos/make_icon.py
"""

import argparse
import io
import os
import struct
import sys

import cairosvg
from PIL import Image, ImageChops, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
DEFAULT_SVG = os.path.join(ROOT, "grc", "scripts", "freedesktop", "gnuradio_logo_icon-square.svg")
DEFAULT_OUT = os.path.join(ROOT, "release", "resources", "icons", "gnuradio-companion.icns")

CANVAS = 1024
BODY = 824
CORNER_RADIUS = 185
LOGO_HEIGHT = 580
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


def render_logo(svg_path):
    scale = 8
    png = cairosvg.svg2png(url=svg_path, output_width=int(SVG_SIZE * scale),
                           output_height=int(SVG_SIZE * scale))
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    img = img.crop(img.getchannel("A").getbbox())
    ratio = LOGO_HEIGHT / img.height
    return img.resize((round(img.width * ratio), LOGO_HEIGHT), Image.LANCZOS)


def squircle_mask(size, radius, supersample=4):
    big = Image.new("L", (size * supersample, size * supersample), 0)
    ImageDraw.Draw(big).rounded_rectangle(
        (0, 0, size * supersample - 1, size * supersample - 1),
        radius=radius * supersample, fill=255)
    return big.resize((size, size), Image.LANCZOS)


def vertical_gradient(size, top, bottom):
    grad = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / (size - 1)
        grad.putpixel((0, y), tuple(round(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    return grad.resize((size, size))


def build_master(svg_path):
    canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    offset = (CANVAS - BODY) // 2

    mask = squircle_mask(BODY, CORNER_RADIUS)
    shadow = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    shadow_alpha = Image.new("L", (CANVAS, CANVAS), 0)
    shadow_alpha.paste(mask, (offset, offset + 12))
    shadow_alpha = shadow_alpha.filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.30))
    shadow.putalpha(shadow_alpha)
    canvas = Image.alpha_composite(canvas, shadow)

    body = vertical_gradient(BODY, (255, 255, 255), (226, 231, 238))
    body.putalpha(mask)
    canvas.alpha_composite(body, (offset, offset))

    # Subtle inner edge so the white body reads against light Dock backgrounds
    edge = ImageChops.subtract(mask, mask.filter(ImageFilter.MinFilter(5)))
    edge_layer = Image.new("RGBA", (BODY, BODY), (0, 0, 0, 0))
    edge_layer.putalpha(edge.point(lambda v: int(v * 0.12)))
    canvas.alpha_composite(edge_layer, (offset, offset))

    logo = render_logo(svg_path)
    x = (CANVAS - logo.width) // 2
    y = (CANVAS - logo.height) // 2
    canvas.alpha_composite(logo, (x, y))
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
    parser.add_argument("--png", help="Also write the 1024 px master PNG here")
    args = parser.parse_args()

    master = build_master(args.svg)
    if args.png:
        master.save(args.png)
    write_icns(master, args.output)
    print("wrote", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
