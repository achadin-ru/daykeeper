"""The icon and the colour of a site, for a bookmark tile.

The page itself says what it looks like: `<link rel="icon">` and `apple-touch-icon`, the
web manifest with its icons and `theme_color`, `<meta name="theme-color">`. The best
raster icon is taken (PNG or ICO; SVG has no decoder here), scaled to ICON_SIZE and kept
as a small PNG in the vault, so a tile shows it offline and the address never goes to a
third-party icon service. The tile colour is the theme colour of the site, else the main
colour of its icon. Pure (no tkinter): decoding, scaling and colour picking are tested
without a window; `fetch` does the network part and is run in a background thread."""

from __future__ import annotations

import colorsys
import ipaddress
import json
import re
import socket
import struct
import urllib.request
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from .pngimage import png

ICON_SIZE = 64
PAGE_LIMIT = 512 * 1024
ICON_LIMIT = 1024 * 1024
MAX_SIDE = 512  # px; a larger icon is skipped for the next candidate
TIMEOUT = 4.0
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Daykeeper"


@dataclass
class SiteInfo:
    icon: bytes | None = None  # a PNG of ICON_SIZE × ICON_SIZE
    colour: str = ""  # "#RRGGBB", readable on white; "" — the site gave none


# ---------------------------------------------------------------- decoding

class Pixels:
    """RGBA pixels, row by row."""

    def __init__(self, width: int, height: int, rgba: bytearray):
        self.width, self.height, self.rgba = width, height, rgba


def _unfilter(data: bytes, width: int, height: int, bpp: int, row_bytes: int) -> bytearray:
    out = bytearray(row_bytes * height)
    prev = bytearray(row_bytes)
    pos = 0
    for y in range(height):
        kind = data[pos]
        line = bytearray(data[pos + 1 : pos + 1 + row_bytes])
        pos += 1 + row_bytes
        if kind == 1:
            for i in range(bpp, row_bytes):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif kind == 2:
            for i in range(row_bytes):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif kind == 3:
            for i in range(row_bytes):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif kind == 4:
            for i in range(row_bytes):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
        elif kind != 0:
            raise ValueError("bad PNG filter")
        out[y * row_bytes : (y + 1) * row_bytes] = line
        prev = line
    return out


def _inflate(data: bytes, size: int) -> bytes:
    """Unpack no more than the picture needs: a megabyte of zeros inflates to a gigabyte."""
    z = zlib.decompressobj()
    out = z.decompress(data, size)
    if z.unconsumed_tail:
        raise ValueError("PNG data larger than the picture")
    return out


