"""GitLab watcher: what changed in my merge requests since the last look.

`GitLabClient` reads the REST API (v4) with a personal access token (scope `read_api`).
`take_snapshot` turns the API answers into plain dicts — one per open MR where I am the
author or a reviewer. `diff_snapshots` compares two snapshots and returns events for the
rules that are switched on. Between full checks only what may have changed is asked for:
approvals and pipelines once a project, threads only where the count of notes moved. The
`updated_at` of an MR is no help here: an approval, a finished pipeline or a resolved
thread leaves it as it was. Watched branches (`PipelineWatch`) cost a request a rule kind
on every check: their newest finished pipeline and the commits since the last head seen.
The client is the only part that touches the network; the rest is pure and unit-tested.
"""

from __future__ import annotations

import json
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from .settings import number

# Rules: code → (UI label key, on by default).
RULES: dict[str, tuple[str, bool]] = {
    "approved": ("My MR got an approval", True),
    "resolved": ("All threads on my MR are resolved", True),
    "pipeline": ("The pipeline of my MR failed", True),
    "merged": ("My MR was merged", True),
    "review_requested": ("I was asked to review an MR", True),
    "my_threads_resolved": ("My threads were resolved in an MR I review", True),
    "replies": ("Someone replied in my threads in an MR I review", True),
}
# Rules about the watched branches (PipelineWatch rows), not my MRs: kept apart so the MR
# rules block of the settings lists only its own; `ALL_RULES` is what a setting may hold.
WATCH_RULES: dict[str, tuple[str, bool]] = {
    "watch_failed": ("A watched pipeline failed", True),
    "watch_fixed": ("A watched pipeline is green again", False),
    "branch_commits": ("New commits on a watched branch", False),
}
ALL_RULES = RULES | WATCH_RULES
PIPELINE_SOURCES = ("", "schedule", "push")  # "" — a pipeline from any source
FINISHED = ("success", "failed", "canceled")  # a pipeline with an outcome; running ones are skipped
INTERVALS = (2, 5, 10, 15, 30, 60)  # minutes between checks
FULL_EVERY = 3600  # s; a full check asks about every MR again, whatever the light ones saw
SINCE_MARGIN = 600  # s; a light check looks this far before the last one (clocks, a slow check)
FEED_KEEP = 200
TIMEOUT_TEXT = "GitLab did not answer in time"
PLAIN_HTTP_TEXT = "The token is sent over https only — write the address with https://"
FOREIGN_REDIRECT_TEXT = "GitLab sends the request to another server — check the address in the settings"
_LOOPBACK = ("localhost", "127.0.0.1", "::1")
MR_ROLES = ("author", "reviewer")


