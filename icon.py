"""The app's icon, drawn in code so the repo holds no images: a gold ring, like the page's
spending ring, on a dark rounded square, with one slice of the ring in cream. The colors are
seanbattey.com's: its background, its gold and its text.

    uv run icon.py static/apple-touch-icon.png         # 180 px, filled square, for a phone's home screen
    uv run icon.py --iconset /tmp/icon.iconset         # every size macOS wants, for the Desktop app
    uv run icon.py --ico "Personal Finance.ico"        # every size Windows wants, for its shortcuts
    uv run icon.py --favicon                           # the tab's and Safari Favorites', in Streamlit's page

The phone's icon is served from static/ (Streamlit's static folder, gitignored); launch.sh draws it
before it starts the page. install.sh turns the iconset into the Desktop app's icon with iconutil,
and install.ps1 gives the .ico to the Start menu's and the Desktop's shortcuts.

The browser tab: Streamlit's page names its own favicon.png, Streamlit's logo, and page_icon only
swaps that link's address by script once the page has loaded. Streamlit also serves that file as
immutable for a year, so a browser that has it never asks again. So --favicon writes the app's icon
over favicon.png in the installed Streamlit, and points the page's link at favicon.png?v=<version>:
the page is served fresh each time, so a browser sees a new address and fetches the new icon, and a
changed icon gets a new address again. launch.sh does it before it starts a page, the Dockerfile
when it builds, and the page itself once as it starts, for a page started some other way.

A browser tab's icon and the home-screen icon are the square filled to the edge (square=True),
fading from a lighter brown at the top to the background, as Job Search's icon does: Safari draws a
white outline around a tab icon it finds too dark, and side by side in its tab bar the flat square
got one where this got none. They are an RGB PNG with no alpha channel at all: Safari puts white
behind a tab icon's clear pixels, so the rounded square showed in a white box there, and iOS rounds
a home-screen icon's corners itself.
The Desktop app's and Windows' icons keep the rounded square.

Safari's Favorites, on a new tab's start page, show a site's home-screen icon, and Safari finds it
only in the page's markup (or at /apple-touch-icon.png), never one a script adds. Without one it
draws the small tab icon in a white tile. So --favicon also writes the home-screen icon into
Streamlit's own folder, served at /apple-touch-icon.png, and names it in Streamlit's page beside
the tab icon, as Job Search and seanbattey.com do. dashboard.py's script still adds one where
Streamlit couldn't be written to.

Safari keeps each icon address's picture for good, and in a tab in front it can show an icon a
script names, such as the home-screen icon dashboard.py adds: an unversioned apple-touch-icon.png
kept Safari's tab on the old green ring after the icon turned gold. So every icon address the page
names, in its markup or by script, carries version().
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import math
import re
import struct
import sys
import zlib
from pathlib import Path

BACKGROUND = (23, 18, 13)  # #17120d, the site's background
TOP = (44, 36, 28)         # #2c241c: the filled square fades from this at the top to BACKGROUND
RING = (242, 193, 78)      # #f2c14e, its gold
SLICE = (242, 233, 216)    # #f2e9d8, its cream text
ICONSET_SIZES = [16, 32, 64, 128, 256, 512, 1024]
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
FAVICON_SIZE = 64  # sharp in a tab on a Retina screen
TOUCH_SIZE = 180   # the home-screen icon's, which Safari's Favorites show too
# The tab icon's link in Streamlit's own page, with or without a version already on it, and the
# home-screen icon's link install_favicon puts after it, if it's there yet.
FAVICON_LINK = re.compile(r'<link rel="shortcut icon" href="\./favicon\.png[^"]*" />'
                          r'(<link rel="apple-touch-icon" href="[^"]*" />)?')


def inside_rounded_square(x: float, y: float, size: float, radius: float) -> bool:
    """Whether a point lies in a square of side `size` with corners rounded to `radius`."""
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def pixel(x: float, y: float, size: int, square: bool = False) -> tuple[int, int, int, int]:
    """The colour at one point of the icon, as RGBA: on a rounded square, or with square=True on
    the whole square, filled to the edge."""
    if not square and not inside_rounded_square(x, y, size, size * 0.22):
        return (0, 0, 0, 0)
    dx, dy = x - size / 2, y - size / 2
    distance = math.hypot(dx, dy)
    if size * 0.22 <= distance <= size * 0.36:
        angle = math.degrees(math.atan2(dx, -dy)) % 360  # clockwise from twelve o'clock
        if 8 <= angle <= 112:
            return (*SLICE, 255)
        if angle < 4 or 116 < angle:
            return (*RING, 255)
    if square:  # lighter at the top, as Job Search's icon is
        t = min(max(y / size, 0.0), 1.0)
        return (*(round(a + (b - a) * t) for a, b in zip(TOP, BACKGROUND)), 255)
    return (*BACKGROUND, 255)


def draw(size: int, square: bool = False) -> bytes:
    """The icon at `size` pixels square, as PNG bytes: the rounded square, or with square=True the
    square filled to the edge, for a browser tab and a phone's home screen, written as RGB with no
    alpha channel, as seanbattey.com's and Job Search's home-screen icons are. Each pixel averages
    a 3x3 grid of samples, so the edges are smooth."""
    samples = [(i + 0.5) / 3 for i in range(3)]
    rows = []
    for row in range(size):
        line = bytearray([0])  # no PNG filter on this row
        for column in range(size):
            total = [0, 0, 0, 0]
            for sy in samples:
                for sx in samples:
                    r, g, b, a = pixel(column + sx, row + sy, size, square)
                    total = [total[0] + r * a, total[1] + g * a, total[2] + b * a, total[3] + a]
            alpha = total[3]
            colour = [round(channel / alpha) if alpha else 0 for channel in total[:3]]
            line += bytes(colour if square else [*colour, round(alpha / 9)])  # square: never clear
        rows.append(bytes(line))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", size, size, 8, 2 if square else 6, 0, 0, 0)  # 8-bit RGB or RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + chunk(b"IEND", b""))


def write_iconset(folder: Path) -> None:
    """Every size macOS's iconutil wants: icon_16x16.png, icon_16x16@2x.png and so on."""
    folder.mkdir(parents=True, exist_ok=True)
    for size in ICONSET_SIZES[:-1]:
        (folder / f"icon_{size}x{size}.png").write_bytes(draw(size))
        (folder / f"icon_{size}x{size}@2x.png").write_bytes(draw(size * 2))