def decode_png(data: bytes) -> Pixels:
    """Any non-interlaced PNG (grey, RGB, palette, with or without alpha; 1–16 bits)."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG")
    pos, idat = 8, []
    width = height = depth = ctype = interlace = 0
    palette, trns = b"", b""
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos : pos + 8])
        body = data[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, ctype, _c, _f, interlace = struct.unpack(">IIBBBBB", body)
        elif kind == b"PLTE":
            palette = body
        elif kind == b"tRNS":
            trns = body
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
    if not 0 < width <= MAX_SIDE or not 0 < height <= MAX_SIDE:
        raise ValueError("bad PNG size")  # the decoder is plain Python: 1024² took 1.5 s of the window's time
    if interlace:
        raise ValueError("interlaced PNG")
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bits = channels * depth
    row_bytes = (width * bits + 7) // 8
    raw = _unfilter(_inflate(b"".join(idat), (row_bytes + 1) * height), width, height, max(1, bits // 8), row_bytes)
    out = bytearray(width * height * 4)
    maxv = (1 << depth) - 1
    for y in range(height):
        row = raw[y * row_bytes : (y + 1) * row_bytes]
        samples: Sequence[int]
        if depth == 16:
            samples = [row[i] for i in range(0, len(row), 2)]  # the high byte is enough
            maxv_s = 255
        elif depth == 8:
            samples = row
            maxv_s = 255
        else:
            per = 8 // depth
            samples = [(row[i // per] >> (8 - depth * (i % per + 1))) & maxv for i in range(width * channels)]
            maxv_s = maxv
        for x in range(width):
            o = (y * width + x) * 4
            s = samples[x * channels : (x + 1) * channels]
            if ctype == 3:
                idx = s[0]
                r, g, b = palette[idx * 3 : idx * 3 + 3] if idx * 3 + 3 <= len(palette) else (0, 0, 0)
                a = trns[idx] if idx < len(trns) else 255
            else:
                scaled = [v * 255 // maxv_s for v in s]
                if ctype == 0:
                    r = g = b = scaled[0]
                    a = 255
                elif ctype == 4:
                    r = g = b = scaled[0]
                    a = scaled[1]
                elif ctype == 2:
                    r, g, b = scaled
                    a = 255
                else:
                    r, g, b, a = scaled
            out[o : o + 4] = bytes((r, g, b, a))
    return Pixels(width, height, out)


def _decode_bmp(data: bytes, width: int, height: int) -> Pixels:
    """A BMP inside an ICO: a header without the file part, the colour rows bottom-up,
    then a 1-bit transparency mask."""
    header = struct.unpack("<IiiHHI", data[:20])
    head_size, bmp_w, _bmp_h2, _planes, bpp, compression = header
    if compression not in (0, 3):
        raise ValueError("compressed BMP")
    width = width or bmp_w
    colours = struct.unpack("<I", data[32:36])[0] if len(data) >= 36 else 0
    pos = head_size
    palette = []
    if bpp <= 8:
        n = min(colours or (1 << bpp), 1 << bpp)  # more colours than the depth can index is a lie
        palette = [data[pos + i * 4 : pos + i * 4 + 4] for i in range(n)]
        pos += n * 4
    row_bytes = ((width * bpp + 31) // 32) * 4
    mask_row = ((width + 31) // 32) * 4
    mask_at = pos + row_bytes * height
    out = bytearray(width * height * 4)
    any_alpha = False
    for y in range(height):
        src = pos + (height - 1 - y) * row_bytes
        for x in range(width):
            if bpp == 32:
                b, g, r, a = data[src + x * 4 : src + x * 4 + 4]
                any_alpha |= a != 0
            elif bpp == 24:
                b, g, r = data[src + x * 3 : src + x * 3 + 3]
                a = 255
            elif bpp in (1, 4, 8):
                per = 8 // bpp
                byte = data[src + x // per]
                idx = (byte >> (8 - bpp * (x % per + 1))) & ((1 << bpp) - 1)
                b, g, r = palette[idx][:3] if idx < len(palette) else (0, 0, 0)
                a = 255
            else:
                raise ValueError("unsupported BMP depth")
            o = (y * width + x) * 4
            out[o : o + 4] = bytes((r, g, b, a))
    if bpp != 32 or not any_alpha:  # the mask says which pixels are see-through
        for y in range(height):
            src = mask_at + (height - 1 - y) * mask_row
            if src + mask_row > len(data):
                break
            for x in range(width):
                if (data[src + x // 8] >> (7 - x % 8)) & 1:
                    out[(y * width + x) * 4 + 3] = 0
    return Pixels(width, height, out)


def decode_ico(data: bytes) -> Pixels:
    """The largest picture of an ICO file (each one is a PNG or a BMP)."""
    _reserved, kind, count = struct.unpack("<HHH", data[:6])
    if kind != 1 or not 0 < count < 64:
        raise ValueError("not an ICO")
    best = None
    for i in range(count):
        w, h, _c, _r, _p, bpp, size, offset = struct.unpack("<BBBBHHII", data[6 + i * 16 : 22 + i * 16])
        w, h = w or 256, h or 256
        score = (w * h, bpp)
        if offset + size <= len(data) and (best is None or score > best[0]):
            best = (score, w, h, data[offset : offset + size])
    if best is None:
        raise ValueError("empty ICO")
    _score, w, h, body = best
    return decode_png(body) if body.startswith(b"\x89PNG") else _decode_bmp(body, w, h)


def decode(data: bytes) -> Pixels:
    if data.startswith(b"\x89PNG"):
        return decode_png(data)
    if data[:4] == b"\x00\x00\x01\x00":
        return decode_ico(data)
    raise ValueError("not a PNG or an ICO")


# ---------------------------------------------------------------- scaling and colour

def trim(p: Pixels) -> Pixels:
    """Cut away fully transparent margins, keeping the picture square."""
    xs, ys = [], []
    for y in range(p.height):
        for x in range(p.width):
            if p.rgba[(y * p.width + x) * 4 + 3] > 16:
                xs.append(x)
                ys.append(y)
    if not xs:
        return p
    x0, x1, y0, y1 = min(xs), max(xs) + 1, min(ys), max(ys) + 1
    side = max(x1 - x0, y1 - y0)
    if side >= max(p.width, p.height) - 1:
        return p
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    x0, y0 = max(0, min(p.width - side, cx - side // 2)), max(0, min(p.height - side, cy - side // 2))
    out = bytearray(side * side * 4)
    for y in range(side):
        src = ((y0 + y) * p.width + x0) * 4
        out[y * side * 4 : (y + 1) * side * 4] = p.rgba[src : src + side * 4]
    return Pixels(side, side, out)


def resize(p: Pixels, size: int) -> Pixels:
    """Scale to size × size: area averaging down, bilinear up; colours are weighted by
    alpha, so transparent pixels do not darken the edges."""
    sw, sh = p.width, p.height
    src = p.rgba
    out = bytearray(size * size * 4)
    if sw >= size and sh >= size:
        fx, fy = sw / size, sh / size
        for y in range(size):
            ya, yb = y * fy, (y + 1) * fy
            for x in range(size):
                xa, xb = x * fx, (x + 1) * fx
                r = g = b = a = wsum = 0.0
                for sy in range(int(ya), min(sh, int(yb + 0.999))):
                    wy = min(yb, sy + 1) - max(ya, sy)
                    if wy <= 0:
                        continue
                    for sx in range(int(xa), min(sw, int(xb + 0.999))):
                        wx = min(xb, sx + 1) - max(xa, sx)
                        if wx <= 0:
                            continue
                        w = wx * wy
                        o = (sy * sw + sx) * 4
                        al = src[o + 3] * w
                        r += src[o] * al
                        g += src[o + 1] * al
                        b += src[o + 2] * al
                        a += al
                        wsum += w
                o = (y * size + x) * 4
                if a > 0:
                    out[o : o + 4] = bytes((round(r / a), round(g / a), round(b / a), round(a / wsum)))
        return Pixels(size, size, out)
    for y in range(size):
        v = max(0.0, (y + 0.5) * sh / size - 0.5)
        y0 = min(int(v), sh - 1)
        y1, ty = min(y0 + 1, sh - 1), v - int(v)
        for x in range(size):
            u = max(0.0, (x + 0.5) * sw / size - 0.5)
            x0 = min(int(u), sw - 1)
            x1, tx = min(x0 + 1, sw - 1), u - int(u)
            acc = [0.0, 0.0, 0.0, 0.0]
            for sx, sy, w in ((x0, y0, (1 - tx) * (1 - ty)), (x1, y0, tx * (1 - ty)), (x0, y1, (1 - tx) * ty), (x1, y1, tx * ty)):
                o = (sy * sw + sx) * 4
                al = src[o + 3] * w
                acc[0] += src[o] * al
                acc[1] += src[o + 1] * al
                acc[2] += src[o + 2] * al
                acc[3] += al
            o = (y * size + x) * 4
            if acc[3] > 0:
                out[o : o + 4] = bytes((round(acc[0] / acc[3]), round(acc[1] / acc[3]), round(acc[2] / acc[3]), round(acc[3])))
    return Pixels(size, size, out)


def _contrast_on_white(r: float, g: float, b: float) -> float:
    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 1.05 / (0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b) + 0.05)


def readable(colour: str) -> str:
    """The colour, darkened only as much as it takes to read on a white tile (contrast 3:1)."""
    r, g, b = (int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    h, light, s = colorsys.rgb_to_hls(r, g, b)
    while _contrast_on_white(r, g, b) < 3.0 and light > 0.05:
        light -= 0.02
        r, g, b = colorsys.hls_to_rgb(h, light, s)
    return f"#{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}"


def main_colour(p: Pixels) -> str:
    """The most common vivid colour of an icon; "" when it is grey, black or white."""
    buckets: dict[int, list[float]] = {}
    step = max(1, (p.width * p.height) // 4096)
    for i in range(0, p.width * p.height, step):
        r, g, b, a = p.rgba[i * 4 : i * 4 + 4]
        if a < 128:
            continue
        h, light, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if s < 0.25 or light < 0.12 or light > 0.92:
            continue
        acc = buckets.setdefault(int(h * 24) % 24, [0.0, 0.0, 0.0, 0.0])
        weight = s * (1 - abs(light - 0.5))
        acc[0] += r * weight
        acc[1] += g * weight
        acc[2] += b * weight
        acc[3] += weight
    if not buckets:
        return ""
    rs, gs, bs, w = max(buckets.values(), key=lambda v: v[3])
    if w < 0.02 * (p.width * p.height / step):
        return ""
    return readable(f"#{round(rs / w):02X}{round(gs / w):02X}{round(bs / w):02X}")


def parse_colour(value: str) -> str:
    """A CSS colour from a theme-color: #rgb, #rrggbb or rgb(); "" — none or too pale/grey."""
    value = value.strip().lower()
    m = re.fullmatch(r"#([0-9a-f]{3}|[0-9a-f]{6})", value)
    if m:
        hx = m.group(1)
        hx = "".join(c * 2 for c in hx) if len(hx) == 3 else hx
        rgb = tuple(int(hx[i : i + 2], 16) for i in (0, 2, 4))
    else:
        m = re.fullmatch(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+).*\)", value)
        if not m:
            return ""
        rgb = tuple(min(255, int(v)) for v in m.groups())
    _h, light, s = colorsys.rgb_to_hls(*(v / 255 for v in rgb))
    if s < 0.2 or light > 0.9 or light < 0.1:  # white, black and grey say nothing about the site
        return ""
    return readable("#{:02X}{:02X}{:02X}".format(*rgb))