def _token_may_go(url: str, origin: str) -> None:
    """The token goes only over https (plain http only to this very machine) and only to
    the server the user typed: a redirect to another host must not carry it away."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise GitLabError(f"Not a web address: {url}")
    if parts.scheme == "http" and parts.hostname not in _LOOPBACK:
        raise GitLabError(PLAIN_HTTP_TEXT)
    if origin and _server(parts) != _server(urllib.parse.urlsplit(origin)):  # the port too: another one is another server
        raise GitLabError(FOREIGN_REDIRECT_TEXT)


def _server(parts: urllib.parse.SplitResult) -> tuple[str, int]:
    return (parts.hostname or "", parts.port or (443 if parts.scheme == "https" else 80))


def web_url(value) -> str:
    """A link from the server is opened with a click: only http(s), never file: or a program."""
    url = str(value or "")
    return url if urllib.parse.urlsplit(url).scheme.lower() in ("http", "https") else ""


def _same_host_redirects():
    """urllib copies every header, the token too, onto the redirected request. The
    network modules (urllib.request, http.client, ssl: 50 ms) are imported on the first
    check, not at start."""
    import urllib.request

    class SameHostRedirects(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            _token_may_go(newurl, req.full_url)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return SameHostRedirects


class GitLabError(Exception):
    """The API could not be reached or refused the request."""


@dataclass
class PipelineWatch:
    """A branch (or a tag) whose newest pipeline and new commits are watched."""

    project: str  # group/repo
    ref: str  # a branch or a tag
    source: str = ""  # "schedule" | "push" | "" — any
    name: str = ""  # how the feed calls it; "project · ref" when empty

    def key(self) -> str:
        """The state key: “group/repo@main”, with “:schedule” when only one source counts."""
        return f"{self.project}@{self.ref}" + (f":{self.source}" if self.source else "")

    def label(self) -> str:
        return self.name.strip() or f"{self.project} · {self.ref}"

    def to_dict(self) -> dict:
        return {"project": self.project, "ref": self.ref, "source": self.source, "name": self.name}

    @classmethod
    def from_dict(cls, d) -> PipelineWatch | None:
        """None for a row without a project or a ref."""
        if not isinstance(d, dict):
            return None
        project, ref = str(d.get("project", "")).strip().strip("/"), str(d.get("ref", "")).strip()
        if not project or not ref:
            return None
        source = str(d.get("source", ""))
        return cls(project, ref, source if source in PIPELINE_SOURCES else "", str(d.get("name", "")))


@dataclass
class GitLabSettings:
    url: str = ""  # e.g. https://gitlab.example.com
    entry_id: str = ""  # vault entry whose secret field holds the token
    interval_min: int = 5
    rules: dict[str, bool] = field(default_factory=lambda: {k: on for k, (_l, on) in ALL_RULES.items()})
    enabled: bool = False
    username: str = ""  # filled by “Check connection”
    projects: list[str] = field(default_factory=list)  # paths of the watched projects; [] — all
    pipelines: list[PipelineWatch] = field(default_factory=list)  # branches watched for pipelines and commits

    def watches(self, project: str) -> bool:
        return not self.projects or project in self.projects

    def rule(self, code: str) -> bool:
        return self.rules.get(code, ALL_RULES[code][1])

    def to_dict(self) -> dict:
        return {
            "url": self.url, "entry_id": self.entry_id, "interval_min": self.interval_min,
            "rules": dict(self.rules), "enabled": self.enabled, "username": self.username,
            "projects": list(self.projects), "pipelines": [w.to_dict() for w in self.pipelines],
        }

    @classmethod
    def from_dict(cls, d) -> GitLabSettings:
        if not isinstance(d, dict):
            return cls()
        interval = d.get("interval_min", 5)
        rules = {k: bool(v) for k, v in (d.get("rules") or {}).items() if k in ALL_RULES}
        return cls(
            url=str(d.get("url", "")).strip().rstrip("/"),
            entry_id=str(d.get("entry_id", "")),
            interval_min=interval if interval in INTERVALS else 5,
            rules={k: rules.get(k, on) for k, (_l, on) in ALL_RULES.items()},
            enabled=bool(d.get("enabled", False)),
            username=str(d.get("username", "")),
            projects=[x for x in projects if isinstance(x, str) and x] if isinstance(projects := d.get("projects"), list) else [],
            pipelines=[w for w in map(PipelineWatch.from_dict, pipes) if w] if isinstance(pipes := d.get("pipelines"), list) else [],
        )


@dataclass
class Event:
    kind: str  # an ALL_RULES code (another source: its own)
    mr: str  # the reference of any source: "group/repo!12", "group/repo@main"
    title: str
    url: str
    text: str  # what happened, in English UI keys formatted by the caller
    args: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    read: bool = False
    source: str = "gitlab"  # which integration raised it
    project_id: int = 0  # the GitLab project of an MR event; 0 — not an MR or an older record
    role: str = ""  # "author" | "reviewer": my part in that MR; "" — unknown

    def to_dict(self) -> dict:
        d = {"kind": self.kind, "mr": self.mr, "title": self.title, "url": self.url, "text": self.text, "args": self.args, "ts": self.ts, "read": self.read, "source": self.source}
        if self.project_id:
            d["project_id"] = self.project_id
        if self.role:
            d["role"] = self.role
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Event:
        return cls(
            kind=str(d.get("kind", "")), mr=str(d.get("mr", "")), title=str(d.get("title", "")), url=web_url(d.get("url")),
            text=str(d.get("text", "")), args=dict(d["args"]) if isinstance(d.get("args"), dict) else {}, ts=number(float, d.get("ts"), None) or time.time(), read=bool(d.get("read", False)),
            source=str(d.get("source") or "gitlab"), project_id=number(int, d.get("project_id"), 0),
            role=role if (role := d.get("role")) in MR_ROLES else "",
        )


class GitLabClient:
    """A tiny REST client. `fetch(url, headers)` → (status, body bytes) can be swapped in tests."""

    def __init__(self, base_url: str, token: str, fetch=None, timeout: float = 20.0):
        base_url = base_url.strip().rstrip("/")
        if base_url and "://" not in base_url:
            base_url = "https://" + base_url  # “gitlab.example.com” typed without the scheme
        self.base = base_url + "/api/v4"
        self.token = token
        self.timeout = timeout
        self._fetch = fetch or self._http
        self._conn: Any = None  # an http.client.HTTPConnection, imported on the first request; the tests put a fake in
        self._conn_key: tuple[str, str] = ("", "")

    def _http(self, url: str, headers: dict) -> tuple[int, bytes]:
        """One kept-alive connection for the whole check. urllib asks the server to close
        the connection after each answer (Connection: close), and some GitLab front ends
        then sometimes send no answer at all: the check hung until the read timeout."""
        import http.client
        import ssl
        import urllib.request

        if urllib.request.getproxies().get(urllib.parse.urlsplit(url).scheme):
            return self._urlopen(url, headers)  # a system proxy: urllib knows how to use it
        for attempt in (1, 2):  # a kept-alive connection the server dropped: one fresh try
            try:
                return self._request(url, headers)
            except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
                self.close()
                if isinstance(exc, TimeoutError):
                    raise GitLabError(TIMEOUT_TEXT) from exc
                if attempt == 2:
                    raise GitLabError(str(exc) or type(exc).__name__) from exc
        raise AssertionError("unreachable")

    def _request(self, url: str, headers: dict, redirects: int = 3) -> tuple[int, bytes]:
        import http.client
        import ssl

        _token_may_go(url, self.base)
        parts = urllib.parse.urlsplit(url)
        key = (parts.scheme, parts.netloc)
        if self._conn is None or self._conn_key != key:
            self.close()
            if parts.scheme == "https":
                self._conn = http.client.HTTPSConnection(parts.netloc, timeout=self.timeout, context=ssl.create_default_context())
            else:
                self._conn = http.client.HTTPConnection(parts.netloc, timeout=self.timeout)
            self._conn_key = key
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        self._conn.request("GET", path, headers={**headers, "User-Agent": "Daykeeper"})
        resp = self._conn.getresponse()
        body = resp.read()
        location = resp.getheader("Location")
        if resp.status in (301, 302, 303, 307, 308) and location and redirects:
            return self._request(urllib.parse.urljoin(url, location), headers, redirects - 1)
        return resp.status, body

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
        self._conn = None

    def _urlopen(self, url: str, headers: dict) -> tuple[int, bytes]:
        import ssl
        import urllib.error
        import urllib.request

        _token_may_go(url, self.base)
        req = urllib.request.Request(url, headers=headers)  # noqa: S310 — https only, checked by _token_may_go above
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()), _same_host_redirects())
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read() if exc.fp else b""
        except (urllib.error.URLError, OSError, ssl.SSLError) as exc:
            raise GitLabError(str(getattr(exc, "reason", exc))) from exc

    def get(self, path: str, **params) -> object:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.base}{path}" + (f"?{query}" if query else "")
        _token_may_go(url, self.base)  # whatever `fetch` is: no token over plain http
        status, body = self._fetch(url, {"PRIVATE-TOKEN": self.token, "Accept": "application/json"})
        if status == 401:
            raise GitLabError("401 Unauthorized — the token is wrong or expired")
        if status == 403:
            raise GitLabError("403 Forbidden — the token lacks the read_api scope")
        if status >= 400:
            raise GitLabError(f"HTTP {status} on {path}")
        try:
            return json.loads(body.decode("utf-8"))
        except ValueError as exc:
            raise GitLabError(f"Not JSON from {path}") from exc

    def get_all(self, path: str, limit: int = 500, **params) -> list:
        out: list = []
        page = 1
        while len(out) < limit:
            chunk = self.get(path, per_page=100, page=page, **params)
            if not isinstance(chunk, list) or not chunk:
                break
            out.extend(chunk)
            if len(chunk) < 100:
                break
            page += 1
        return out[:limit]

    def me(self) -> dict:
        try:
            user = self.get("/user")
        except GitLabError as exc:
            if str(exc).startswith("HTTP 404"):  # a project link instead of the server address
                raise GitLabError("No GitLab API at this address — it should be the server itself, e.g. https://gitlab.example.com") from exc
            raise
        if not isinstance(user, dict) or "id" not in user:
            raise GitLabError("Unexpected answer from /user")
        return user

    def mr(self, project: int | str, iid: int) -> dict:
        """One merge request: title, author, branches, description, head pipeline.
        `project` — the numeric id or the “group/repo” path."""
        answer = self.get(f"/projects/{_project_ref(project)}/merge_requests/{_iid(iid)}")
        if not isinstance(answer, dict):
            raise GitLabError("Unexpected answer for the merge request")
        return answer

    def mr_changes(self, project: int | str, iid: int) -> dict:
        """The merge request with its `changes`: one diff a file (read_api is enough)."""
        answer = self.get(f"/projects/{_project_ref(project)}/merge_requests/{_iid(iid)}/changes")
        if not isinstance(answer, dict):
            raise GitLabError("Unexpected answer for the merge request changes")
        return answer


def _iid(iid: int | str) -> int:
    """A merge request number as the API wants it; anything else is a GitLabError."""
    try:
        number = int(str(iid).strip())
    except ValueError:
        raise GitLabError(f"Not a merge request number: {iid}") from None
    if number < 1:
        raise GitLabError(f"Not a merge request number: {iid}")
    return number


def _project_ref(project: int | str) -> str:
    """A project id stays as it is, a path is URL-encoded (“group/repo” → “group%2Frepo”)."""
    text = str(project).strip().strip("/")
    if not text:
        raise GitLabError("No project given")
    return text if text.isdigit() else project_path(text)


def mr_key(mr: dict) -> str:
    ref = (mr.get("references") or {}).get("full")
    return ref or f"{mr.get('project_id')}!{mr.get('iid')}"


def project_of(key: str) -> str:
    """“group/repo!12” → “group/repo”."""
    return key.rsplit("!", 1)[0]


def _stamp(t: float, date_only: bool = False) -> str:
    return time.strftime("%Y-%m-%d" if date_only else "%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _new_approvals(client: GitLabClient, pids: list, since: float) -> dict[tuple, set[str]]:
    """Who approved what in these projects lately: one request a project instead of one an MR.
    The events API filters by day only, so a day more is taken; a repeat changes nothing."""
    out: dict[tuple, set[str]] = {}
    for pid in pids:
        for ev in client.get_all(f"/projects/{pid}/events", action="approved", target_type="merge_request", after=_stamp(since - 86400, date_only=True)):
            user = (ev.get("author") or {}).get("username") if isinstance(ev, dict) else None
            if user and ev.get("target_iid") is not None:
                out.setdefault((pid, ev["target_iid"]), set()).add(user)
    return out


def _new_pipelines(client: GitLabClient, mrs: list[dict], since: float) -> dict[tuple, str]:
    """The status of the newest pipeline that changed lately, per MR: one request a project.
    A pipeline belongs to the MR by its head commit, or by the MR's own ref (merged results)."""
    out: dict[tuple, str] = {}
    for pid in sorted({mr["project_id"] for mr in mrs}, key=str):
        pipes = [p for p in client.get_all(f"/projects/{pid}/pipelines", updated_after=_stamp(since - SINCE_MARGIN)) if isinstance(p, dict)]
        for mr in (m for m in mrs if m["project_id"] == pid):
            ref = f"refs/merge-requests/{mr['iid']}/"
            mine = [p for p in pipes if (mr.get("sha") and p.get("sha") == mr["sha"]) or str(p.get("ref", "")).startswith(ref)]
            if mine:
                out[(pid, mr["iid"])] = str(max(mine, key=lambda p: p.get("id") or 0).get("status") or "")
    return out


