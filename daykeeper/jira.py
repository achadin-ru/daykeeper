"""Jira watcher: what changed in my issues since the last look, and a transition on demand.

`JiraClient` reads the REST API (v2, the same on Jira Cloud and Data Center). The way in
is chosen by the address: `*.atlassian.net` is Jira Cloud — Basic with the e-mail and an
API token; any other server is Data Center — Bearer with a personal access token.
`take_snapshot` turns the issues of the filter into plain dicts, `diff_snapshots` compares
two snapshots and returns events for the rules that are switched on. Unlike an MR in
GitLab, a transition or a comment moves the `updated` of an issue, so a light check asks
only for what was updated lately. The client is the only part that touches the network;
the rest is pure and unit-tested. Events are `gitlab.Event`, so they share one feed.
"""

from __future__ import annotations

import base64
import json
import math
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime

from .gitlab import FOREIGN_REDIRECT_TEXT, FULL_EVERY, INTERVALS, CheckResult, Event, GitLabError, _token_may_go, web_url

__all__ = ["FULL_EVERY", "RULES", "CheckResult", "JiraClient", "JiraError", "JiraSettings", "check", "diff_snapshots", "take_snapshot", "transition_by_name"]

# Rules: code → (UI label key, on by default).
RULES: dict[str, tuple[str, bool]] = {
    "status": ("An issue moved to a watched status", True),
    "assigned_bug": ("A bug was assigned to me", True),
    "commented": ("A new comment in my issue", True),
    "reopened": ("My issue was sent back to work", False),
}
DEFAULT_STATUSES = ("Ready for test",)
DEFAULT_JQL = "assignee = currentUser() OR reporter = currentUser() OR watcher = currentUser()"
VERDICTS = ("pass", "fail")  # the keys of JiraSettings.transitions: a verdict → the transition to offer
BUG_TYPES = ("bug", "defect")
SINCE_MARGIN = 600  # s; a light check looks this far before the last one (clocks, a slow check)
PAGE = 100
SEARCH_LIMIT = 1000  # issues one check reads at most
FIELDS = ("summary", "status", "assignee", "reporter", "issuetype", "comment", "created", "updated")
TIMEOUT_TEXT = "Jira did not answer in time"
JIRA_REDIRECT_TEXT = "Jira sends the request to another server — check the address in the settings"
BROKEN_ANSWER_TEXT = "Jira sent a broken answer — try again later"
NO_LOGIN_TEXT = "Jira Cloud needs the login (your e-mail) next to the API token"
# a status category → how far along the work is; a watched status sits between work and done
_RANK = {"new": 0.0, "indeterminate": 1.0, "done": 2.0}
_WATCHED_RANK = 1.5


class JiraError(Exception):
    """The API could not be reached or refused the request."""


def is_cloud(url: str) -> bool:
    host = urllib.parse.urlsplit(_normalize(url)).hostname or ""
    return host == "atlassian.net" or host.endswith(".atlassian.net")


def _normalize(url: str) -> str:
    url = str(url or "").strip().rstrip("/")
    if url and "://" not in url:
        url = "https://" + url  # “jira.example.com” typed without the scheme
    return url


def _guard(url: str, origin: str) -> None:
    """The https-only guard of gitlab.py, with the Jira words."""
    try:
        _token_may_go(url, origin)
    except GitLabError as exc:
        text = str(exc)
        raise JiraError(JIRA_REDIRECT_TEXT if text == FOREIGN_REDIRECT_TEXT else text) from exc


def parse_statuses(text: str) -> list[str]:
    """“Ready for test, In review” → ["Ready for test", "In review"]; repeats dropped."""
    return _unique(str(text or "").split(","))


def _unique(names) -> list[str]:
    out: list[str] = []
    for part in names:
        name = part.strip()
        if name and name.casefold() not in (x.casefold() for x in out):
            out.append(name)
    return out


