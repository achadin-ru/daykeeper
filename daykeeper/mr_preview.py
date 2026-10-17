"""A merge request as one Markdown page for the built-in viewer.

`render_mr` takes the answers of `GitLabClient.mr` and `GitLabClient.mr_changes` and
writes a header (title, link, author, branches, pipeline), the description, a table of
the files with the lines added and removed, then each file's diff in a fenced block. A
big merge request is cut: whole files while they fit the byte budget, the rest is left to
the browser. `mr_template_context` gives the placeholders of the assistant templates.
Pure: no network, no tkinter; unit-tested on hand-written answers.
"""

from __future__ import annotations

import re

from .gitlab import web_url
from .i18n import _

LIMIT_BYTES = 400_000  # of diff text; a page this size still opens in a moment
_BINARY = re.compile(r"^(Binary files .* differ|GIT binary patch)\s*$", re.MULTILINE)
_HUNK = re.compile(r"^@@ ")
# a link as the viewer reads it (markdown._INLINE): [text](target "title"); an image
# ![alt](target) is the same link there, with its "!" left as text
_LINK_AT = re.compile(r"\[(?P<text>[^\]]+)\]\((?P<href>[^)\s]+)(?:\s+\"[^\"]*\")?\)")
_WEB = ("http://", "https://")  # the only targets the viewer hands to the browser
_ZWSP = "\u200b"  # not whitespace to the viewer, so a line it leads is no fence


def _one_line(value) -> str:
    """Server text for one line: no line breaks, no stray spaces."""
    return " ".join(str(value or "").split())


def _cell(value) -> str:
    """Text for a table cell. A “|” would split the cell — in GitHub-flavoured Markdown
    and in the built-in viewer alike, which knows no backslash escapes — so it becomes
    a broken bar “¦”; a line break becomes a space."""
    return _one_line(value).replace("|", "¦")


