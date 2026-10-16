"""GitLab watcher: what changed in my merge requests since the last look.

`GitLabClient` reads the REST API (v4) with a personal access token (scope `read_api`).
`take_snapshot` turns the API answers into plain dicts — one per open MR where I am the
author or a reviewer. `diff_snapshots` compares two snapshots and returns events for the
rules that are switched on. Between full checks only what may have changed is asked for:
approvals and pipelines once a project, threads only where the count of notes moved. The
`updated_at` of an MR is no help here: an approval, a finished pipeline or a resolved
thread leaves it as it was. The client is the only part that touches the network; the
rest is pure and unit-tested.
"""

from __future__ import annotations

import http.client
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

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
INTERVALS = (2, 5, 10, 15, 30, 60)  # minutes between checks
FULL_EVERY = 3600  # s; a full check asks about every MR again, whatever the light ones saw
SINCE_MARGIN = 600  # s; a light check looks this far before the last one (clocks, a slow check)
FEED_KEEP = 200
TIMEOUT_TEXT = "GitLab did not answer in time"
PLAIN_HTTP_TEXT = "The token is sent over https only — write the address with https://"
FOREIGN_REDIRECT_TEXT = "GitLab sends the request to another server — check the address in the settings"
_LOOPBACK = ("localhost", "127.0.0.1", "::1")


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


class _SameHostRedirects(urllib.request.HTTPRedirectHandler):
    """urllib copies every header, the token too, onto the redirected request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _token_may_go(newurl, req.full_url)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitLabError(Exception):
    """The API could not be reached or refused the request."""


@dataclass
class GitLabSettings:
    url: str = ""  # e.g. https://gitlab.example.com
    entry_id: str = ""  # vault entry whose secret field holds the token
    interval_min: int = 5
    rules: dict[str, bool] = field(default_factory=lambda: {k: on for k, (_l, on) in RULES.items()})
    enabled: bool = False
    username: str = ""  # filled by “Check connection”
    projects: list[str] = field(default_factory=list)  # paths of the watched projects; [] — all

    def watches(self, project: str) -> bool:
        return not self.projects or project in self.projects

    def rule(self, code: str) -> bool:
        return self.rules.get(code, RULES[code][1])

    def to_dict(self) -> dict:
        return {
            "url": self.url, "entry_id": self.entry_id, "interval_min": self.interval_min,
            "rules": dict(self.rules), "enabled": self.enabled, "username": self.username,
            "projects": list(self.projects),
        }

    @classmethod
    def from_dict(cls, d) -> GitLabSettings:
        if not isinstance(d, dict):
            return cls()
        interval = d.get("interval_min", 5)
        rules = {k: bool(v) for k, v in (d.get("rules") or {}).items() if k in RULES}
        return cls(
            url=str(d.get("url", "")).strip().rstrip("/"),
            entry_id=str(d.get("entry_id", "")),
            interval_min=interval if interval in INTERVALS else 5,
            rules={k: rules.get(k, on) for k, (_l, on) in RULES.items()},
            enabled=bool(d.get("enabled", False)),
            username=str(d.get("username", "")),
            projects=[x for x in projects if isinstance(x, str) and x] if isinstance(projects := d.get("projects"), list) else [],
        )


@dataclass
class Event:
    kind: str  # a RULES code
    mr: str  # "group/repo!12"
    title: str
    url: str
    text: str  # what happened, in English UI keys formatted by the caller
    args: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    read: bool = False

    def to_dict(self) -> dict:
        return {"kind": self.kind, "mr": self.mr, "title": self.title, "url": self.url, "text": self.text, "args": self.args, "ts": self.ts, "read": self.read}

    @classmethod
    def from_dict(cls, d: dict) -> Event:
        return cls(
            kind=str(d.get("kind", "")), mr=str(d.get("mr", "")), title=str(d.get("title", "")), url=web_url(d.get("url")),
            text=str(d.get("text", "")), args=dict(d["args"]) if isinstance(d.get("args"), dict) else {}, ts=number(float, d.get("ts"), None) or time.time(), read=bool(d.get("read", False)),
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
        self._conn: http.client.HTTPConnection | None = None
        self._conn_key: tuple[str, str] = ("", "")

    def _http(self, url: str, headers: dict) -> tuple[int, bytes]:
        """One kept-alive connection for the whole check. urllib asks the server to close
        the connection after each answer (Connection: close), and some GitLab front ends
        then sometimes send no answer at all: the check hung until the read timeout."""
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
        _token_may_go(url, self.base)
        req = urllib.request.Request(url, headers=headers)  # noqa: S310 — https only, checked by _token_may_go above
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()), _SameHostRedirects)
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
            events.append(Event(kind, key, facts.get("title", ""), facts.get("url", ""), text, args))

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


@dataclass
class CheckResult:
    """What one check brought back: who I am, the new snapshot, the events since the old one."""

    username: str
    snapshot: dict[str, dict]
    events: list[Event]
    projects: list[str] = field(default_factory=list)  # all the projects with my open MRs
    started: float = 0.0  # when the check began: the next light one looks back from here
    full: bool = True


def check(url: str, token: str, settings: GitLabSettings, old: dict[str, dict], fetch=None, since: float = 0.0) -> CheckResult:
    """One whole check against the server (meant for a background thread): me, the
    snapshot, the events against `old` — none when `old` is empty, the first look is the
    baseline. `since` — the start of the last check for a light one, 0 for a full one.
    Raises GitLabError when the server cannot be reached or refuses."""
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
        return CheckResult(str(me.get("username", "")), new, events, sorted(seen), started, full=since <= 0)
    finally:
        client.close()