@dataclass
class JiraSettings:
    url: str = ""  # e.g. https://jira.example.com or https://example.atlassian.net
    entry_id: str = ""  # vault entry: the secret field holds the token, the login field the e-mail (Cloud)
    interval_min: int = 5
    rules: dict[str, bool] = field(default_factory=lambda: {k: on for k, (_l, on) in RULES.items()})
    statuses: list[str] = field(default_factory=lambda: list(DEFAULT_STATUSES))
    jql: str = ""  # "" — DEFAULT_JQL
    transitions: dict[str, str] = field(default_factory=dict)  # "pass" → "Done", "fail" → "Test failed"; empty — nothing offered
    enabled: bool = False
    username: str = ""  # filled by “Check connection”

    def rule(self, code: str) -> bool:
        return self.rules.get(code, RULES[code][1])

    def query(self) -> str:
        return self.jql.strip() or DEFAULT_JQL

    def watched(self, status: str) -> bool:
        return status.casefold() in (s.casefold() for s in self.statuses)

    def to_dict(self) -> dict:
        return {
            "url": self.url, "entry_id": self.entry_id, "interval_min": self.interval_min, "rules": dict(self.rules),
            "statuses": list(self.statuses), "jql": self.jql, "transitions": dict(self.transitions),
            "enabled": self.enabled, "username": self.username,
        }

    @classmethod
    def from_dict(cls, d) -> JiraSettings:
        if not isinstance(d, dict):
            return cls()
        interval = d.get("interval_min", 5)
        raw_rules = d.get("rules")
        rules = {k: bool(v) for k, v in raw_rules.items() if k in RULES} if isinstance(raw_rules, dict) else {}
        raw_statuses = d.get("statuses", list(DEFAULT_STATUSES))
        if isinstance(raw_statuses, str):
            statuses = parse_statuses(raw_statuses)
        elif isinstance(raw_statuses, list):
            statuses = _unique(x for x in raw_statuses if isinstance(x, str))
        else:
            statuses = list(DEFAULT_STATUSES)
        raw_tr = d.get("transitions")
        transitions = {k: v.strip() for k, v in raw_tr.items() if k in VERDICTS and isinstance(v, str) and v.strip()} if isinstance(raw_tr, dict) else {}
        return cls(
            url=_normalize(str(d.get("url", ""))) if str(d.get("url", "")).strip() else "",
            entry_id=str(d.get("entry_id", "")),
            interval_min=interval if isinstance(interval, int) and interval in INTERVALS else 5,
            rules={k: rules.get(k, on) for k, (_l, on) in RULES.items()},
            statuses=statuses,
            jql=d["jql"].strip() if isinstance(d.get("jql"), str) else "",
            transitions=transitions,
            enabled=bool(d.get("enabled", False)),
            username=str(d.get("username", "")),
        )