def icon_png(data: bytes) -> tuple[bytes, str]:
    """(a PNG of ICON_SIZE, the main colour) from the bytes of a PNG or an ICO."""

    pixels = resize(trim(decode(data)), ICON_SIZE)
    return png(pixels.width, pixels.height, pixels.rgba), main_colour(pixels)


def shrink_png(data: bytes, size: int) -> bytes:
    """The stored icon (a PNG) scaled to size × size, as a PNG — what a tile shows.
    Pure Python, so a background thread does it, not the window."""

    pixels = resize(decode_png(data), size)
    return png(pixels.width, pixels.height, pixels.rgba)


# ---------------------------------------------------------------- the page

class _HeadParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.icons: list[tuple[str, str, str, str]] = []  # (rel, href, sizes, type)
        self.manifest = ""
        self.theme = ""
        self.manifest_url = ""

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "link":
            rel = a.get("rel", "").lower()
            if "icon" in rel.split() or "apple-touch-icon" in rel or "apple-touch-icon-precomposed" in rel:
                self.icons.append((rel, a.get("href", ""), a.get("sizes", ""), a.get("type", "").lower()))
            elif rel == "manifest":
                self.manifest = a.get("href", "")
        elif tag == "meta" and a.get("name", "").lower() == "theme-color" and not self.theme:
            if "dark" not in a.get("media", ""):
                self.theme = a.get("content", "")


