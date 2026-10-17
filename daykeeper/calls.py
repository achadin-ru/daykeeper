"""A call recording or transcript → a note card with notes and tasks.

The pieces of the “Call → note” wizard that need no window:

- Finding whisper.cpp: the path from the settings, `whisper-cli` on PATH, or the copy
  inside an installed GeckIt; the model is a `ggml-*.bin` next to the binary or the path
  from the settings. All file-system and PATH access comes in as parameters, so the tests
  pass fakes and nothing real is looked at.
- The command lines: whisper-cli for a WAV of 16 kHz mono, ffmpeg to make one from
  mp3/m4a/ogg/webm (only when ffmpeg is there; otherwise the wizard asks for a WAV).
- Reading what whisper printed, the prompt that asks an assistant for a digest of the
  call, and a tolerant reader of its answer: JSON inside prose or a code fence, missing
  fields, tasks given as plain strings. A broken answer gives an empty digest, never an
  exception.
- The data for the note card and the transcript file it links to.

Nothing here starts a program: the caller runs the argv lists."""

from __future__ import annotations

import json
import ntpath
import os
import posixpath
import re
import secrets
import shutil
import struct
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .i18n import _
from .settings import private
from .vault import write_private

# Settings keys (settings.json) the wizard and the settings section read and write.
SETTING_BINARY = "calls_whisper_binary"  # path to whisper-cli; empty — look it up
SETTING_MODEL = "calls_whisper_model"  # path to a ggml-*.bin; empty — next to the binary
SETTING_LANGUAGE = "calls_language"  # whisper's -l: "auto", "en", "ru", …
SETTING_THREADS = "calls_threads"  # whisper's -t; 0 — by the number of processors

DEFAULT_LANGUAGE = "auto"
WHISPER_NAMES = ("whisper-cli", "whisper-cpp")  # the second is the Homebrew name
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".vtt", ".srt"}  # a transcript already: no whisper
CONVERT_SUFFIXES = {".mp3", ".m4a", ".ogg", ".oga", ".opus", ".webm", ".flac", ".aac", ".mp4", ".wma"}
AUDIO_SUFFIXES = CONVERT_SUFFIXES | {".wav"}
# Bigger models transcribe better: when several lie next to the binary, the best one wins.
MODEL_RANK = ("large", "medium", "small", "base", "tiny")
CALLS_DIR = "calls"
TASK_MARK = "→ "

Which = Callable[[str], str | None]
Exists = Callable[[str], bool]
ListDir = Callable[[str], Iterable[str]]


def _paths(platform: str):
    """Path functions of the system a path belongs to (a test can look at both)."""
    return ntpath if platform == "win32" else posixpath


def _exe(name: str, platform: str) -> str:
    return name + ".exe" if platform == "win32" else name


def geckit_whisper_dirs(environ: Mapping[str, str], platform: str = sys.platform) -> list[str]:
    """Where an installed GeckIt keeps its whisper.cpp: resources\\whisper of the per-user
    install on Windows, Contents/Resources/whisper of the bundle on macOS."""
    if platform == "win32":
        local = environ.get("LOCALAPPDATA", "")
        return [ntpath.join(local, "Programs", "geckit", "resources", "whisper")] if local else []
    if platform == "darwin":
        bundles = ["/Applications/GeckIt.app"]
        if environ.get("HOME"):
            bundles.append(posixpath.join(environ["HOME"], "Applications", "GeckIt.app"))
        return [posixpath.join(b, "Contents", "Resources", "whisper") for b in bundles]
    return []


def whisper_binary(
    settings: Mapping,
    which: Which = shutil.which,
    exists: Exists = os.path.isfile,
    environ: Mapping[str, str] = os.environ,
    platform: str = sys.platform,
) -> str | None:
    """The whisper-cli to run, or None. Order: the path from the settings (when it is a
    file); whisper-cli on PATH; the copy inside an installed GeckIt."""
    configured = str(settings.get(SETTING_BINARY) or "").strip()
    if configured and exists(configured):
        return configured
    for name in WHISPER_NAMES:
        found = which(name)
        if found:
            return found
    p = _paths(platform)
    for folder in geckit_whisper_dirs(environ, platform):
        candidate = p.join(folder, _exe("whisper-cli", platform))
        if exists(candidate):
            return candidate
    return None


def _model_rank(name: str) -> int:
    low = name.lower()
    for i, size in enumerate(MODEL_RANK):
        if size in low:
            return i
    return len(MODEL_RANK)


def whisper_model(
    settings: Mapping,
    binary: str | None,
    listdir: ListDir = os.listdir,
    exists: Exists = os.path.isfile,
    platform: str = sys.platform,
) -> str | None:
    """The model file, or None: the path from the settings (when it is a file), else the
    best `ggml-*.bin` next to the binary or in a `models` folder beside it."""
    configured = str(settings.get(SETTING_MODEL) or "").strip()
    if configured and exists(configured):
        return configured
    if not binary:
        return None
    p = _paths(platform)
    folder = p.dirname(binary)
    for where in (folder, p.join(folder, "models")):
        try:
            names = [n for n in listdir(where) if n.lower().startswith("ggml-") and n.lower().endswith(".bin")]
        except OSError:
            continue
        for name in sorted(names, key=lambda n: (_model_rank(n), n)):
            path = p.join(where, name)
            if exists(path):
                return path
    return None