def take_snapshot(client: GitLabClient, me: dict, rules: GitLabSettings, old: dict[str, dict] | None = None,
                  since: float = 0.0, seen: set[str] | None = None) -> dict[str, dict]:
    """Open MRs where I am the author or a reviewer → {mr key: facts}. Asks only for what
    the switched-on rules need. `old` — the previous snapshot. `since` — when the last
    check began; 0 makes this a full check that asks about every MR. `seen` gets the paths
    of all the projects with such MRs, the unwatched too (the settings list them)."""
    old = old or {}
    full = since <= 0
    uid, uname = me["id"], me.get("username", "")
    snap: dict[str, dict] = {}
    need_author = any(rules.rule(r) for r in ("approved", "resolved", "pipeline", "merged"))
    need_review = any(rules.rule(r) for r in ("review_requested", "my_threads_resolved", "replies"))

    def watched(mrs: list) -> list[dict]:
        out = []
        for mr in mrs:
            project = project_of(mr_key(mr))
            if seen is not None:
                seen.add(project)
            if rules.watches(project):
                out.append(mr)
        return out

    if need_author:
        mine = watched(client.get_all("/merge_requests", scope="all", state="opened", author_id=uid))
        known = [mr for mr in mine if not full and mr_key(mr) in old]  # a new MR is asked about by itself
        pids = sorted({mr["project_id"] for mr in known}, key=str)
        light_keys = {mr_key(mr) for mr in known}
        approvals = _new_approvals(client, pids, since) if known and rules.rule("approved") else {}
        pipelines = _new_pipelines(client, known, since) if known and rules.rule("pipeline") else {}
        for mr in mine:
            pid, iid = mr["project_id"], mr["iid"]
            facts = _base(mr, "author")
            before = old.get(mr_key(mr), {})
            light = mr_key(mr) in light_keys
            if rules.rule("approved"):
                if light:
                    facts["approved_by"] = sorted(set(before.get("approved_by", [])) | approvals.get((pid, iid), set()))
                else:
                    answer = client.get(f"/projects/{pid}/merge_requests/{iid}/approvals")
                    facts["approved_by"] = sorted(
                        a["user"]["username"] for a in (answer.get("approved_by") or []) if isinstance(a, dict) and a.get("user")
                    ) if isinstance(answer, dict) else []
            listed = mr.get("blocking_discussions_resolved")
            if listed is not None:
                facts["blocking_resolved"] = bool(listed)
            if rules.rule("pipeline") and light:
                facts["pipeline"] = pipelines.get((pid, iid), before.get("pipeline", ""))
            if (rules.rule("pipeline") and not light) or (rules.rule("resolved") and listed is None):  # the list has no pipeline
                whole = client.get(f"/projects/{pid}/merge_requests/{iid}")
                if isinstance(whole, dict):
                    facts["blocking_resolved"] = bool(whole.get("blocking_discussions_resolved", True))
                    if rules.rule("pipeline") and not light:
                        facts["pipeline"] = ((whole.get("head_pipeline") or {}).get("status")) or ""
            if rules.rule("resolved") and facts.get("blocking_resolved") and before.get("blocking_resolved") is False:
                discussions = client.get_all(f"/projects/{pid}/merge_requests/{iid}/discussions")
                facts["threads"] = sum(1 for d in discussions if _resolvable(d))
            else:
                facts["threads"] = before.get("threads", 0)  # only read on the flip above
            snap[mr_key(mr)] = facts

    if need_review:
        for mr in watched(client.get_all("/merge_requests", scope="all", state="opened", reviewer_id=uid)):
            key = mr_key(mr)
            facts = snap.get(key) or _base(mr, "reviewer")
            facts["reviewer"] = True
            facts["notes"] = mr.get("user_notes_count")
            before = old.get(key, {})
            if rules.rule("my_threads_resolved") or rules.rule("replies"):
                # a reply moves the count of notes, a resolve does not: an MR with my
                # threads still open is read every time
                same = (not full and before.get("reviewer") and facts["notes"] is not None and facts["notes"] == before.get("notes")
                        and before.get("my_threads", 0) == before.get("my_resolved", 0))
                if same:
                    for k, empty in (("my_threads", 0), ("my_resolved", 0), ("replies", 0), ("last_replier", "")):
                        facts[k] = before.get(k, empty)
                else:
                    discussions = client.get_all(f"/projects/{mr['project_id']}/merge_requests/{mr['iid']}/discussions")
                    mine = [d for d in discussions if _resolvable(d) and _notes(d) and _notes(d)[0].get("author", {}).get("username") == uname]
                    facts["my_threads"] = len(mine)
                    facts["my_resolved"] = sum(1 for d in mine if all(n.get("resolved") for n in _notes(d) if n.get("resolvable")))
                    replies = [n for d in discussions if any(x.get("author", {}).get("username") == uname for x in _notes(d))
                               for n in _notes(d) if n.get("author", {}).get("username") != uname and not n.get("system")]
                    facts["replies"] = len(replies)
                    facts["last_replier"] = max(replies, key=lambda n: n.get("created_at", ""))["author"].get("username", "") if replies else ""
            snap[key] = facts
    return snap