def _size_of(sizes: str) -> int:
    m = re.search(r"(\d+)x(\d+)", sizes or "")
    return int(m.group(1)) if m else 0


def _rank(size: int) -> tuple[int, int]:
    """Prefer the smallest icon not smaller than ICON_SIZE, then the largest smaller one."""
    if size >= ICON_SIZE:
        return (0, size)
    return (1, -size) if size else (2, 0)


def candidates(page_url: str, parser: _HeadParser, manifest: dict | None) -> list[str]:
    """Icon addresses, the best first; SVG ones are skipped."""
    found: list[tuple[tuple, str]] = []
    for rel, href, sizes, kind in parser.icons:
        if not href or kind == "image/svg+xml" or href.lower().split("?")[0].endswith(".svg") or "mask-icon" in rel:
            continue
        size = _size_of(sizes) or (180 if "apple-touch" in rel else 0)
        found.append((_rank(size), urljoin(page_url, href)))
    for icon in (manifest or {}).get("icons", []) if isinstance(manifest, dict) else []:
        if not isinstance(icon, dict) or not icon.get("src"):
            continue
        src = str(icon["src"])
        if "svg" in str(icon.get("type", "")) or src.lower().split("?")[0].endswith(".svg"):
            continue
        found.append((_rank(_size_of(str(icon.get("sizes", "")))), urljoin(parser.manifest_url, src)))
    found.sort(key=lambda t: t[0])
    out = [u for _r, u in found]
    root = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
    for fallback in ("/apple-touch-icon.png", "/favicon.ico"):
        out.append(root + fallback)
    return list(dict.fromkeys(out))  # each address once, in order