def default_threads(cpu_count: int | None = None) -> int:
    """Half the processors, 1 to 8: the rest of the machine stays usable meanwhile."""
    n = os.cpu_count() if cpu_count is None else cpu_count
    return max(1, min(8, (n or 2) // 2))


_LANGUAGE = re.compile(r"[a-z]{2,3}")


def whisper_language(language: str | None) -> str:
    """The value for whisper's -l: "auto" or a two- or three-letter code ("en", "ru",
    "yue"); anything else — "-h" would read as an option — falls back to "auto"."""
    code = (language or "").strip().lower()
    return code if code == DEFAULT_LANGUAGE or _LANGUAGE.fullmatch(code) else DEFAULT_LANGUAGE


def transcribe_argv(binary: str, model: str, wav: str, language: str = DEFAULT_LANGUAGE, threads: int = 0) -> list[str]:
    """whisper-cli for one WAV (16 kHz mono): plain text, no timestamps (-nt), no progress
    lines (-np). `threads` 0 or less — by the processors."""
    count = threads if threads > 0 else default_threads()
    return [binary, "-m", model, "-f", wav, "-l", whisper_language(language), "-t", str(count), "-nt", "-np"]


def is_transcript(path: str | os.PathLike) -> bool:
    """A text file that is the transcript itself."""
    return Path(path).suffix.lower() in TEXT_SUFFIXES


def is_audio(path: str | os.PathLike) -> bool:
    return Path(path).suffix.lower() in AUDIO_SUFFIXES


def wav_is_ready(head: bytes) -> bool:
    """The first bytes of a WAV file describe 16 kHz mono 16-bit PCM — what whisper-cli
    takes as is. Only the first chunk after the RIFF header is looked at (where every
    recorder puts `fmt `); anything unusual reads as not ready."""
    if len(head) < 36 or head[:4] != b"RIFF" or head[8:12] != b"WAVE" or head[12:16] != b"fmt ":
        return False
    fmt, channels, rate = struct.unpack("<HHI", head[20:28])
    bits = struct.unpack("<H", head[34:36])[0]
    return fmt == 1 and channels == 1 and rate == 16000 and bits == 16


def needs_conversion(path: str | os.PathLike, head: bytes | None = None) -> bool:
    """The file has to go through ffmpeg first: compressed audio always; a WAV when its
    first bytes are given and are not 16 kHz mono PCM."""
    suffix = Path(path).suffix.lower()
    if suffix in CONVERT_SUFFIXES:
        return True
    if suffix == ".wav" and head is not None:
        return not wav_is_ready(head)
    return False


def ffmpeg_argv(src: str | os.PathLike, dst: str | os.PathLike, which: Which = shutil.which) -> list[str] | None:
    """ffmpeg making a WAV of 16 kHz mono from `src`, or None when ffmpeg is not on PATH
    (the wizard then asks for a WAV). The input goes with the “file:” protocol, so a name
    like “concat:a|b” or “http:…” is only ever read as a local file; ffmpeg's file
    protocol takes the rest as the path as is, a Windows one (“file:C:\\x.mp3”) too."""
    ffmpeg = which("ffmpeg")
    if not ffmpeg:
        return None
    return [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", "file:" + os.fspath(src), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", os.fspath(dst)]


# ---- what whisper printed

_TIMESTAMP = re.compile(r"^\s*\[\s*\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}\s*-->\s*\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}\s*\]\s*")
_SUBTITLE_TIME = re.compile(r"^\s*\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}\s*-->\s*\d{1,2}:\d{2}(?::\d{2})?[.,]\d{1,3}.*$")
_MARKER = re.compile(r"([\[(*])\s*([^\[\]()*]{1,40}?)\s*[\])*]")
# Sound descriptions whisper writes instead of speech (it writes them in the call's
# language, so the common Russian ones are here too).
NOISE_WORDS = {
    "blank_audio", "blank audio", "silence", "music", "laughter", "laughs", "applause", "noise",
    "inaudible", "no speech", "background noise", "typing", "coughing", "sound",
    "музыка", "тишина", "смех", "аплодисменты", "шум", "неразборчиво",
}


def _is_marker(opening: str, content: str) -> bool:
    if content.strip().lower() in NOISE_WORDS:
        return True
    letters = re.sub(r"[\s_]", "", content)
    return opening == "[" and letters.isalpha() and letters.isupper()  # [BLANK_AUDIO], [SOUND OF RAIN]


def parse_transcript(stdout: str) -> str:
    """The text of a call from whisper's output: segments joined with spaces, timestamps
    (if the output has them) and silence or music markers dropped."""
    parts: list[str] = []
    for raw in stdout.splitlines():
        line = _TIMESTAMP.sub("", raw)
        if _SUBTITLE_TIME.match(line) or line.strip().isdigit() or line.strip() == "WEBVTT":
            continue
        line = _MARKER.sub(lambda m: " " if _is_marker(m.group(1), m.group(2)) else m.group(0), line)
        line = " ".join(line.split())
        if line:
            parts.append(line)
    return " ".join(parts)


# ---- the digest

@dataclass
class Task:
    text: str
    owner: str = ""
    due: str = ""


@dataclass
class Digest:
    summary: str = ""
    notes: list[str] = field(default_factory=list)
    tasks: list[Task] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.summary or self.notes or self.tasks)


def digest_prompt(transcript: str) -> str:
    """The prompt for an assistant: a digest of the call as JSON and nothing else."""
    return (
        "Below is the transcript of a work call. Write a digest of it.\n"
        "Answer with JSON only — no prose before or after it, no code fence — in this shape:\n"
        '{"summary": "two to five sentences: what the call was about and what was decided",\n'
        ' "notes": ["a fact, decision or agreement worth keeping", "..."],\n'
        ' "tasks": [{"text": "what has to be done", "owner": "who does it, or empty", "due": "when, or empty"}]}\n'
        "Write in the language of the transcript. Keep each note and task to one line. Only "
        "tasks that were actually agreed on; use empty lists when there are none. Do not "
        "invent owners or dates that were not said.\n\n"
        "Transcript:\n"
        f"{transcript.strip()}\n"
    )


_FENCE = re.compile(r"```[a-zA-Z0-9_-]*\s*\n?(.*?)```", re.S)


def _first_object(text: str) -> dict | None:
    """The first JSON object in the text, or None. Never raises: broken JSON is a
    ValueError, JSON nested deeper than Python's recursion limit a RecursionError."""
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            value, _end = decoder.raw_decode(text, start)
        except (ValueError, RecursionError):
            value = None
        if isinstance(value, dict):
            return value
        start = text.find("{", start + 1)
    return None


def _text(value: object) -> str:
    if value is None or isinstance(value, (dict, list)):
        return ""
    return " ".join(str(value).split())


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out = []
    for v in value:
        if isinstance(v, dict):
            v = v.get("text") or v.get("note") or ""
        s = _text(v)
        if s:
            out.append(s)
    return out


def _tasks(value: object) -> list[Task]:
    if isinstance(value, (str, dict)):
        value = [value]
    if not isinstance(value, list):
        return []
    out = []
    for v in value:
        if isinstance(v, dict):
            task = Task(_text(v.get("text") or v.get("task") or v.get("title")), _text(v.get("owner") or v.get("assignee")), _text(v.get("due") or v.get("deadline")))
        else:
            task = Task(_text(v))
        if task.text:
            out.append(task)
    return out


def parse_digest(text: str) -> Digest:
    """The digest from an assistant's answer. Takes the first JSON object found — inside a
    code fence or prose too; missing or odd fields stay empty; tasks may be plain strings.
    No object at all gives an empty Digest."""
    data = None
    for block in _FENCE.findall(text or ""):
        data = _first_object(block)
        if data is not None:
            break
    if data is None:
        data = _first_object(text or "")
    if data is None:
        return Digest()
    return Digest(_text(data.get("summary")), _strings(data.get("notes")), _tasks(data.get("tasks")))


# ---- the note card and the transcript file

@dataclass
class NoteDraft:
    """What the wizard turns into a note: notes.Notes.add(date=…, title=…, text=…), then
    add_item() for every item and attach() for the transcript file."""

    date: str
    title: str
    text: str
    items: list[str] = field(default_factory=list)


def task_line(task: Task) -> str:
    extra = ", ".join(x for x in (task.owner, task.due) if x)
    return f"{TASK_MARK}{task.text}" + (f" ({extra})" if extra else "")


def note_from_digest(digest: Digest, date: str, title_prefix: str = "") -> NoteDraft:
    """The card of a call: the title “Call <date>” (or “<title_prefix> <date>”), the summary
    as its text, the notes and then the tasks (marked with an arrow) as its items."""
    title = f"{title_prefix.strip()} {date}" if title_prefix.strip() else _("Call {date}").format(date=date)
    items = list(digest.notes) + [task_line(t) for t in digest.tasks]
    return NoteDraft(date=date, title=title, text=digest.summary, items=items)


def new_call_id() -> str:
    return secrets.token_hex(6)


_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def transcript_path(data_dir: str | os.PathLike, call_id: str) -> Path:
    """`<data>/calls/<id>.md`; an id that could step out of the folder is refused."""
    if not _SAFE_ID.fullmatch(call_id):
        raise ValueError(f"bad call id: {call_id!r}")
    return Path(data_dir) / CALLS_DIR / f"{call_id}.md"


def transcript_file(data_dir: str | os.PathLike, call_id: str, text: str) -> Path:
    """Write the transcript to `<data>/calls/<id>.md`, owner-only (a call can hold anything
    said in it), and return the path to attach to the note."""
    path = transcript_path(data_dir, call_id)
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        private(path.parent, folder=True)
    body = text if text.endswith("\n") else text + "\n"
    write_private(path, body.encode("utf-8"))
    return path