def _notes(d: dict) -> list[dict]:
    return [n for n in (d.get("notes") or []) if isinstance(n, dict)]


def _resolvable(d: dict) -> bool:
    return any(n.get("resolvable") for n in _notes(d))


def _base(mr: dict, role: str) -> dict:
    return {
        "role": role,
        "title": str(mr.get("title", "")),
        "url": web_url(mr.get("web_url")),
        "author": str((mr.get("author") or {}).get("username", "")),
        "project_id": mr.get("project_id"),
        "iid": mr.get("iid"),
    }


def closed_states(client: GitLabClient, gone: dict[str, dict]) -> dict[str, str]:
    """MRs that left the open list: their state now (merged / closed)."""
    out: dict[str, str] = {}
    for key, facts in gone.items():
        if facts.get("project_id") is None or facts.get("iid") is None:
            continue
        try:
            mr = client.get(f"/projects/{facts['project_id']}/merge_requests/{facts['iid']}")
        except GitLabError:
            continue
        if isinstance(mr, dict):
            out[key] = str(mr.get("state", ""))
    return out


def diff_snapshots(old: dict[str, dict], new: dict[str, dict], rules: GitLabSettings, gone_states: dict[str, str] | None = None) -> list[Event]:
    """Events between two snapshots. An MR seen for the first time raises only
    “review requested” (everything else about it is the baseline)."""
    events: list[Event] = []

    def add(kind: str, key: str, facts: dict, text: str, **args) -> None:
        if rules.rule(kind):
            role = facts.get("role")
            events.append(Event(kind, key, facts.get("title", ""), facts.get("url", ""), text, args,
                                project_id=number(int, facts.get("project_id"), 0), role=role if role in MR_ROLES else ""))

    for key, now in new.items():
        before = old.get(key)
        if before is None:
            if now.get("reviewer"):
                add("review_requested", key, now, "Review requested: {mr} by @{author}", mr=key, author=now.get("author", ""))
            continue
        if now.get("role") == "author":
            new_approvers = sorted(set(now.get("approved_by", [])) - set(before.get("approved_by", [])))
            for user in new_approvers:
                add("approved", key, now, "@{user} approved {mr}", user=user, mr=key)
            if now.get("blocking_resolved") and before.get("blocking_resolved") is False and now.get("threads", 0) > 0:
                add("resolved", key, now, "All threads are resolved on {mr}", mr=key)
            if now.get("pipeline") == "failed" and before.get("pipeline") not in ("failed", None):
                add("pipeline", key, now, "The pipeline failed on {mr}", mr=key)
        if now.get("reviewer"):
            if not before.get("reviewer"):
                add("review_requested", key, now, "Review requested: {mr} by @{author}", mr=key, author=now.get("author", ""))
            gained = now.get("my_resolved", 0) - before.get("my_resolved", 0)
            if gained > 0:
                add("my_threads_resolved", key, now, "{n} of your threads resolved on {mr}", n=gained, mr=key)
            more = now.get("replies", 0) - before.get("replies", 0)
            if more > 0:
                add("replies", key, now, "@{user} replied in your threads on {mr}", user=now.get("last_replier", ""), mr=key)

    for key, state in (gone_states or {}).items():
        facts = old.get(key, {})
        if state == "merged" and facts.get("role") == "author":
            add("merged", key, facts, "{mr} was merged", mr=key)
    return events