def is_private_host(host: str, resolve=socket.gethostbyname) -> bool:
    """Loopback, link-local, private or unresolvable: a redirect there from a public site
    would make the icon fetch probe the user's own network."""
    host = host.strip("[]").lower()
    if not host or host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ip = ipaddress.ip_address(resolve(host))
        except (OSError, ValueError):
            return True
    return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_unspecified or ip.is_multicast


def redirect_allowed(from_url: str, to_url: str, resolve=socket.gethostbyname) -> bool:
    """A redirect of an icon fetch is followed only to http(s), and into a private network
    only back to the bookmark's own host and port: a site must not be able to make the
    app knock on other machines or ports of the user's network."""
    to, src = urlparse(to_url), urlparse(from_url)
    if to.scheme not in ("http", "https"):
        return False
    if is_private_host(to.hostname or "", resolve):
        return (to.hostname or "").lower() == (src.hostname or "").lower() and to.port == src.port
    return True


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not redirect_allowed(req.full_url, newurl):
            return None  # urllib raises the HTTPError of the redirect; fetch() tries the next candidate
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_SafeRedirects())


def _get(url: str, limit: int, opener) -> tuple[bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})  # noqa: S310 — the user's own bookmark, http(s) only
    with opener(req, timeout=TIMEOUT) as resp:  # noqa: S310 — the user's own bookmark
        return resp.read(limit + 1)[:limit], resp.geturl()


def fetch(url: str, opener=_OPENER.open) -> SiteInfo:
    """The icon and the colour of the site at `url`. Never raises: a site that does not
    answer or gives nothing usable yields an empty SiteInfo."""
    info = SiteInfo()
    if urlparse(url).scheme not in ("http", "https"):
        return info
    parser = _HeadParser()
    page_url = url
    try:
        body, page_url = _get(url, PAGE_LIMIT, opener)
        parser.feed(body.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001, S110 — a login wall, a timeout, a broken page: try the usual places
        pass
    manifest = None
    if parser.manifest:
        parser.manifest_url = urljoin(page_url, parser.manifest)
        try:
            manifest = json.loads(_get(parser.manifest_url, PAGE_LIMIT, opener)[0].decode("utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            manifest = None
    theme = parse_colour(parser.theme)
    if not theme and isinstance(manifest, dict):
        theme = parse_colour(str(manifest.get("theme_color", "")))
    for icon_url in candidates(page_url, parser, manifest)[:4]:
        try:
            data = _get(icon_url, ICON_LIMIT, opener)[0]
            info.icon, colour = icon_png(data)
        except Exception:  # noqa: BLE001, S112 — not an image, an SVG behind a .png name, a 404
            continue
        info.colour = theme or colour
        return info
    info.colour = theme
    return info
