#!/usr/bin/env python3
"""Generate PWA icon PNGs from a simple drawing spec using only stdlib."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    chunk = chunk_type + data
    crc = zlib.crc32(chunk) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk + struct.pack(">I", crc)


def make_png_rgb(width: int, height: int, pixel_func) -> bytes:
    """Create a minimal valid PNG (RGB, 8-bit, no interlace).

    pixel_func(x, y) -> (r, g, b) where each is 0-255.
    """
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter byte: None
        for x in range(width):
            r, g, b = pixel_func(x, y)
            raw.extend((r, g, b))
    compressed = zlib.compress(bytes(raw), level=9)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    idat = compressed
    iend = b""
    return sig + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", iend)


def draw_icon(size: int) -> bytes:
    """Draw a black-square icon with the threads-analytics spiral logo in white."""
    cx, cy = size / 2, size / 2
    radius = size * 0.36
    stroke = max(1, size * 0.035)

    def dist(x: float, y: float) -> float:
        return ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5

    def pixel(x: int, y: int):
        # Background: black
        r = g = b = 0

        # Outer ring
        d = dist(x, y)
        if abs(d - radius) <= stroke / 2:
            r = g = b = 255
            return r, g, b

        # Spiral arc (simplified as a partial ring segment)
        # We draw an arc from ~45deg to ~225deg at radius * 0.55
        if d < radius - stroke:
            angle = (math.atan2(y - cy, x - cx) + 2 * math.pi) % (2 * math.pi)
            arc_r = radius * 0.55
            if abs(d - arc_r) <= stroke / 2:
                # Only show arc in upper-left quadrant-ish
                if 0.5 <= angle <= 4.0:
                    r = g = b = 255
                    return r, g, b

        # Center dot
        if d <= stroke * 0.8:
            r = g = b = 255

        return r, g, b

    import math
    return make_png_rgb(size, size, pixel)


def main() -> None:
    static_dir = Path(__file__).parent.parent / "src" / "threads_analytics" / "web" / "static"
    static_dir.mkdir(parents=True, exist_ok=True)

    sizes = {
        "apple-touch-icon": 180,
        "icon-192": 192,
        "icon-512": 512,
    }

    for name, size in sizes.items():
        png_path = static_dir / f"{name}.png"
        png_path.write_bytes(draw_icon(size))
        print(f"Wrote {png_path}")


if __name__ == "__main__":
    main()