# ---- watched branches: their newest pipeline and new commits


def project_path(project: str) -> str:
    """“group/repo” → “group%2Frepo”: the API takes a project by its URL-encoded path."""
    return urllib.parse.quote(project, safe="")


def _row_failed(exc: GitLabError) -> bool:
    """A watch row the server does not know (a typo, a removed branch) spoils only that
    row; a bad token or a dead server stops the whole check."""
    msg = str(exc)
    return msg.startswith("HTTP ")


_PIPE_FACTS = ("status", "id", "url", "finished_at", "sha")


def _watch_pipelines(client: GitLabClient, watches: list[PipelineWatch], old: dict[str, dict]) -> dict[str, dict]:
    """key → {status, id, url, finished_at, sha} of the newest pipeline with an outcome:
    one request a watch for its few newest pipelines; a running one is skipped, so
    “failed → running → success” is seen as green again. A row that fails keeps its old
    facts and says why under "error"."""
    out: dict[str, dict] = {}
    for w in watches:
        before = old.get(w.key(), {})
        kept = {k: before[k] for k in _PIPE_FACTS if k in before}
        try:
            pipes = client.get(f"/projects/{project_path(w.project)}/pipelines", ref=w.ref, source=w.source or None, per_page=5)
        except GitLabError as exc:
            if not _row_failed(exc):
                raise
            out[w.key()] = kept | {"error": str(exc)}
            continue
        done = [p for p in pipes if isinstance(p, dict) and p.get("status") in FINISHED] if isinstance(pipes, list) else []
        if not done:
            out[w.key()] = kept
            continue
        newest = max(done, key=lambda p: number(int, p.get("id"), 0))
        # the pipelines LIST gives no finished_at (only the single-pipeline endpoint does):
        # updated_at stands in for it, the last change of a finished pipeline is its end
        out[w.key()] = {
            "status": str(newest.get("status", "")), "id": number(int, newest.get("id"), 0), "url": web_url(newest.get("web_url")),
            "finished_at": str(newest.get("finished_at") or newest.get("updated_at") or ""), "sha": str(newest.get("sha", "")),
        }
    return out


