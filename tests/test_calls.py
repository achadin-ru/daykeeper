"""Call → note: finding whisper.cpp, the command lines, reading whisper's output and the
assistant's digest, the note card and the transcript file. Fakes for PATH and the file
system; temporary folders only. No whisper, ffmpeg or assistant is ever started."""

import os
import struct
import tempfile
import unittest
from pathlib import Path

from daykeeper import calls
from daykeeper.i18n import language, set_language

WIN_ENV = {"LOCALAPPDATA": r"C:\Users\sam\AppData\Local"}
GECKIT_WIN = r"C:\Users\sam\AppData\Local\Programs\geckit\resources\whisper\whisper-cli.exe"


def which_of(found: dict):
    return lambda name: found.get(name)


def files(*paths):
    present = set(paths)
    return lambda p: p in present


class WhisperBinaryTest(unittest.TestCase):
    def test_the_setting_comes_first(self):
        got = calls.whisper_binary({calls.SETTING_BINARY: r"D:\tools\whisper-cli.exe"}, which_of({"whisper-cli": "/usr/bin/whisper-cli"}), files(r"D:\tools\whisper-cli.exe", GECKIT_WIN), WIN_ENV, "win32")
        self.assertEqual(got, r"D:\tools\whisper-cli.exe")

    def test_a_missing_setting_falls_through_to_path(self):
        got = calls.whisper_binary({calls.SETTING_BINARY: r"D:\gone.exe"}, which_of({"whisper-cli": r"C:\bin\whisper-cli.exe"}), files(GECKIT_WIN), WIN_ENV, "win32")
        self.assertEqual(got, r"C:\bin\whisper-cli.exe")

    def test_homebrew_name_on_path(self):
        got = calls.whisper_binary({}, which_of({"whisper-cpp": "/opt/homebrew/bin/whisper-cpp"}), files(), {}, "darwin")
        self.assertEqual(got, "/opt/homebrew/bin/whisper-cpp")

    def test_geckit_copy_on_windows(self):
        self.assertEqual(calls.whisper_binary({}, which_of({}), files(GECKIT_WIN), WIN_ENV, "win32"), GECKIT_WIN)

    def test_geckit_bundle_on_macos(self):
        user_bundle = "/Users/sam/Applications/GeckIt.app/Contents/Resources/whisper/whisper-cli"
        self.assertEqual(calls.whisper_binary({}, which_of({}), files(user_bundle), {"HOME": "/Users/sam"}, "darwin"), user_bundle)
        system = "/Applications/GeckIt.app/Contents/Resources/whisper/whisper-cli"
        self.assertEqual(calls.whisper_binary({}, which_of({}), files(system, user_bundle), {"HOME": "/Users/sam"}, "darwin"), system)

    def test_nothing_found(self):
        self.assertIsNone(calls.whisper_binary({}, which_of({}), files(), WIN_ENV, "win32"))
        self.assertIsNone(calls.whisper_binary({}, which_of({}), files(), {}, "win32"))
        self.assertIsNone(calls.whisper_binary({}, which_of({}), files(), {"HOME": "/home/sam"}, "linux"))


class WhisperModelTest(unittest.TestCase):
    folder = r"C:\w"

    def listing(self, tree: dict):
        def listdir(path):
            if path not in tree:
                raise FileNotFoundError(path)
            return tree[path]

        return listdir

    def test_the_setting_comes_first(self):
        got = calls.whisper_model({calls.SETTING_MODEL: r"D:\m\ggml-small.bin"}, r"C:\w\whisper-cli.exe", self.listing({}), files(r"D:\m\ggml-small.bin"), "win32")
        self.assertEqual(got, r"D:\m\ggml-small.bin")

    def test_the_best_model_next_to_the_binary(self):
        names = ["readme.txt", "ggml-tiny.bin", "ggml-medium.bin", "ggml-base.bin", "whisper-cli.exe"]
        present = files(*(rf"C:\w\{n}" for n in names))
        got = calls.whisper_model({}, r"C:\w\whisper-cli.exe", self.listing({self.folder: names}), present, "win32")
        self.assertEqual(got, r"C:\w\ggml-medium.bin")

    def test_models_subfolder(self):
        listdir = self.listing({self.folder: ["whisper-cli.exe"], r"C:\w\models": ["ggml-base.en.bin"]})
        got = calls.whisper_model({}, r"C:\w\whisper-cli.exe", listdir, files(r"C:\w\models\ggml-base.en.bin"), "win32")
        self.assertEqual(got, r"C:\w\models\ggml-base.en.bin")

    def test_no_model(self):
        self.assertIsNone(calls.whisper_model({}, r"C:\w\whisper-cli.exe", self.listing({}), files(), "win32"))
        self.assertIsNone(calls.whisper_model({}, None, self.listing({}), files(), "win32"))