def _code(value) -> str:
    """An inline code span that survives backticks in the text: the fence is one
    backtick longer than the longest run inside."""
    text = _one_line(value)
    runs = [len(r) for r in re.findall(r"`+", text)]
    fence = "`" * (max(runs, default=0) + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _viewer_lines(text: str) -> list[str]:
    """The lines as the viewer splits them: at “\\n”, “\\r\\n” and a lone “\\r” only."""
    return text.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def _no_local_links(text: str) -> str:
    """Server text with every link or image whose target is not a web address reduced to
    its visible text. The viewer resolves any other target against the preview file and
    opens it, so a merge request could point at the user's own files. Every “[” is tried,
    a kept web link included, so a link hidden in its title is found too; a replacement
    is read again from where it starts, as it may join with the text around it."""
    pos = 0
    while (start := text.find("[", pos)) != -1:
        m = _LINK_AT.match(text, start)
        if m is None or m.group("href").startswith(_WEB):
            pos = start + 1
            continue
        begin = start - 1 if start and text[start - 1] == "!" else start
        text = text[:begin] + m.group("text") + text[m.end():]
        pos = begin
    return text


def _fenced(diff: str) -> list[str]:
    """A diff in a ``` block that no line of it can close. The viewer opens a block on a
    line starting (after spaces) with ``` or ~~~ and closes it on the first line starting
    with the same three characters, whatever the fence length — so such a line gets a
    zero-width space in front: it looks the same and is no fence any more."""
    lines = _viewer_lines(diff.rstrip("\r\n"))
    return ["```diff", *(_ZWSP + line if line.lstrip().startswith(("```", "~~~")) else line for line in lines), "```"]


def diff_counts(diff: str) -> tuple[int, int]:
    """Lines added and removed in one file's diff. GitLab sends the hunks without the
    “---/+++” header; when a header is there it is skipped, so a removed line that reads
    “-- x” still counts."""
    added = removed = 0
    in_hunk = not diff.startswith(("diff ", "--- "))
    for line in diff.splitlines():
        if _HUNK.match(line):
            in_hunk = True
        elif in_hunk and line.startswith("+"):
            added += 1
        elif in_hunk and line.startswith("-"):
            removed += 1
    return added, removed


def is_binary(change: dict) -> bool:
    return bool(_BINARY.search(str(change.get("diff") or "")))


def _path(change: dict) -> str:
    return str(change.get("new_path") or change.get("old_path") or "?")


def _what(change: dict) -> str:
    """How the file changed, for the table."""
    if change.get("new_file"):
        kind = _("new")
    elif change.get("deleted_file"):
        kind = _("deleted")
    elif change.get("renamed_file"):
        kind = _("renamed from {path}").format(path=_cell(change.get("old_path")))
    else:
        kind = _("changed")
    if is_binary(change):
        kind += ", " + _("binary")
    return kind


def _changes(changes_json) -> list[dict]:
    changes = changes_json.get("changes") if isinstance(changes_json, dict) else None
    return [c for c in changes if isinstance(c, dict)] if isinstance(changes, list) else []


def _cut(diff: str, limit: int) -> str:
    """The head of a diff within `limit` bytes, cut at a line end."""
    data = diff.encode("utf-8")[:limit]
    text = data.decode("utf-8", errors="ignore")
    return text[: text.rfind("\n") + 1] if "\n" in text else text


def _balanced(text: str) -> str:
    """A description with an unclosed code fence would swallow the rest of the page. It
    is closed the way the viewer reads fences: a block opened by ``` or ~~~ ends only on
    a line starting with the same marker."""
    marker = ""
    for line in _viewer_lines(text):
        head = line.strip()
        if not marker:
            marker = head[:3] if head.startswith(("```", "~~~")) else ""
        elif head.startswith(marker):
            marker = ""
    return text + (f"\n{marker}" if marker else "")


def render_mr(mr_json: dict, changes_json: dict, limit_bytes: int = LIMIT_BYTES) -> str:
    """The merge request as Markdown. `mr_json` — the MR itself (its head pipeline is
    there); `changes_json` — the answer of /changes (it repeats the MR fields, so either
    may stand in for the other). Diffs past `limit_bytes` are left to the browser."""
    mr = mr_json if isinstance(mr_json, dict) else {}
    extra = changes_json if isinstance(changes_json, dict) else {}

    def field(name: str):
        value = mr.get(name)
        return value if value not in (None, "") else extra.get(name)

    url = web_url(field("web_url"))
    ctx = mr_template_context({**extra, **{k: v for k, v in mr.items() if v not in (None, "")}})
    out: list[str] = [f"# {_one_line(field('title')) or _('Merge request')}", ""]
    ref = f"{ctx['mr_project']}!{ctx['mr_iid']}" if ctx["mr_project"] and ctx["mr_iid"] else ""
    if url:
        out += [f"[{ref}]({url})" if ref else f"<{url}>", ""]
    elif ref:
        out += [ref, ""]
    author = field("author") if isinstance(field("author"), dict) else {}
    who = _one_line(author.get("name"))
    login = _one_line(author.get("username"))
    if who or login:
        out.append(f"- **{_('Author')}:** " + (f"{who} (@{login})" if who and login else who or f"@{login}"))
    source, target = _one_line(field("source_branch")), _one_line(field("target_branch"))
    if source or target:
        out.append(f"- **{_('Branches')}:** {_code(source or '?')} → {_code(target or '?')}")
    state = _one_line(field("state"))
    if state:
        out.append(f"- **{_('State')}:** {state}" + (f", {_('draft')}" if field("draft") or field("work_in_progress") else ""))
    pipeline = field("head_pipeline") or field("pipeline")
    if isinstance(pipeline, dict) and pipeline.get("status"):
        status = _one_line(pipeline.get("status"))
        link = web_url(pipeline.get("web_url"))
        out.append(f"- **{_('Pipeline')}:** " + (f"[{status}]({link})" if link else status))
    # title, author, state and the reference are the server's text, and so is the
    # description: none of it may carry a link to a local file, not even one formed
    # together with the markup around it
    out = [_no_local_links(line) for line in out] + [""]

    description = _no_local_links("\n".join(_viewer_lines(str(field("description") or ""))).strip())
    out += [f"## {_('Description')}", "", _balanced(description) if description else f"_{_('No description.')}_", ""]

    changes = _changes(changes_json)
    counts = [diff_counts(str(c.get("diff") or "")) for c in changes]
    out += [f"## {_('Files')} ({len(changes)}, +{sum(a for a, _r in counts)} −{sum(r for _a, r in counts)})", ""]
    if changes:
        out += [f"| {_('File')} | {_('Change')} | + | − |", "|---|---|---:|---:|"]
        for change, (added, removed) in zip(changes, counts, strict=True):
            out.append(f"| {_cell(_path(change))} | {_what(change)} | {added} | {removed} |")
        out.append("")
    if extra.get("overflow"):
        out += [f"_{_('GitLab sent only part of the diffs: the rest is in the browser.')}_", ""]

    budget, shown, cut = max(0, limit_bytes), 0, False
    body: list[str] = []
    for change in changes:
        diff = str(change.get("diff") or "")
        size = len(diff.encode("utf-8"))
        if size > budget:
            if shown == 0 and budget:  # one huge file: its head rather than nothing
                diff, cut = _cut(diff, budget), True
            else:
                break
        budget -= len(diff.encode("utf-8"))
        shown += 1
        body += [f"### {_code(_path(change))}", ""]
        if is_binary(change):
            body += [f"_{_('Binary file, not shown.')}_", ""]
        elif change.get("too_large") or (not diff and change.get("collapsed")):
            body += [f"_{_('The diff is too large for GitLab to send: see it in the browser.')}_", ""]
        elif not diff:
            body += [f"_{_('No changes in the text.')}_", ""]
        else:
            body += [*_fenced(diff), ""]
        if cut:
            body += [f"_{_('The diff is cut here.')}_", ""]
            break
    if shown < len(changes) or cut:
        note = _("First {n} of {total} files shown, the rest in the browser.").format(n=shown, total=len(changes))
        out += [f"> {note}" + (f" [{_('Open')}]({url})" if url else ""), ""]
    out += body
    return "\n".join(out).rstrip("\n") + "\n"


def mr_template_context(mr_json: dict) -> dict[str, str]:
    """{mr_url} {mr_title} {mr_project} {mr_iid} for the assistant templates. The project
    is the “group/repo” path, taken from the full reference or else from the link."""
    mr = mr_json if isinstance(mr_json, dict) else {}
    url = web_url(mr.get("web_url"))
    iid = str(mr.get("iid") or "")
    full = str((mr.get("references") or {}).get("full") or "") if isinstance(mr.get("references"), dict) else ""
    project = full.rsplit("!", 1)[0] if "!" in full else ""
    if not project and "/-/merge_requests/" in url:
        project = url.split("://", 1)[-1].split("/", 1)[-1].split("/-/merge_requests/", 1)[0]
        if not iid:
            iid = url.rsplit("/", 1)[-1]
    return {"mr_url": url, "mr_title": _one_line(mr.get("title")), "mr_project": project, "mr_iid": iid}