def _is_me(commit: dict, me: dict) -> bool:
    """A commit has no username, only the name and the e-mail written into it."""
    emails = {str(me.get(k) or "").lower() for k in ("email", "commit_email", "public_email")} - {""}
    if str(commit.get("author_email") or "").lower() in emails:
        return True
    return bool(me.get("name")) and commit.get("author_name") == me.get("name")


def _branch_commits(client: GitLabClient, watches: list[PipelineWatch], old: dict[str, dict], me: dict) -> dict[str, dict]:
    """key → {head, head_at, new_commits, author, commit_url, commit_title}: the commits
    pushed since the head seen last time, mine left out. One request a watch; the first
    look only remembers the head."""
    out: dict[str, dict] = {}
    for w in watches:
        before = old.get(w.key(), {})
        head, head_at = before.get("head"), str(before.get("head_at") or "")
        try:
            got = client.get(f"/projects/{project_path(w.project)}/repository/commits", ref_name=w.ref, since=head_at or None, per_page=100)
        except GitLabError as exc:
            if not _row_failed(exc):
                raise
            out[w.key()] = {"head": head, "head_at": head_at, "new_commits": 0, "error": str(exc)}
            continue
        commits = [c for c in got if isinstance(c, dict) and c.get("id")] if isinstance(got, list) else []
        fresh: list[dict] = []
        if head:  # newest first: everything above the old head is new
            for c in commits:
                if c["id"] == head:
                    break
                fresh.append(c)
        others = [c for c in fresh if not _is_me(c, me)]
        facts: dict[str, Any] = {
            "head": commits[0]["id"] if commits else head,
            "head_at": str(commits[0].get("committed_date") or head_at) if commits else head_at,
            "new_commits": len(others),
        }
        if others:
            facts |= {"author": str(others[0].get("author_name", "")), "commit_url": web_url(others[0].get("web_url")), "commit_title": str(others[0].get("title", ""))}
        out[w.key()] = facts
    return out