class ArgvTest(unittest.TestCase):
    def test_transcribe(self):
        self.assertEqual(
            calls.transcribe_argv("whisper-cli", "m.bin", "call.wav", "en", 4),
            ["whisper-cli", "-m", "m.bin", "-f", "call.wav", "-l", "en", "-t", "4", "-nt", "-np"],
        )

    def test_transcribe_defaults(self):
        argv = calls.transcribe_argv("w", "m", "a.wav", "", 0)
        self.assertEqual(argv[argv.index("-l") + 1], calls.DEFAULT_LANGUAGE)
        self.assertGreaterEqual(int(argv[argv.index("-t") + 1]), 1)

    def test_transcribe_language_is_a_code_or_auto(self):
        for given, expected in (("en", "en"), (" RU ", "ru"), ("yue", "yue"), ("auto", "auto"), (None, "auto"),
                                ("-h", "auto"), ("--help", "auto"), ("e", "auto"), ("engl", "auto"), ("e1", "auto"),
                                ("en -t 64", "auto"), ("ру", "auto")):
            with self.subTest(given=given):
                argv = calls.transcribe_argv("w", "m", "a.wav", given, 2)  # type: ignore[arg-type]
                self.assertEqual(argv[argv.index("-l") + 1], expected)
                self.assertEqual(len(argv), 11)

    def test_default_threads(self):
        self.assertEqual(calls.default_threads(1), 1)
        self.assertEqual(calls.default_threads(8), 4)
        self.assertEqual(calls.default_threads(64), 8)
        self.assertEqual(calls.default_threads(0), 1)

    def test_ffmpeg_only_when_found(self):
        self.assertIsNone(calls.ffmpeg_argv("a.mp3", "a.wav", which_of({})))
        argv = calls.ffmpeg_argv("a.mp3", "a.wav", which_of({"ffmpeg": "/usr/bin/ffmpeg"}))
        assert argv is not None
        self.assertEqual(argv[0], "/usr/bin/ffmpeg")
        self.assertEqual(argv[argv.index("-i") + 1], "file:a.mp3")
        self.assertEqual(argv[argv.index("-ar") + 1], "16000")
        self.assertEqual(argv[argv.index("-ac") + 1], "1")
        self.assertEqual(argv[-1], "a.wav")
        self.assertIn("-nostdin", argv)

    def test_ffmpeg_reads_the_input_as_a_file_only(self):
        found = which_of({"ffmpeg": "ffmpeg"})
        for src in ("concat:a.mp3|b.mp3", "http://example.com/a.mp3", "-i.mp3", r"C:\Users\me\call.mp3", "/home/me/call.mp3"):
            with self.subTest(src=src):
                argv = calls.ffmpeg_argv(src, "out.wav", found)
                assert argv is not None
                self.assertEqual(argv[argv.index("-i") + 1], "file:" + src)
        argv = calls.ffmpeg_argv(Path("dir") / "call.mp3", "out.wav", found)
        assert argv is not None
        self.assertEqual(argv[argv.index("-i") + 1], "file:" + str(Path("dir") / "call.mp3"))


def wav_head(rate=16000, channels=1, bits=16, fmt=1) -> bytes:
    block = channels * bits // 8
    return b"RIFF" + struct.pack("<I", 36) + b"WAVE" + b"fmt " + struct.pack("<IHHIIHH", 16, fmt, channels, rate, rate * block, block, bits)