def ico(sizes: list[int] = ICO_SIZES) -> bytes:
    """A Windows .ico holding the icon at each size, each one a PNG, which Windows reads from Vista
    on: a header, one 16-byte entry per image (a width or height of 256 is written as 0), then the
    images."""
    images = [draw(size) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))  # reserved, 1 for an icon, how many
    offset = len(header) + 16 * len(images)
    entries = b""
    for size, image in zip(sizes, images):
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32, len(image), offset)
        offset += len(image)
    return header + entries + b"".join(images)


def streamlit_static() -> Path | None:
    """The folder the installed Streamlit serves its own page from; None without Streamlit."""
    spec = importlib.util.find_spec("streamlit")
    if spec is None or not spec.origin:
        return None
    return Path(spec.origin).parent / "static"


def version() -> str:
    """Twelve hex digits of the icon's own hash, for the address of each icon the page names: a
    changed icon gets new addresses, which a browser holding the old one fetches afresh."""
    return hashlib.sha256(draw(FAVICON_SIZE, square=True)).hexdigest()[:12]


def install_favicon(static: Path | None = None) -> bool:
    """Make the browser tab and Safari's Favorites show the app's icon: write it over favicon.png in
    Streamlit's own static folder (or `static`), and the home-screen icon beside it as
    apple-touch-icon.png, and have its page name both, at favicon.png?v=<the icon's hash> and
    apple-touch-icon.png?v=<the same>, each unless it's so already. Returns whether anything
    changed. A Streamlit that can't be written to (a container's, owned by root) raises OSError:
    the Dockerfile does it while it builds instead."""
    static = static or streamlit_static()
    if static is None or not static.is_dir():
        return False
    changed = False
    for name, size in (("favicon.png", FAVICON_SIZE), ("apple-touch-icon.png", TOUCH_SIZE)):
        image = draw(size, square=True)
        file = static / name
        if not file.exists() or file.read_bytes() != image:
            file.write_bytes(image)
            changed = True
    page = static / "index.html"
    if page.exists():
        html = page.read_text(encoding="utf-8")
        links = (f'<link rel="shortcut icon" href="./favicon.png?v={version()}" />'
                 f'<link rel="apple-touch-icon" href="./apple-touch-icon.png?v={version()}" />')
        linked = FAVICON_LINK.sub(lambda found: links, html)
        if linked != html:
            page.write_text(linked, encoding="utf-8")
            changed = True
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Draw the app's icon.")
    parser.add_argument("path", nargs="?", type=Path,
                        help="a home-screen PNG to write, filled to the edge, 180 px unless --size says")
    parser.add_argument("--size", type=int, default=180)
    parser.add_argument("--iconset", type=Path, help="a folder to fill with the sizes macOS wants")
    parser.add_argument("--ico", type=Path, help="a .ico to write, with the sizes Windows wants")
    parser.add_argument("--favicon", action="store_true",
                        help="make the browser tab and Safari's Favorites show the app's icon, "
                             "in the installed Streamlit")
    args = parser.parse_args(argv)
    if args.favicon:
        install_favicon()
    if args.iconset:
        write_iconset(args.iconset)
    if args.ico:
        args.ico.parent.mkdir(parents=True, exist_ok=True)
        args.ico.write_bytes(ico())
    if args.path:
        args.path.parent.mkdir(parents=True, exist_ok=True)
        args.path.write_bytes(draw(args.size, square=True))
    if not (args.iconset or args.ico or args.path or args.favicon):
        parser.error("give a PNG to write, --iconset FOLDER, --ico FILE or --favicon")
    return 0


if __name__ == "__main__":
    sys.exit(main())