def take_pipelines(client: GitLabClient, me: dict, settings: GitLabSettings, old: dict[str, dict]) -> dict[str, dict]:
    """The facts of every watched branch, asking only for what the switched-on rules need."""
    watches = settings.pipelines
    state: dict[str, dict] = {w.key(): {"project": w.project, "ref": w.ref, "name": w.label()} for w in watches}
    if settings.rule("watch_failed") or settings.rule("watch_fixed"):
        for key, facts in _watch_pipelines(client, watches, old).items():
            state[key].update(facts)
    if settings.rule("branch_commits"):
        for key, facts in _branch_commits(client, watches, old, me).items():
            error = state[key].get("error") or facts.pop("error", None)
            state[key].update(facts)
            if error:
                state[key]["error"] = error
    return state


def diff_pipelines(old: dict[str, dict], new: dict[str, dict], rules: GitLabSettings) -> list[Event]:
    """Events between two looks at the watched branches. A row seen for the first time is
    the baseline; a failed pipeline is news once, the same one failed again is not."""
    events: list[Event] = []

    def add(kind: str, facts: dict, url: str, text: str, **args) -> None:
        if rules.rule(kind):
            ref = f"{facts.get('project', '')}@{facts.get('ref', '')}"
            name = facts.get("name") or ref
            events.append(Event(kind, ref, name, url, text, {"name": name, "project": facts.get("project", ""), **args}))

    for key, now in new.items():
        before = old.get(key)
        if before is None:
            continue
        status, was = now.get("status"), before.get("status")
        if status == "failed" and (now.get("id") != before.get("id") or was != "failed"):
            add("watch_failed", now, now.get("url", ""), "The pipeline failed on {name}")
        if status == "success" and was == "failed":
            add("watch_fixed", now, now.get("url", ""), "{name} is green again")
        if now.get("new_commits", 0) > 0 and before.get("head"):
            add("branch_commits", now, now.get("commit_url", ""), "{n} new commits on {name}, the last by {author}", n=now["new_commits"], author=now.get("author", ""))
    return events