class ConversionTest(unittest.TestCase):
    def test_compressed_audio(self):
        for name in ("a.mp3", "b.M4A", "c.ogg", "d.webm"):
            self.assertTrue(calls.needs_conversion(name), name)

    def test_wav_and_text(self):
        self.assertFalse(calls.needs_conversion("a.wav"))
        self.assertFalse(calls.needs_conversion("notes.txt"))
        self.assertTrue(calls.is_transcript("notes.md"))
        self.assertFalse(calls.is_transcript("a.wav"))
        self.assertTrue(calls.is_audio("a.wav"))
        self.assertTrue(calls.is_audio("a.mp3"))

    def test_wav_by_its_header(self):
        self.assertFalse(calls.needs_conversion("a.wav", wav_head()))
        self.assertTrue(calls.needs_conversion("a.wav", wav_head(rate=44100)))
        self.assertTrue(calls.needs_conversion("a.wav", wav_head(channels=2)))
        self.assertTrue(calls.needs_conversion("a.wav", wav_head(bits=24)))
        self.assertTrue(calls.needs_conversion("a.wav", wav_head(fmt=3)))
        self.assertTrue(calls.needs_conversion("a.wav", b"not a wav"))


class TranscriptTest(unittest.TestCase):
    def test_plain_segments(self):
        out = " Good morning, everyone.\n\n Let's start with the release.\n"
        self.assertEqual(calls.parse_transcript(out), "Good morning, everyone. Let's start with the release.")

    def test_timestamps_are_stripped(self):
        out = "[00:00:00.000 --> 00:00:02.500]   Hello team.\n[00:00:02.500 --> 00:00:05.000]  The demo is on Friday.\n"
        self.assertEqual(calls.parse_transcript(out), "Hello team. The demo is on Friday.")

    def test_subtitle_format(self):
        out = "WEBVTT\n\n1\n00:00.000 --> 00:02.000\nFirst line.\n\n2\n00:02.000 --> 00:04.000\nSecond line.\n"
        self.assertEqual(calls.parse_transcript(out), "First line. Second line.")

    def test_markers_are_dropped(self):
        out = "[BLANK_AUDIO]\n (music)\n Okay [Music] so the plan is ready.\n *laughter*\n[ Silence ]\n(тишина)\n"
        self.assertEqual(calls.parse_transcript(out), "Okay so the plan is ready.")

    def test_speech_in_brackets_stays(self):
        out = "Ask Mia (she owns the design) about it. [OK?]\n"
        self.assertEqual(calls.parse_transcript(out), "Ask Mia (she owns the design) about it. [OK?]")

    def test_empty(self):
        self.assertEqual(calls.parse_transcript(""), "")
        self.assertEqual(calls.parse_transcript("[BLANK_AUDIO]\n"), "")


CLEAN = '{"summary": "The team planned the garden app demo.", "notes": ["Demo on Friday", "Use the new icons"], "tasks": [{"text": "Prepare the slides", "owner": "Mia", "due": "Thursday"}, {"text": "Book the room", "owner": "", "due": ""}]}'


