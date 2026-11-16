"""Render the app icon (amber level bars on a dark rounded square) to ui/icon.svg and ui/app.ico.

Pure Python: the geometry is simple enough to rasterize here (signed distances, 4x4
supersampling on edge pixels), and the .ico holds one PNG per size (zlib + struct).
Run from the repo:  python tools/make_icon.py   [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import math
import os
import struct
import zlib

SIZES = (16, 24, 32, 48, 64, 128, 256)
VIEW = 16.0
BG = (0x26, 0x2C, 0x33)
AMBER = (0xF0, 0xB4, 0x3C)
RADIUS = 3.0
STROKE = 1.6
BARS = ((3, 11, 7), (6, 11, 4), (9, 11, 6), (12, 11, 8))  # x, y bottom, y top (viewBox units)

SVG = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' width='16' height='16'>"
    f"<rect width='16' height='16' rx='{RADIUS:g}' fill='#{BG[0]:02X}{BG[1]:02X}{BG[2]:02X}'/>"
    "<path d='" + "".join(f"M{x} {y0}V{y1}" for x, y0, y1 in BARS) + "' "
    f"stroke='#{AMBER[0]:02X}{AMBER[1]:02X}{AMBER[2]:02X}' stroke-width='{STROKE:g}' "
    "stroke-linecap='round' fill='none'/></svg>\n"
)


def _sd_round_rect(x: float, y: float) -> float:
    """Signed distance to the 16x16 rounded square (negative inside)."""
    h = VIEW / 2
    qx, qy = abs(x - h) - (h - RADIUS), abs(y - h) - (h - RADIUS)
    out = math.hypot(max(qx, 0.0), max(qy, 0.0))
    return out + min(max(qx, qy), 0.0) - RADIUS


def _sd_bars(x: float, y: float) -> float:
    """Signed distance to the nearest round-capped bar."""
    best = 1e9
    r = STROKE / 2
    for bx, y0, y1 in BARS:
        lo, hi = min(y0, y1), max(y0, y1)
        cy = min(max(y, lo), hi)
        best = min(best, math.hypot(x - bx, y - cy) - r)
    return best


def render(size: int, ss: int = 4) -> bytes:
    """RGBA pixels, row-major, premultiplied-free (straight alpha)."""
    scale = VIEW / size
    px_units = scale  # one pixel in viewBox units
    out = bytearray()
    offs = [(i + 0.5) / ss for i in range(ss)]
    for py in range(size):
        for px in range(size):
            cx, cy = (px + 0.5) * scale, (py + 0.5) * scale
            d_bg, d_bar = _sd_round_rect(cx, cy), _sd_bars(cx, cy)
            if abs(d_bg) > px_units and abs(d_bar) > px_units:
                a_bg = 1.0 if d_bg < 0 else 0.0
                a_bar = 1.0 if d_bar < 0 else 0.0
            else:
                n_bg = n_bar = 0
                for oy in offs:
                    for ox in offs:
                        sx, sy = (px + ox) * scale, (py + oy) * scale
                        if _sd_round_rect(sx, sy) < 0:
                            n_bg += 1
                            if _sd_bars(sx, sy) < 0:
                                n_bar += 1
                a_bg = n_bg / (ss * ss)
                a_bar = n_bar / max(1, n_bg)  # bars only paint inside the square
            if a_bg <= 0:
                out += b"\0\0\0\0"
                continue
            rgb = [round(BG[i] * (1 - a_bar) + AMBER[i] * a_bar) for i in range(3)]
            out += bytes(rgb) + bytes([round(255 * a_bg)])
    return bytes(out)


def png(size: int, rgba: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    stride = size * 4
    raw = b"".join(b"\0" + rgba[r * stride:(r + 1) * stride] for r in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def ico(images: list[tuple[int, bytes]]) -> bytes:
    head = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, data in images:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    return head + entries + blobs


def build(out_dir: str, sizes=SIZES) -> tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    svg_path = os.path.join(out_dir, "icon.svg")
    ico_path = os.path.join(out_dir, "app.ico")
    with open(svg_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(SVG)
    with open(ico_path, "wb") as f:
        f.write(ico([(s, png(s, render(s))) for s in sizes]))
    return svg_path, ico_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", default=os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui"))
    a = ap.parse_args(argv)
    for p in build(a.out_dir):
        print("wrote", p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