@dataclass
class CheckResult:
    """What one check brought back: who I am, the new snapshot, the events since the old one."""

    username: str
    snapshot: dict[str, dict]
    events: list[Event]
    projects: list[str] = field(default_factory=list)  # all the projects with my open MRs
    started: float = 0.0  # when the check began: the next light one looks back from here
    full: bool = True
    pipelines: dict[str, dict] | None = None  # the watched branches; None — not looked at this time


def check(url: str, token: str, settings: GitLabSettings, old: dict[str, dict], fetch=None, since: float = 0.0,
          pipelines: dict[str, dict] | None = None) -> CheckResult:
    """One whole check against the server (meant for a background thread): me, the
    snapshot, the events against `old` — none when `old` is empty, the first look is the
    baseline. `since` — the start of the last check for a light one, 0 for a full one.
    `pipelines` — the last facts of the watched branches; None leaves them out. Light or
    full, each watch costs a request a rule kind. Raises GitLabError when the server cannot
    be reached or refuses."""
    started = time.time()
    since = since if old else 0.0
    client = GitLabClient(url, token, fetch=fetch)
    try:
        me = client.me()
        seen: set[str] = set()
        new = take_snapshot(client, me, settings, old, since, seen)
        # an MR of a project no longer watched is not asked about: it did not close
        gone = {k: v for k, v in old.items() if k not in new and settings.watches(project_of(k))}
        events = diff_snapshots(old, new, settings, closed_states(client, gone)) if old else []
        watched = None
        if pipelines is not None:
            watched = take_pipelines(client, me, settings, pipelines)
            events += diff_pipelines(pipelines, watched, settings)
        return CheckResult(str(me.get("username", "")), new, events, sorted(seen), started, full=since <= 0, pipelines=watched)
    finally:
        client.close()
