"""Site icons and colours for bookmarks; Windows shortcuts in routines; card height in notes."""
import base64
import io
import struct
import unittest

from daykeeper import siteicon
from daykeeper.bookmarks import DEFAULT_COLOUR, Bookmark, Bookmarks
from daykeeper.routines import Routine, launch_plan, read_shortcut
from daykeeper.theme import png


def solid(width, height, rgba):
    return png(width, height, bytearray(bytes(rgba) * width * height))


def ico_bmp32(size, rgba):
    """An ICO with one 32-bit BMP picture of one colour."""
    r, g, b, a = rgba
    header = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    pixels = bytes((b, g, r, a)) * size * size
    mask = b"\x00" * (((size + 31) // 32) * 4) * size
    body = header + pixels + mask
    return struct.pack("<HHH", 0, 1, 1) + struct.pack("<BBBBHHII", size, size, 0, 0, 1, 32, len(body), 22) + body


class FakeResponse(io.BytesIO):
    def __init__(self, data, url):
        super().__init__(data)
        self.url = url

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class DecodeTest(unittest.TestCase):
    def test_png_round_trip_and_resize(self):
        p = siteicon.decode_png(solid(10, 6, (200, 30, 40, 255)))
        self.assertEqual((p.width, p.height), (10, 6))
        self.assertEqual(tuple(p.rgba[:4]), (200, 30, 40, 255))
        big = siteicon.resize(p, 64)
        self.assertEqual((big.width, big.height), (64, 64))
        self.assertEqual(tuple(big.rgba[-4:]), (200, 30, 40, 255))
        small = siteicon.resize(siteicon.decode_png(solid(128, 128, (0, 128, 0, 255))), 16)
        self.assertEqual(tuple(small.rgba[:4]), (0, 128, 0, 255))

    def test_ico_with_a_bmp_picture(self):
        p = siteicon.decode(ico_bmp32(16, (10, 120, 220, 255)))
        self.assertEqual((p.width, p.height), (16, 16))
        self.assertEqual(tuple(p.rgba[:4]), (10, 120, 220, 255))

    def test_not_an_image(self):
        with self.assertRaises(ValueError):
            siteicon.decode(b"<svg xmlns='http://www.w3.org/2000/svg'/>")

    def test_shrink_png_gives_a_png_of_the_asked_size(self):
        small = siteicon.shrink_png(solid(64, 64, (0, 128, 0, 255)), 26)
        self.assertTrue(small.startswith(b"\x89PNG"))
        p = siteicon.decode_png(small)
        self.assertEqual((p.width, p.height), (26, 26))
        self.assertEqual(tuple(p.rgba[:4]), (0, 128, 0, 255))
        with self.assertRaises(ValueError):
            siteicon.shrink_png(b"not a png", 26)

    def test_transparent_margins_are_cut(self):
        rgba = bytearray(20 * 20 * 4)
        for y in range(5, 15):
            for x in range(5, 15):
                rgba[(y * 20 + x) * 4 : (y * 20 + x) * 4 + 4] = bytes((255, 0, 0, 255))
        self.assertEqual(siteicon.trim(siteicon.Pixels(20, 20, rgba)).width, 10)


class ColourTest(unittest.TestCase):
    def test_theme_colour(self):
        self.assertEqual(siteicon.parse_colour("#E24329"), "#E24329")
        self.assertEqual(siteicon.parse_colour("#fff"), "", "white says nothing about the site")
        self.assertEqual(siteicon.parse_colour("#333333"), "", "grey says nothing either")
        self.assertTrue(siteicon.parse_colour("rgb(0, 160, 80)"))
        self.assertEqual(siteicon.parse_colour("var(--brand)"), "")

    def test_pale_colours_are_darkened_to_read_on_white(self):
        r, g, b = (int(siteicon.parse_colour("#9AD0FF")[i : i + 2], 16) for i in (1, 3, 5))
        self.assertLess((r + g + b) / 3, 160)

    def test_main_colour_of_an_icon(self):
        self.assertTrue(siteicon.main_colour(siteicon.decode_png(solid(8, 8, (230, 0, 40, 255)))))
        self.assertEqual(siteicon.main_colour(siteicon.decode_png(solid(8, 8, (20, 20, 20, 255)))), "")


class FetchTest(unittest.TestCase):
    def test_the_page_icon_and_theme_colour(self):
        page = b"""<html><head>
            <link rel="icon" type="image/svg+xml" href="/logo.svg">
            <link rel="apple-touch-icon" href="/touch.png">
            <meta name="theme-color" content="#E24329">
        </head></html>"""
        files = {"https://git.example.com/": page, "https://git.example.com/touch.png": solid(180, 180, (10, 10, 200, 255))}
        asked = []

        def opener(req, timeout):
            asked.append(req.full_url)
            if req.full_url not in files:
                raise OSError("404")
            return FakeResponse(files[req.full_url], req.full_url)

        info = siteicon.fetch("https://git.example.com/", opener=opener)
        self.assertNotIn("https://git.example.com/logo.svg", asked, "SVG has no decoder here")
        self.assertEqual(info.colour, "#E24329")
        p = siteicon.decode_png(info.icon)
        self.assertEqual((p.width, p.height), (siteicon.ICON_SIZE, siteicon.ICON_SIZE))

    def test_a_silent_site_gives_nothing_and_does_not_raise(self):
        def opener(req, timeout):
            raise TimeoutError

        info = siteicon.fetch("https://down.example.com", opener=opener)
        self.assertIsNone(info.icon)
        self.assertEqual(info.colour, "")

    def test_the_favicon_ico_when_the_page_names_none(self):
        def opener(req, timeout):
            if req.full_url.endswith("/favicon.ico"):
                return FakeResponse(ico_bmp32(32, (0, 150, 70, 255)), req.full_url)
            return FakeResponse(b"<html></html>", req.full_url)

        info = siteicon.fetch("https://plain.example.com/app", opener=opener)
        self.assertIsNotNone(info.icon)
        self.assertTrue(info.colour)

    def test_only_web_addresses(self):
        self.assertIsNone(siteicon.fetch("file:///C:/x", opener=None).icon)


class BookmarkLookTest(unittest.TestCase):
    def test_site_colour_unless_the_user_picked_one(self):
        bm = Bookmark(title="Git", url="https://git.example.com")
        self.assertEqual(bm.colour(), DEFAULT_COLOUR)
        bm.site_colour = "#FC6D26"
        self.assertEqual(bm.colour(), "#FC6D26")
        bm.color = "green"
        self.assertNotEqual(bm.colour(), "#FC6D26")

    def test_kept_in_the_vault_and_asked_again_later(self):
        bm = Bookmark(title="Git", url="https://git.example.com", favicon=base64.b64encode(b"x").decode(), site_colour="#FC6D26", site_checked=1000.0)
        back = Bookmark.from_dict(bm.to_dict())
        self.assertEqual((back.favicon, back.site_colour, back.site_checked), (bm.favicon, bm.site_colour, 1000.0))
        self.assertEqual(Bookmark.from_dict({**bm.to_dict(), "site_colour": "red; x"}).site_colour, "")
        self.assertFalse(back.needs_site_info(now=1000.0 + 3600))
        self.assertTrue(back.needs_site_info(now=1000.0 + 40 * 86400))
        self.assertFalse(Bookmark(url="mailto:a@b.c").needs_site_info())

    def test_a_new_address_forgets_the_old_look(self):
        bms = Bookmarks()
        bm = bms.add("Git", "https://git.example.com")
        bm.favicon, bm.site_colour, bm.site_checked = "eA==", "#FC6D26", 5.0
        bm.set_small_icon(b"y", 26)
        bms.update(bm, url="https://git.example.com/group")
        self.assertEqual(bm.favicon, "eA==", "same site — same icon")
        bms.update(bm, url="https://other.example.com")
        self.assertEqual((bm.favicon, bm.site_colour, bm.site_checked), ("", "", 0.0))
        self.assertEqual((bm.favicon_small, bm.favicon_small_px), ("", 0))

    def test_the_small_icon_is_kept_and_an_older_file_has_none(self):
        bm = Bookmark(title="Git", url="https://git.example.com", favicon="eA==")
        self.assertEqual(bm.small_icon_at(26), "", "an older file: the icon is shrunk later")
        bm.set_small_icon(b"small", 26)
        back = Bookmark.from_dict(bm.to_dict())
        self.assertEqual((back.favicon_small, back.favicon_small_px), (base64.b64encode(b"small").decode(), 26))
        self.assertEqual(back.small_icon_at(26), back.favicon_small)
        self.assertEqual(back.small_icon_at(39), "", "another screen scale needs another size")
        older = {k: v for k, v in bm.to_dict().items() if not k.startswith("favicon_small")}
        self.assertEqual((Bookmark.from_dict(older).favicon_small, Bookmark.from_dict(older).favicon_small_px), ("", 0))
        self.assertEqual(Bookmark.from_dict({**older, "favicon_small_px": None}).favicon_small_px, 0)
        bm.set_small_icon(b"", 26)
        self.assertEqual((bm.favicon_small, bm.favicon_small_px), ("", 0))


def lnk(target: str, args: str = "", cwd: str = "") -> bytes:
    """A minimal shortcut: LinkInfo with a local path, then the working folder and arguments."""
    flags = 0x02 | 0x80 | (0x10 if cwd else 0) | (0x20 if args else 0)
    header = struct.pack("<I16sI", 0x4C, b"\x01\x14\x02" + b"\x00" * 13, flags) + b"\x00" * (0x4C - 24)
    path = target.encode("latin-1") + b"\x00"
    info_head = 0x1C
    info = struct.pack("<7I", info_head + 4 + len(path) + 1, info_head, 1, info_head, info_head + 4, 0, info_head + 4 + len(path))
    info += b"\x00" * 4 + path + b"\x00"
    strings = b""
    for value in (cwd, args):
        if value:
            strings += struct.pack("<H", len(value)) + value.encode("utf-16-le")
    return header + info + strings


class ShortcutTest(unittest.TestCase):
    def test_read_a_shortcut(self):
        sc = read_shortcut(lnk(r"C:\Windows\System32\cmd.exe", "/c run.bat", r"D:\work"))
        self.assertEqual(sc.target, r"C:\Windows\System32\cmd.exe")
        self.assertEqual(sc.args, "/c run.bat")
        self.assertEqual(sc.cwd, r"D:\work")
        self.assertIsNone(read_shortcut(b"not a shortcut"))

    def test_a_shortcut_to_a_script_runs_and_is_waited_for(self):
        r = Routine(name="Sync", kind="file", target=r"C:\Users\me\Desktop\sync.lnk")
        fake = lambda _p: read_shortcut(lnk(r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-File D:\\s\\sync.ps1", r"D:\s"))  # noqa: E731
        plan = launch_plan(r, platform="win32", which=lambda _n: None, exists=lambda _p: True, shortcut=fake)
        self.assertEqual(plan.action, "run")
        self.assertEqual(plan.argv, [r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe", "-File", "D:\\s\\sync.ps1"])
        self.assertEqual(plan.cwd, r"D:\s")

    def test_a_shortcut_to_a_document_just_opens(self):
        r = Routine(name="Doc", kind="file", target=r"C:\x\report.lnk")
        fake = lambda _p: read_shortcut(lnk(r"C:\x\report.docx"))  # noqa: E731
        self.assertEqual(launch_plan(r, platform="win32", exists=lambda _p: True, shortcut=fake).action, "open_path")

    def test_an_unreadable_shortcut_is_started_by_windows_and_waited_for(self):
        r = Routine(name="App", kind="script", target=r"C:\x\app.lnk")
        plan = launch_plan(r, platform="win32", exists=lambda _p: True, shortcut=lambda _p: None)
        self.assertIsNone(plan.argv)
        self.assertTrue(plan.shell.startswith('start "" /wait "'), plan.shell)  # quoted: a name with & is one path to cmd

    def test_a_script_runner_alone_is_refused(self):
        r = Routine(name="PS", kind="script", target=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")
        self.assertEqual(launch_plan(r, platform="win32", exists=lambda _p: True).problem, "interpreter")
        r.args = "-File D:\\s\\sync.ps1"
        self.assertEqual(launch_plan(r, platform="win32", exists=lambda _p: True).problem, "")


if __name__ == "__main__":
    unittest.main()
