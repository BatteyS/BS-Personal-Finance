"""The app's icon, drawn in code so the repo holds no images: a green ring, like the page's
spending ring, on a dark rounded square, with one slice of the ring in a lighter green.

    uv run icon.py static/apple-touch-icon.png         # 180 px, for a phone's home screen
    uv run icon.py --iconset /tmp/icon.iconset         # every size macOS wants, for the Desktop app

The phone's icon is served from static/ (Streamlit's static folder, gitignored); launch.sh draws it
before it starts the page. install.sh turns the iconset into the Desktop app's icon with iconutil.
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
import zlib
from pathlib import Path

BACKGROUND = (24, 26, 27)
RING = (27, 175, 122)      # the brokerage green from the page's palette
SLICE = (134, 224, 182)    # a lighter step of it
ICONSET_SIZES = [16, 32, 64, 128, 256, 512, 1024]


def inside_rounded_square(x: float, y: float, size: float, radius: float) -> bool:
    """Whether a point lies in a square of side `size` with corners rounded to `radius`."""
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def pixel(x: float, y: float, size: int) -> tuple[int, int, int, int]:
    """The colour at one point of the icon, as RGBA."""
    if not inside_rounded_square(x, y, size, size * 0.22):
        return (0, 0, 0, 0)
    dx, dy = x - size / 2, y - size / 2
    distance = math.hypot(dx, dy)
    if size * 0.22 <= distance <= size * 0.36:
        angle = math.degrees(math.atan2(dx, -dy)) % 360  # clockwise from twelve o'clock
        if 8 <= angle <= 112:
            return (*SLICE, 255)
        if angle < 4 or 116 < angle:
            return (*RING, 255)
    return (*BACKGROUND, 255)


def draw(size: int) -> bytes:
    """The icon at `size` pixels square, as PNG bytes. Each pixel averages a 3x3 grid of samples,
    so the edges are smooth."""
    samples = [(i + 0.5) / 3 for i in range(3)]
    rows = []
    for row in range(size):
        line = bytearray([0])  # no PNG filter on this row
        for column in range(size):
            total = [0, 0, 0, 0]
            for sy in samples:
                for sx in samples:
                    r, g, b, a = pixel(column + sx, row + sy, size)
                    total = [total[0] + r * a, total[1] + g * a, total[2] + b * a, total[3] + a]
            alpha = total[3]
            colour = [round(channel / alpha) if alpha else 0 for channel in total[:3]]
            line += bytes([*colour, round(alpha / 9)])
        rows.append(bytes(line))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + chunk(b"IEND", b""))


def write_iconset(folder: Path) -> None:
    """Every size macOS's iconutil wants: icon_16x16.png, icon_16x16@2x.png and so on."""
    folder.mkdir(parents=True, exist_ok=True)
    for size in ICONSET_SIZES[:-1]:
        (folder / f"icon_{size}x{size}.png").write_bytes(draw(size))
        (folder / f"icon_{size}x{size}@2x.png").write_bytes(draw(size * 2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draw the app's icon.")
    parser.add_argument("path", nargs="?", type=Path, help="a PNG to write, 180 px unless --size says")
    parser.add_argument("--size", type=int, default=180)
    parser.add_argument("--iconset", type=Path, help="a folder to fill with the sizes macOS wants")
    args = parser.parse_args(argv)
    if args.iconset:
        write_iconset(args.iconset)
    if args.path:
        args.path.parent.mkdir(parents=True, exist_ok=True)
        args.path.write_bytes(draw(args.size))
    if not (args.iconset or args.path):
        parser.error("give a PNG to write, or --iconset FOLDER")
    return 0


if __name__ == "__main__":
    sys.exit(main())
