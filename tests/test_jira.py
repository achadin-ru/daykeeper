"""Jira watcher: the client against a fake API, snapshots and the events between them."""
import base64
import http.client
import json
import time
import unittest
import urllib.parse
from unittest import mock

from daykeeper import gitlab
from daykeeper.jira import (
    DEFAULT_JQL, RULES, JiraClient, JiraError, JiraSettings, check, diff_snapshots, is_cloud, parse_statuses, take_snapshot,
    transition_by_name,
)

CLOUD = "https://example.atlassian.net"
DC = "https://jira.example.com"
ME_CLOUD = {"accountId": "acc-me", "displayName": "Me Myself", "emailAddress": "me@example.com"}
ME_DC = {"name": "me", "key": "JIRAUSER1", "displayName": "Me Myself"}
OTHER_CLOUD = {"accountId": "acc-ann", "displayName": "Ann"}
OTHER_DC = {"name": "ann", "key": "JIRAUSER2", "displayName": "Ann"}


def stamp(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S.000+0000", time.gmtime(t))


class FakeJira:
    """Answers the endpoints the watcher uses. `cloud` picks the shapes of Jira Cloud
    (accountId, /search/jql with a page token) or Data Center (name/key, /search with startAt)."""

    def __init__(self, cloud: bool):
        self.cloud = cloud
        self.me = ME_CLOUD if cloud else ME_DC
        self.other = OTHER_CLOUD if cloud else OTHER_DC
        self.issues: dict[str, dict] = {}
        self.transitions = {"DEMO-1": [{"id": "31", "name": "Pass", "to": {"name": "Done"}}, {"id": "41", "name": "Fail", "to": {"name": "Test failed"}}]}
        self.calls: list[tuple[str, str, dict, bytes | None]] = []
        self.posted: list[tuple[str, dict]] = []

    def add(self, key, status="In Progress", category="indeterminate", assignee=None, reporter=None, itype="Task", summary="Fix"):
        self.issues[key] = {
            "key": key,
            "fields": {
                "summary": summary, "status": {"name": status, "statusCategory": {"key": category}},
                "assignee": assignee, "reporter": reporter if reporter is not None else self.me,
                "issuetype": {"name": itype}, "comment": {"comments": [], "total": 0, "maxResults": 50, "startAt": 0},
                "created": stamp(time.time()), "updated": stamp(time.time()),
            },
            "changelog": {"startAt": 0, "maxResults": 100, "total": 0, "histories": []},
        }
        return self.issues[key]

    def move(self, key, status, category="indeterminate", by=None):
        issue = self.issues[key]
        old = issue["fields"]["status"]["name"]
        issue["fields"]["status"] = {"name": status, "statusCategory": {"key": category}}
        self._history(issue, by or self.other, {"field": "status", "fieldtype": "jira", "fromString": old, "toString": status})

    def assign(self, key, user, by=None):
        issue = self.issues[key]
        issue["fields"]["assignee"] = user
        self._history(issue, by or self.other, {"field": "assignee", "fieldtype": "jira", "to": user.get("accountId") or user.get("name")})

    def comment(self, key, author, body="Looks good"):
        box = self.issues[key]["fields"]["comment"]
        box["comments"].append({"id": str(len(box["comments"]) + 1), "author": author, "body": body, "created": stamp(time.time())})
        box["total"] = len(box["comments"])

    def _history(self, issue, author, item):
        if self.cloud:
            item = {**item, "fieldId": item["field"]}  # Cloud names the field by id as well
        histories = issue["changelog"]["histories"]
        histories.append({"id": str(len(histories) + 1), "author": author, "created": stamp(time.time()), "items": [item]})
        issue["changelog"]["total"] = len(histories)

    def __call__(self, url, headers, method="GET", data=None):
        self.calls.append((method, url, headers, data))
        auth = headers.get("Authorization", "")
        good = "Basic " + base64.b64encode(b"me@example.com:tok").decode() if self.cloud else "Bearer tok"
        if auth != good:
            return 401, b"{}"
        parsed = urllib.parse.urlsplit(url)
        path = parsed.path.replace("/rest/api/2", "")
        q = dict(urllib.parse.parse_qsl(parsed.query))
        if path == "/myself":
            return 200, json.dumps(self.me).encode()
        if path in ("/search", "/search/jql"):
            if (path == "/search/jql") != self.cloud:
                return 410, json.dumps({"errorMessages": ["The requested API has been removed"]}).encode()
            issues = list(self.issues.values())
            size = int(q.get("maxResults", 50))
            if self.cloud:
                start = int(q.get("nextPageToken") or 0)
                chunk = issues[start:start + size]
                more = start + size < len(issues)
                body = {"issues": chunk, "isLast": not more, **({"nextPageToken": str(start + size)} if more else {})}
            else:
                start = int(q.get("startAt", 0))
                body = {"startAt": start, "maxResults": size, "total": len(issues), "issues": issues[start:start + size]}
            return 200, json.dumps(body).encode()
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "issue" and parts[2] == "transitions":
            key = parts[1]
            if key not in self.transitions:
                return 404, json.dumps({"errorMessages": ["Issue does not exist"]}).encode()
            if method == "POST":
                self.posted.append((key, json.loads(data)))
                return 204, b""
            return 200, json.dumps({"expand": "transitions", "transitions": self.transitions[key]}).encode()
        return 404, b"{}"


def client_for(fake):
    return JiraClient(CLOUD if fake.cloud else DC, "tok", "me@example.com" if fake.cloud else "", fetch=fake)


def run(fake, old, settings=None, since=0.0):
    settings = settings or JiraSettings()
    client = client_for(fake)
    new = take_snapshot(client, client.myself(), settings, old, since)
    return new, diff_snapshots(old, new, settings, since) if old else []


class ClientTest(unittest.TestCase):
    def test_cloud_signs_in_with_basic_and_data_center_with_bearer(self):
        cloud, dc = FakeJira(cloud=True), FakeJira(cloud=False)
        self.assertEqual(client_for(cloud).myself()["accountId"], "acc-me")
        self.assertEqual(client_for(dc).myself()["name"], "me")
        self.assertTrue(cloud.calls[0][2]["Authorization"].startswith("Basic "))
        self.assertEqual(dc.calls[0][2]["Authorization"], "Bearer tok")
        self.assertTrue(is_cloud("example.atlassian.net"))
        self.assertFalse(is_cloud("https://atlassian.net.example.com"))

    def test_cloud_without_a_login_says_so_and_sends_nothing(self):
        fake = FakeJira(cloud=True)
        with self.assertRaises(JiraError) as ctx:
            JiraClient(CLOUD, "tok", "", fetch=fake).myself()
        self.assertIn("login", str(ctx.exception))
        self.assertEqual(fake.calls, [])

    def test_plain_http_refuses_to_send_the_token(self):
        fake = FakeJira(cloud=False)
        with self.assertRaises(JiraError) as ctx:
            JiraClient("http://jira.example.com", "tok", fetch=fake).myself()
        self.assertEqual(str(ctx.exception), gitlab.PLAIN_HTTP_TEXT)
        self.assertEqual(fake.calls, [])
        JiraClient("http://localhost:8080", "tok", fetch=fake).myself()  # this machine is fine
        self.assertEqual(len(fake.calls), 1)

    def test_address_without_scheme_gets_https(self):
        fake = FakeJira(cloud=False)
        JiraClient("jira.example.com/", "tok", fetch=fake).myself()
        self.assertTrue(fake.calls[0][1].startswith("https://jira.example.com/rest/api/2/myself"))

    def test_errors_are_plain(self):
        fake = FakeJira(cloud=False)
        with self.assertRaises(JiraError) as ctx:
            JiraClient(DC, "wrong", fetch=fake).myself()
        self.assertIn("401", str(ctx.exception))
        with self.assertRaises(JiraError) as ctx:
            client_for(fake).transitions("DEMO-404")
        self.assertIn("Issue does not exist", str(ctx.exception))

    def test_a_broken_answer_is_a_plain_error(self):
        class BrokenOpener:
            def open(self, req, timeout):
                raise http.client.BadStatusLine("garbage")

        with mock.patch("urllib.request.build_opener", return_value=BrokenOpener()), self.assertRaises(JiraError) as ctx:
            JiraClient(DC, "tok").myself()
        self.assertIn("broken answer", str(ctx.exception))

    def test_search_pages_by_startat_on_data_center(self):
        fake = FakeJira(cloud=False)
        for n in range(1, 251):
            fake.add(f"DEMO-{n}")
        found = client_for(fake).search(DEFAULT_JQL)
        self.assertEqual(len(found), 250)
        searches = [urllib.parse.parse_qs(urllib.parse.urlsplit(u).query) for _m, u, _h, _d in fake.calls]
        self.assertEqual([s["startAt"][0] for s in searches], ["0", "100", "200"])
        self.assertEqual(searches[0]["maxResults"], ["100"])
        self.assertEqual(searches[0]["expand"], ["changelog"])

    def test_search_pages_by_token_on_cloud(self):
        fake = FakeJira(cloud=True)
        for n in range(1, 151):
            fake.add(f"DEMO-{n}")
        found = client_for(fake).search(DEFAULT_JQL)
        self.assertEqual(len(found), 150)
        paths = [urllib.parse.urlsplit(u).path for _m, u, _h, _d in fake.calls]
        self.assertEqual(paths, ["/rest/api/2/search/jql"] * 2)
        self.assertIn("nextPageToken=100", fake.calls[1][1])

    def test_light_search_narrows_by_updated_and_keeps_the_order(self):
        fake = FakeJira(cloud=False)
        client_for(fake).search("project = DEMO ORDER BY updated DESC", since=time.time() - 270)  # 4.5 min + the 10 min margin → 15
        jql = urllib.parse.parse_qs(urllib.parse.urlsplit(fake.calls[0][1]).query)["jql"][0]
        self.assertEqual(jql, '(project = DEMO) AND updated >= "-15m" ORDER BY updated DESC')

    def test_transition_by_name_or_target_status(self):
        fake = FakeJira(cloud=False)
        client = client_for(fake)
        self.assertEqual(client.transitions("DEMO-1"), [("31", "Pass", "Done"), ("41", "Fail", "Test failed")])
        self.assertEqual(transition_by_name(client, "DEMO-1", "pass"), "31")
        self.assertEqual(transition_by_name(client, "DEMO-1", "Test failed"), "41")
        self.assertEqual(fake.posted, [("DEMO-1", {"transition": {"id": "31"}}), ("DEMO-1", {"transition": {"id": "41"}})])
        post = [c for c in fake.calls if c[0] == "POST"][0]
        self.assertEqual(post[2]["Content-Type"], "application/json")

    def test_missing_transition_names_what_is_open(self):
        fake = FakeJira(cloud=True)
        with self.assertRaises(JiraError) as ctx:
            transition_by_name(client_for(fake), "DEMO-1", "Closed")
        self.assertIn("Closed", str(ctx.exception))
        self.assertIn("Pass, Fail", str(ctx.exception))
        self.assertEqual(fake.posted, [])


class SnapshotTest(unittest.TestCase):
    def both(self):
        return (FakeJira(cloud=True), FakeJira(cloud=False))

    def test_snapshot_facts(self):
        for fake in self.both():
            with self.subTest(cloud=fake.cloud):
                fake.add("DEMO-1", status="Ready for test", assignee=fake.other, summary="Login fails")
                fake.comment("DEMO-1", fake.other)
                snap, _ = run(fake, {})
                facts = snap["DEMO-1"]
                self.assertEqual(facts["summary"], "Login fails")
                self.assertEqual(facts["status"], "Ready for test")
                self.assertEqual(facts["comments"], 1)
                self.assertEqual(facts["last_comment_by"], "acc-ann" if fake.cloud else "JIRAUSER2")
                self.assertEqual(facts["url"], (CLOUD if fake.cloud else DC) + "/browse/DEMO-1")
                self.assertTrue(facts["mine"])  # I am the reporter

    def test_first_check_is_the_baseline(self):
        for fake in self.both():
            with self.subTest(cloud=fake.cloud):
                fake.add("DEMO-1", status="Ready for test", assignee=fake.me, itype="Bug")
                fake.comment("DEMO-1", fake.other)
                result = check(CLOUD if fake.cloud else DC, "tok", "me@example.com", JiraSettings(), {}, fetch=fake)
                self.assertEqual(result.events, [])
                self.assertIn("DEMO-1", result.snapshot)
                self.assertTrue(result.full)
                self.assertEqual(result.username, "Me Myself" if fake.cloud else "me")

    def test_status_moved_into_a_watched_status(self):
        for fake in self.both():
            with self.subTest(cloud=fake.cloud):
                fake.add("DEMO-1")
                old, _ = run(fake, {})
                fake.move("DEMO-1", "Ready for test")
                _new, events = run(fake, old)
                self.assertEqual([(e.kind, e.mr, e.args["status"]) for e in events], [("status", "DEMO-1", "Ready for test")])
                self.assertEqual(events[0].text, "{key} moved to {status}")

    def test_my_own_move_raises_nothing(self):
        fake = FakeJira(cloud=False)
        fake.add("DEMO-1")
        old, _ = run(fake, {})
        fake.move("DEMO-1", "Ready for test", by=fake.me)
        self.assertEqual(run(fake, old)[1], [])

    def test_unwatched_status_raises_nothing(self):
        fake = FakeJira(cloud=True)
        fake.add("DEMO-1")
        old, _ = run(fake, {})
        fake.move("DEMO-1", "In review")
        self.assertEqual(run(fake, old)[1], [])
        settings = JiraSettings(statuses=["in review"])
        fake.move("DEMO-1", "Coding")
        old, _ = run(fake, {}, settings)
        fake.move("DEMO-1", "In Review")
        self.assertEqual([e.kind for e in run(fake, old, settings)[1]], ["status"])

    def test_bug_assigned_to_me(self):
        for fake in self.both():
            with self.subTest(cloud=fake.cloud):
                fake.add("DEMO-1", itype="Bug", assignee=fake.other)
                fake.add("DEMO-2", itype="Task", assignee=fake.other)
                old, _ = run(fake, {})
                fake.assign("DEMO-1", fake.me)
                fake.assign("DEMO-2", fake.me)
                _new, events = run(fake, old)
                self.assertEqual([(e.kind, e.mr) for e in events], [("assigned_bug", "DEMO-1")])

    def test_new_bug_assigned_to_me_on_a_light_check(self):
        fake = FakeJira(cloud=True)
        fake.add("DEMO-1")
        last = time.time() - 120
        old, _ = run(fake, {})
        fake.add("DEMO-2", itype="Bug", assignee=None, reporter=fake.other)
        fake.assign("DEMO-2", fake.me)
        new, events = run(fake, old, since=last)
        self.assertEqual([(e.kind, e.mr) for e in events], [("assigned_bug", "DEMO-2")])
        self.assertIn("DEMO-1", new)  # a light check keeps what it did not read again

    def test_full_check_skips_an_old_assignment_new_to_the_snapshot(self):
        for fake in self.both():
            with self.subTest(cloud=fake.cloud):
                url = CLOUD if fake.cloud else DC
                fake.add("DEMO-1")
                last = time.time() - 120
                old, _ = run(fake, {})
                # a bug assigned to me a month ago drifts into the filter (an edited JQL)
                long_ago = stamp(time.time() - 30 * 86400)
                bug = fake.add("DEMO-2", itype="Bug", assignee=None, reporter=fake.other)
                fake.assign("DEMO-2", fake.me)
                bug["fields"]["created"] = bug["changelog"]["histories"][-1]["created"] = long_ago
                result = check(url, "tok", "me@example.com", JiraSettings(), old, fetch=fake, previous=last)
                self.assertTrue(result.full)
                self.assertEqual(result.events, [])
                self.assertIn("DEMO-2", result.snapshot)
                # a fresh assignment of another bug still raises the event on a full check
                fake.add("DEMO-3", itype="Bug", assignee=None, reporter=fake.other)
                fake.assign("DEMO-3", fake.me)
                result = check(url, "tok", "me@example.com", JiraSettings(), result.snapshot, fetch=fake, previous=last)
                self.assertEqual([(e.kind, e.mr) for e in result.events], [("assigned_bug", "DEMO-3")])

    def test_bug_created_already_assigned_to_me(self):
        fake = FakeJira(cloud=False)
        fake.add("DEMO-1")
        last = time.time() - 120
        old, _ = run(fake, {})
        fake.add("DEMO-2", itype="Bug", assignee=fake.me, reporter=fake.other)  # no changelog entry
        old_bug = fake.add("DEMO-3", itype="Bug", assignee=fake.me, reporter=fake.other)
        old_bug["fields"]["created"] = stamp(time.time() - 30 * 86400)
        new, events = run(fake, old, since=last)
        self.assertEqual([(e.kind, e.mr) for e in events], [("assigned_bug", "DEMO-2")])
        self.assertGreater(new["DEMO-2"]["assignee_at"], last)

    def test_new_comment_by_someone_else_in_my_issue(self):
        for fake in self.both():
            with self.subTest(cloud=fake.cloud):
                fake.add("DEMO-1", assignee=fake.me, reporter=fake.other)
                fake.add("DEMO-2", assignee=fake.other, reporter=fake.other)  # watched, not mine
                old, _ = run(fake, {})
                fake.comment("DEMO-1", fake.other)
                fake.comment("DEMO-2", fake.other)
                _new, events = run(fake, old)
                self.assertEqual([(e.kind, e.mr, e.args["user"]) for e in events], [("commented", "DEMO-1", "Ann" if fake.cloud else "ann")])
                old = _new
                fake.comment("DEMO-1", fake.me)
                self.assertEqual(run(fake, old)[1], [])

    def test_my_issue_sent_back_to_work(self):
        settings = JiraSettings(rules={**{k: on for k, (_l, on) in RULES.items()}, "reopened": True})
        for fake in self.both():
            with self.subTest(cloud=fake.cloud):
                fake.add("DEMO-1", status="Ready for test", assignee=fake.me)
                fake.add("DEMO-2", status="Done", category="done", assignee=fake.other)
                fake.add("DEMO-3", status="Ready for test", assignee=fake.other, reporter=fake.other)  # not mine
                old, _ = run(fake, {}, settings)
                fake.move("DEMO-1", "In Progress")
                fake.move("DEMO-2", "Reopened")
                fake.move("DEMO-3", "In Progress")
                _new, events = run(fake, old, settings)
                self.assertEqual(sorted((e.kind, e.mr) for e in events), [("reopened", "DEMO-1"), ("reopened", "DEMO-2")])

    def test_reopened_is_off_by_default_and_rules_switch_off(self):
        fake = FakeJira(cloud=False)
        fake.add("DEMO-1", status="Ready for test", assignee=fake.me)
        old, _ = run(fake, {})
        fake.move("DEMO-1", "In Progress")
        self.assertEqual(run(fake, old)[1], [])
        off = JiraSettings(rules={k: False for k in RULES})
        fake.move("DEMO-1", "Ready for test")
        self.assertEqual(run(fake, old, off)[1], [])

    def test_events_are_marked_as_jira_when_the_feed_knows_sources(self):
        fake = FakeJira(cloud=True)
        fake.add("DEMO-1")
        old, _ = run(fake, {})
        fake.move("DEMO-1", "Ready for test")
        event = run(fake, old)[1][0]
        self.assertIsInstance(event, gitlab.Event)
        if hasattr(event, "source"):
            self.assertEqual(event.source, "jira")
        self.assertEqual(gitlab.Event.from_dict(event.to_dict()).mr, "DEMO-1")


class SettingsTest(unittest.TestCase):
    def test_defaults(self):
        s = JiraSettings()
        self.assertEqual(s.statuses, ["Ready for test"])
        self.assertEqual(s.transitions, {})  # nothing is ever sent unless chosen
        self.assertEqual(s.query(), DEFAULT_JQL)
        self.assertFalse(s.rule("reopened"))
        self.assertTrue(s.rule("status"))

    def test_round_trip(self):
        s = JiraSettings(url=DC, entry_id="e1", interval_min=10, statuses=["Ready for test", "In review"], jql="project = DEMO",
                         transitions={"pass": "Done", "fail": "Test failed"}, enabled=True, username="me")
        self.assertEqual(JiraSettings.from_dict(s.to_dict()), s)

    def test_garbage_is_tolerated(self):
        for junk in (None, [], "x", 5):
            self.assertEqual(JiraSettings.from_dict(junk), JiraSettings())
        s = JiraSettings.from_dict({
            "url": " jira.example.com/ ", "interval_min": "5", "rules": ["status"], "statuses": [" A ", 3, "a", ""],
            "jql": 7, "transitions": {"pass": " Done ", "fail": "", "other": "X", "delete": 1}, "enabled": 1,
        })
        self.assertEqual(s.url, "https://jira.example.com")
        self.assertEqual(s.interval_min, 5)
        self.assertEqual(s.rules, JiraSettings().rules)
        self.assertEqual(s.statuses, ["A"])
        self.assertEqual(s.jql, "")
        self.assertEqual(s.transitions, {"pass": "Done"})
        self.assertTrue(s.enabled)
        self.assertEqual(JiraSettings.from_dict({"statuses": "Ready for test, In review"}).statuses, ["Ready for test", "In review"])
        self.assertEqual(JiraSettings.from_dict({"statuses": []}).statuses, [])
        self.assertEqual(JiraSettings.from_dict({"interval_min": 7}).interval_min, 5)

    def test_parse_statuses(self):
        self.assertEqual(parse_statuses(" Ready for test ,, ready FOR test, Done "), ["Ready for test", "Done"])


if __name__ == "__main__":
    unittest.main()