class JiraClient:
    """A tiny REST client. `fetch(url, headers, method="GET", data=None)` → (status, body
    bytes) can be swapped in tests; the https guard runs before it either way."""

    def __init__(self, base_url: str, token: str, login: str = "", fetch=None, timeout: float = 20.0):
        self.site = _normalize(base_url)
        self.base = self.site + "/rest/api/2"
        self.cloud = is_cloud(self.site)
        self.token = token
        self.login = login.strip()
        self.timeout = timeout
        self._fetch = fetch or self._http

    def _auth(self) -> str:
        if self.cloud:
            if not self.login:
                raise JiraError(NO_LOGIN_TEXT)
            return "Basic " + base64.b64encode(f"{self.login}:{self.token}".encode()).decode("ascii")
        return "Bearer " + self.token

    def _http(self, url: str, headers: dict, method: str = "GET", data: bytes | None = None) -> tuple[int, bytes]:
        """urllib with the redirect guard of gitlab.py (a redirect keeps the token on the
        same server). The network modules are imported on the first request."""
        import http.client
        import ssl
        import urllib.error
        import urllib.request

        from .gitlab import _same_host_redirects

        req = urllib.request.Request(url, data=data, headers={**headers, "User-Agent": "Daykeeper"}, method=method)  # noqa: S310 — https only, checked by _guard
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl.create_default_context()), _same_host_redirects())
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read() if exc.fp else b""
        except GitLabError as exc:  # the redirect guard
            text = str(exc)
            raise JiraError(JIRA_REDIRECT_TEXT if text == FOREIGN_REDIRECT_TEXT else text) from exc
        except TimeoutError as exc:
            raise JiraError(TIMEOUT_TEXT) from exc
        except http.client.HTTPException as exc:  # a broken answer: BadStatusLine, IncompleteRead, …
            raise JiraError(BROKEN_ANSWER_TEXT) from exc
        except (urllib.error.URLError, OSError, ssl.SSLError) as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                raise JiraError(TIMEOUT_TEXT) from exc
            raise JiraError(str(reason) or type(exc).__name__) from exc

    def _call(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> object:
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = f"{self.base}{path}" + (f"?{query}" if query else "")
        _guard(url, self.site)  # before the token is even put into a header
        headers = {"Authorization": self._auth(), "Accept": "application/json"}
        if body is None:
            status, raw = self._fetch(url, headers)
        else:
            headers["Content-Type"] = "application/json"
            status, raw = self._fetch(url, headers, method=method, data=json.dumps(body).encode("utf-8"))
        if status == 401:
            raise JiraError("401 Unauthorized — the token or the login is wrong or expired")
        if status == 403:
            raise JiraError("403 Forbidden — the token may not do this")
        if status >= 400:
            detail = _error_text(raw)
            raise JiraError(f"HTTP {status} on {path}" + (f": {detail}" if detail else ""))
        if not raw.strip():
            return None  # 204 No Content after a transition
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise JiraError(f"Not JSON from {path}") from exc

    def get(self, path: str, **params) -> object:
        return self._call("GET", path, params)

    def myself(self) -> dict:
        try:
            user = self.get("/myself")
        except JiraError as exc:
            if str(exc).startswith("HTTP 404"):  # a board or issue link instead of the server address
                raise JiraError("No Jira API at this address — it should be the server itself, e.g. https://jira.example.com") from exc
            raise
        if not isinstance(user, dict) or not _uid(user):
            raise JiraError("Unexpected answer from /myself")
        return user

    def search(self, jql: str, fields: list[str] | tuple[str, ...] = FIELDS, since: float = 0.0, limit: int = SEARCH_LIMIT) -> list[dict]:
        """Issues of the filter, pages of 100 with the changelog. `since` > 0 — only those
        updated after it (with a margin). Cloud pages by a token (`/search/jql`), Data Center
        by `startAt` (`/search`)."""
        jql = _updated_since(jql, since)
        params: dict = {"jql": jql, "fields": ",".join(fields), "expand": "changelog", "maxResults": PAGE}
        out: list[dict] = []
        token: str | None = None
        while len(out) < limit:
            if self.cloud:
                answer = self.get("/search/jql", **params, nextPageToken=token)
            else:
                answer = self.get("/search", **params, startAt=len(out))
            issues = [i for i in (answer.get("issues") or []) if isinstance(i, dict)] if isinstance(answer, dict) else []
            out.extend(issues)
            if not issues or not isinstance(answer, dict):
                break
            if self.cloud:
                token = answer.get("nextPageToken")
                if answer.get("isLast", not token) or not token:
                    break
            else:
                total = answer.get("total")
                if len(issues) < PAGE or (isinstance(total, int) and len(out) >= total):
                    break
        return out[:limit]

    def transitions(self, key: str) -> list[tuple[str, str, str]]:
        """The transitions open for the issue now: (id, name, name of the target status)."""
        answer = self.get(f"/issue/{urllib.parse.quote(key)}/transitions")
        items = answer.get("transitions") if isinstance(answer, dict) else None
        return [
            (str(t.get("id", "")), str(t.get("name", "")), str((t.get("to") or {}).get("name", "")))
            for t in (items or []) if isinstance(t, dict) and t.get("id") is not None
        ]

    def transition(self, key: str, transition_id: str) -> None:
        self._call("POST", f"/issue/{urllib.parse.quote(key)}/transitions", body={"transition": {"id": str(transition_id)}})

    def browse(self, key: str) -> str:
        return f"{self.site}/browse/{key}"


def transition_by_name(client: JiraClient, key: str, name: str) -> str:
    """Move the issue by the transition called `name` (or the one leading to the status of
    that name). Returns the id that was sent; raises JiraError naming what is open instead."""
    wanted = name.strip().casefold()
    found = client.transitions(key)
    match = next((t for t in found if t[1].casefold() == wanted), None) or next((t for t in found if t[2].casefold() == wanted), None)
    if not wanted or match is None:
        open_now = ", ".join(t[1] for t in found) or "none"
        raise JiraError(f"No transition “{name}” for {key} — open now: {open_now}")
    client.transition(key, match[0])
    return match[0]


def _error_text(raw: bytes) -> str:
    try:
        answer = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return ""
    if not isinstance(answer, dict):
        return ""
    msgs = [str(m) for m in (answer.get("errorMessages") or []) if m]
    errors = answer.get("errors")
    if isinstance(errors, dict):
        msgs += [f"{k}: {v}" for k, v in errors.items()]
    return "; ".join(msgs)[:300]


_ORDER_BY = re.compile(r"\s+order\s+by\s+.*$", re.IGNORECASE | re.DOTALL)


def _updated_since(jql: str, since: float) -> str:
    """The filter narrowed to issues updated since `since` (minutes, relative: the server's
    time zone does not matter). An ORDER BY of the user's filter stays at the end."""
    if since <= 0:
        return jql
    minutes = max(1, math.ceil((time.time() - since + SINCE_MARGIN) / 60))
    order = _ORDER_BY.search(jql)
    head = jql[: order.start()] if order else jql
    return f'({head.strip()}) AND updated >= "-{minutes}m"' + (order.group(0) if order else "")


def _uid(user) -> str:
    """One id for a user in any answer: accountId on Cloud, key (or name) on Data Center."""
    if not isinstance(user, dict):
        return ""
    return str(user.get("accountId") or user.get("key") or user.get("name") or "")


def _uname(user) -> str:
    if not isinstance(user, dict):
        return ""
    return str(user.get("name") or user.get("displayName") or user.get("emailAddress") or user.get("accountId") or "")


def _when(text) -> float:
    """“2026-10-14T10:00:00.000+0000” → epoch seconds; 0 when it does not parse."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(str(text), fmt).timestamp()
        except ValueError:
            continue
    return 0.0


def _last_change(issue: dict, field_name: str) -> tuple[str, float]:
    """Who made the newest change of a field and when, from the changelog."""
    histories = (issue.get("changelog") or {}).get("histories") or []
    best = ("", 0.0)
    for h in histories:
        if not isinstance(h, dict):
            continue
        if any(isinstance(i, dict) and str(i.get("field", "")).casefold() == field_name for i in (h.get("items") or [])):
            at = _when(h.get("created"))
            if at >= best[1]:
                best = (_uid(h.get("author")), at)
    return best


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _facts(client: JiraClient, issue: dict, me_id: str) -> dict:
    f: dict = _dict(issue.get("fields"))
    status, box = _dict(f.get("status")), _dict(f.get("comment"))
    comments = [c for c in (box.get("comments") or []) if isinstance(c, dict)]
    total = box.get("total")
    # the newest by time; of two in the same second, the later in the list
    last = max(enumerate(comments), key=lambda ic: (_when(ic[1].get("created")), ic[0]), default=(0, None))[1]
    status_by, status_at = _last_change(issue, "status")
    assignee_by, assignee_at = _last_change(issue, "assignee")
    if not assignee_at:  # assigned when the issue was created: no change in the changelog
        assignee_at = _when(f.get("created"))
    key = str(issue.get("key", ""))
    assignee, reporter = _uid(f.get("assignee")), _uid(f.get("reporter"))
    return {
        "summary": str(f.get("summary") or ""),
        "status": str(status.get("name") or ""),
        "category": str((status.get("statusCategory") or {}).get("key") or ""),
        "assignee": assignee,
        "type": str((f.get("issuetype") or {}).get("name") or ""),
        "mine": bool(me_id) and me_id in (assignee, reporter),
        "comments": total if isinstance(total, int) else len(comments),
        "last_comment_by": _uid(last.get("author")) if last else "",
        "last_comment_name": _uname(last.get("author")) if last else "",
        "status_by": status_by,
        "status_at": status_at,
        "assignee_by": assignee_by,
        "assignee_at": assignee_at,
        "updated": str(f.get("updated") or ""),
        "url": web_url(client.browse(key)),
    }


def take_snapshot(client: JiraClient, me: dict, settings: JiraSettings, old: dict[str, dict] | None = None, since: float = 0.0) -> dict[str, dict]:
    """Issues of the filter → {key: facts}. `since` — when the last check began; 0 makes
    this a full check that reads the whole filter (an issue that left it leaves the
    snapshot). A light one reads only what was updated and keeps the rest of `old`."""
    me_id = _uid(me)
    snap: dict[str, dict] = dict(old or {}) if since > 0 else {}
    for issue in client.search(settings.query(), FIELDS, since):
        if issue.get("key"):
            facts = _facts(client, issue, me_id)
            facts["me"] = me_id  # diff_snapshots needs to know who “me” is
            snap[str(issue["key"])] = facts
    return snap


def _rank(facts: dict, settings: JiraSettings) -> float:
    if facts.get("category") == "done":
        return _RANK["done"]
    if settings.watched(str(facts.get("status", ""))):
        return _WATCHED_RANK
    return _RANK.get(str(facts.get("category", "")), _RANK["indeterminate"])


def _is_bug(facts: dict) -> bool:
    return str(facts.get("type", "")).casefold() in BUG_TYPES


def _recent(facts: dict, at_key: str, since: float) -> bool:
    """The change behind `at_key` happened after `since` (the start of the last check)."""
    return since > 0 and float(facts.get(at_key) or 0) >= since - SINCE_MARGIN


def _event(kind: str, key: str, facts: dict, text: str, args: dict) -> Event:
    """An Event of the shared feed, marked as coming from Jira."""
    return Event(kind, key, str(facts.get("summary", "")), str(facts.get("url", "")), text, args, source="jira")


def diff_snapshots(old: dict[str, dict], new: dict[str, dict], settings: JiraSettings, since: float = 0.0) -> list[Event]:
    """Events between two snapshots. What I did myself raises nothing. An issue seen for
    the first time raises only what happened after `since` (the start of the last check):
    a bug assigned to me, a move into a watched status. A full check reads issues that only
    drifted into the filter (an edited JQL, a filter above SEARCH_LIMIT), so “first seen”
    alone says nothing about when."""
    events: list[Event] = []

    def add(kind: str, ref: str, facts: dict, text: str, **args) -> None:
        if settings.rule(kind):
            events.append(_event(kind, ref, facts, text, args))

    for key, now in new.items():
        me = str(now.get("me", ""))
        before = old.get(key)
        by_other = now.get("status_by") != me or not me
        if before is None:
            if _recent(now, "status_at", since) and by_other and settings.watched(str(now.get("status", ""))):
                add("status", key, now, "{key} moved to {status}", key=key, status=now.get("status", ""))
            if _recent(now, "assignee_at", since) and me and now.get("assignee") == me and _is_bug(now) and now.get("assignee_by") != me:
                add("assigned_bug", key, now, "Bug {key} was assigned to you", key=key)
            continue
        moved = now.get("status") != before.get("status")
        if moved and by_other and settings.watched(str(now.get("status", ""))) and not settings.watched(str(before.get("status", ""))):
            add("status", key, now, "{key} moved to {status}", key=key, status=now.get("status", ""))
        if moved and by_other and now.get("mine") and _rank(now, settings) < _rank(before, settings):
            add("reopened", key, now, "{key} was sent back to {status}", key=key, status=now.get("status", ""))
        if me and now.get("assignee") == me and before.get("assignee") != me and _is_bug(now) and now.get("assignee_by") != me:
            add("assigned_bug", key, now, "Bug {key} was assigned to you", key=key)
        more = int(now.get("comments") or 0) - int(before.get("comments") or 0)
        if more > 0 and now.get("mine") and now.get("last_comment_by") and now.get("last_comment_by") != me:
            add("commented", key, now, "@{user} commented on {key}", user=now.get("last_comment_name", ""), key=key)
    return events


def check(url: str, token: str, login: str, settings: JiraSettings, old: dict[str, dict], fetch=None, since: float = 0.0,
          previous: float | None = None) -> CheckResult:
    """One whole check against the server (meant for a background thread): me, the
    snapshot, the events against `old` — none when `old` is empty, the first look is the
    baseline. `since` — the start of the last check for a light one, 0 for a full one.
    `previous` — the start of the last check that worked, also for a full one (an issue
    new to the snapshot is judged by what happened after it); by default `since`.
    Raises JiraError when the server cannot be reached or refuses."""
    started = time.time()
    since = since if old else 0.0
    previous = since if previous is None else previous
    client = JiraClient(url, token, login, fetch=fetch)
    me = client.myself()
    new = take_snapshot(client, me, settings, old, since)
    events = diff_snapshots(old, new, settings, previous) if old else []
    return CheckResult(_uname(me), new, events, [], started, full=since <= 0)