class DigestTest(unittest.TestCase):
    def test_prompt(self):
        prompt = calls.digest_prompt("  We agreed to ship on Friday.  ")
        self.assertIn("JSON only", prompt)
        for key in ('"summary"', '"notes"', '"tasks"', '"owner"', '"due"'):
            self.assertIn(key, prompt)
        self.assertTrue(prompt.rstrip().endswith("We agreed to ship on Friday."))

    def test_clean(self):
        d = calls.parse_digest(CLEAN)
        self.assertEqual(d.summary, "The team planned the garden app demo.")
        self.assertEqual(d.notes, ["Demo on Friday", "Use the new icons"])
        self.assertEqual(d.tasks, [calls.Task("Prepare the slides", "Mia", "Thursday"), calls.Task("Book the room")])

    def test_prose_and_fence(self):
        text = "Sure! Here is the digest:\n```json\n" + CLEAN + "\n```\nLet me know if you need more."
        self.assertEqual(calls.parse_digest(text), calls.parse_digest(CLEAN))
        self.assertEqual(calls.parse_digest("Here it is: " + CLEAN + " Done."), calls.parse_digest(CLEAN))

    def test_braces_in_prose_before_the_object(self):
        text = "Format {like this}: " + CLEAN
        self.assertEqual(calls.parse_digest(text).summary, "The team planned the garden app demo.")

    def test_no_tasks_and_missing_fields(self):
        d = calls.parse_digest('{"summary": "Short sync.", "notes": []}')
        self.assertEqual((d.summary, d.notes, d.tasks), ("Short sync.", [], []))
        d = calls.parse_digest('{"notes": "Only one note"}')
        self.assertEqual((d.summary, d.notes, d.tasks), ("", ["Only one note"], []))

    def test_tasks_as_strings(self):
        d = calls.parse_digest('{"summary": "x", "tasks": ["Fix the login page", "", 42, {"text": "Call Leo", "owner": null}]}')
        self.assertEqual(d.tasks, [calls.Task("Fix the login page"), calls.Task("42"), calls.Task("Call Leo")])

    def test_broken_is_empty_not_an_error(self):
        for text in ("", "no json here", '{"summary": "cut off', "[1, 2, 3]", "```json\n{broken\n```", None):
            d = calls.parse_digest(text)  # type: ignore[arg-type]
            self.assertTrue(d.is_empty(), repr(text))

    def test_deep_nesting_is_empty_not_an_error(self):
        deep = "[" * 100_000 + "]" * 100_000  # past the parser's stack limit: a RecursionError inside
        for text in ('{"a": ' + deep + "}", '{"a": ' + "[" * 100_000, "```json\n" + '{"a": ' + deep + "}\n```"):
            with self.subTest(size=len(text)):
                self.assertTrue(calls.parse_digest(text).is_empty())
        self.assertEqual(calls.parse_digest('{"x": ' + deep + '} {"summary": "ok"}').summary, "ok", "the next object is still found")

    def test_odd_types(self):
        d = calls.parse_digest('{"summary": {"a": 1}, "notes": 5, "tasks": "Write the report"}')
        self.assertEqual((d.summary, d.notes, d.tasks), ("", [], [calls.Task("Write the report")]))


class NoteTest(unittest.TestCase):
    def setUp(self):
        self.lang = language()
        self.addCleanup(set_language, self.lang)
        set_language("en")

    def test_card(self):
        draft = calls.note_from_digest(calls.parse_digest(CLEAN), "2026-10-18")
        self.assertEqual(draft.date, "2026-10-18")
        self.assertEqual(draft.title, "Call 2026-10-18")
        self.assertEqual(draft.text, "The team planned the garden app demo.")
        self.assertEqual(draft.items, ["Demo on Friday", "Use the new icons", "→ Prepare the slides (Mia, Thursday)", "→ Book the room"])

    def test_title_prefix_and_translation(self):
        self.assertEqual(calls.note_from_digest(calls.Digest(), "2026-10-18", "Retro").title, "Retro 2026-10-18")
        set_language("ru")
        self.assertEqual(calls.note_from_digest(calls.Digest(), "2026-10-18").title, "Созвон 2026-10-18")

    def test_owner_or_due_alone(self):
        self.assertEqual(calls.task_line(calls.Task("Ship", due="Monday")), "→ Ship (Monday)")
        self.assertEqual(calls.task_line(calls.Task("Ship", owner="Leo")), "→ Ship (Leo)")


class TranscriptFileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)

    def test_location_and_content(self):
        path = calls.transcript_file(self.data, "abc123", "Hello team.")
        self.assertEqual(path, self.data / "calls" / "abc123.md")
        self.assertEqual(path.read_text(encoding="utf-8"), "Hello team.\n")
        self.assertFalse(path.with_suffix(".md.tmp").exists())
        if os.name == "posix":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)

    def test_rewrite(self):
        calls.transcript_file(self.data, "abc", "one")
        self.assertEqual(calls.transcript_file(self.data, "abc", "two\n").read_text(encoding="utf-8"), "two\n")

    def test_new_ids_are_safe(self):
        cid = calls.new_call_id()
        self.assertEqual(calls.transcript_path(self.data, cid).parent, self.data / "calls")

    def test_bad_id_refused(self):
        for bad in ("../x", "a/b", "a\\b", "", "x" * 65, "a.b", "abc\n", "abc\r\n", "\nabc"):
            with self.assertRaises(ValueError, msg=bad):
                calls.transcript_path(self.data, bad)


if __name__ == "__main__":
    unittest.main()
