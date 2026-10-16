"""The app icon — an amber tile with the ink letter D and a small key standing in its
counter — drawn here and written to `assets/` as PNG (256 px, the window icon), ICO
(16–256 px, the Windows exe) and ICNS (the macOS bundle). The results are committed; run this only after changing the design.

    python tools/make_icon.py

Needs Pillow (not a build dependency: `pip install Pillow`) and the Segoe UI Bold font of
Windows; pass another bold font with `--font <path>` elsewhere.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
S = 1024  # the drawing size; every coordinate below is in these units
AMBER_TOP, AMBER_BOTTOM, INK = (0xFC, 0xD3, 0x4D), (0xF5, 0x9E, 0x0B), (0x11, 0x18, 0x27)
KEY_FRACTION = 0.66  # the key's height as a share of the counter's
ICO_SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]


def _layer() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    return img, ImageDraw.Draw(img)


def tile() -> Image.Image:
    """The rounded square with a vertical gradient."""
    grad = Image.new("RGBA", (S, S))
    px = grad.load()
    for y in range(S):
        t = y / (S - 1)
        colour = tuple(round(AMBER_TOP[i] * (1 - t) + AMBER_BOTTOM[i] * t) for i in range(3)) + (255,)
        for x in range(S):
            px[x, y] = colour
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * 0.22), fill=255)
    img, _ = _layer()
    img.paste(grad, (0, 0), mask)
    return img


def glyph(font_path: str) -> Image.Image:
    img, d = _layer()
    font = ImageFont.truetype(font_path, 760)
    box = d.textbbox((0, 0), "D", font=font)
    d.text(((S - (box[2] - box[0])) / 2 - box[0], (S - (box[3] - box[1])) / 2 - box[1] - 10), "D", font=font, fill=INK + (255,))
    return img


def counter_box(letter: Image.Image) -> tuple[int, int, int, int]:
    """The enclosed hole of the letter: the transparent pixels the outside cannot reach."""
    alpha = letter.split()[3].point(lambda v: 255 if v > 8 else 0).convert("L")
    marker = alpha.copy()
    ImageDraw.floodfill(marker, (0, 0), 128)
    inner = marker.point(lambda v: 255 if v == 0 else 0)
    box = inner.getbbox()
    assert box is not None, "the letter has no counter"
    return box


def key(scale: float, dx: int, dy: int) -> Image.Image:
    """An upright key, the bow up, centred then moved by (dx, dy)."""
    img, d = _layer()
    colour = INK + (255,)
    cx = S // 2
    bow_r, hole_r, half_w = int(150 * scale), int(62 * scale), int(62 * scale)
    top = int(S * 0.5 - 330 * scale)
    bow_y = top + bow_r
    d.ellipse((cx - bow_r, bow_y - bow_r, cx + bow_r, bow_y + bow_r), fill=colour)
    d.ellipse((cx - hole_r, bow_y - hole_r, cx + hole_r, bow_y + hole_r), fill=(0, 0, 0, 0))
    bottom = int(S * 0.5 + 330 * scale)
    d.rounded_rectangle((cx - half_w, bow_y + bow_r - 20, cx + half_w, bottom), radius=half_w, fill=colour)
    tooth_w, tooth_h = int(95 * scale), int(44 * scale)
    d.rounded_rectangle((cx + half_w - 10, bottom - tooth_h, cx + half_w + tooth_w, bottom), radius=tooth_h // 2, fill=colour)
    d.rounded_rectangle((cx + half_w - 10, bottom - 3 * tooth_h, cx + half_w + tooth_w * 0.7, bottom - 2 * tooth_h), radius=tooth_h // 2, fill=colour)
    return img.transform((S, S), Image.AFFINE, (1, 0, -dx, 0, 1, -dy), resample=Image.BICUBIC)


def shadow(img: Image.Image) -> Image.Image:
    sh, _ = _layer()
    sh.paste((0, 0, 0, 255), (12, 12), img.split()[3].point(lambda v: v * 70 // 255))
    return sh.filter(ImageFilter.GaussianBlur(20))


def draw(font_path: str) -> Image.Image:
    letter = glyph(font_path)
    x0, y0, x1, y1 = counter_box(letter)
    scale = (y1 - y0) * KEY_FRACTION / 660  # the key is 660 units tall at scale 1
    small_key = key(scale, (x0 + x1) // 2 - S // 2, (y0 + y1) // 2 - S // 2)
    out = tile()
    for part in (letter, small_key):
        out = Image.alpha_composite(out, shadow(part))
        out = Image.alpha_composite(out, part)
    return out


def write(img: Image.Image) -> list[Path]:
    ASSETS.mkdir(exist_ok=True)
    png, ico, icns = ASSETS / "icon.png", ASSETS / "icon.ico", ASSETS / "icon.icns"
    img.resize((256, 256), Image.LANCZOS).save(png)  # the window icon: small to load at start
    img.save(ico, sizes=ICO_SIZES)
    img.save(icns)
    return [png, ico, icns]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--font", default=r"C:\Windows\Fonts\segoeuib.ttf", help="a bold TrueType font for the letter")
    args = parser.parse_args()
    for path in write(draw(args.font)):
        print(f"{path.relative_to(ROOT)}: {path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
