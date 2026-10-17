"""Watched branches: the newest pipeline and new commits from a fake API, the events between looks."""
import json
import unittest
import urllib.parse

from daykeeper.gitlab import RULES, WATCH_RULES, Event, GitLabSettings, PipelineWatch, check as gitlab_check, project_path
from daykeeper.integrations import Integrations

ME = {"id": 7, "username": "me", "name": "Me Myself", "email": "me@example.com"}


class FakeBranches:
    """/user, an empty MR list, and per project the pipelines and the commits of a branch."""

    def __init__(self):
        self.pipelines = {}  # (project, ref) → [pipeline dicts], newest first
        self.commits = {}  # (project, ref) → [commit dicts], newest first
        self.calls = []

    def pipe(self, project, ref, pid, status, source="schedule"):
        self.pipelines.setdefault((project, ref), []).insert(0, {
            "id": pid, "status": status, "ref": ref, "source": source, "sha": f"s{pid}",
            "web_url": f"https://gitlab.example.com/{project}/-/pipelines/{pid}", "finished_at": "2026-10-18T03:12:00Z",
        })

    def commit(self, project, ref, cid, author="Ann", email="ann@example.com", at="2026-10-18T10:00:00Z"):
        self.commits.setdefault((project, ref), []).insert(0, {
            "id": cid, "title": f"Change {cid}", "author_name": author, "author_email": email, "committed_date": at,
            "web_url": f"https://gitlab.example.com/{project}/-/commit/{cid}",
        })

    def __call__(self, url, headers):
        self.calls.append(url)
        parsed = urllib.parse.urlsplit(url)
        path = parsed.path.removeprefix("/api/v4")
        q = dict(urllib.parse.parse_qsl(parsed.query))
        if path == "/user":
            return 200, json.dumps(ME).encode()
        if path == "/merge_requests":
            return 200, b"[]"
        parts = path.strip("/").split("/")  # projects/<encoded path>/pipelines | repository/commits
        project = urllib.parse.unquote(parts[1])
        if parts[2:] == ["pipelines"]:
            if (project, q["ref"]) not in self.pipelines:
                return 404, b"{}"
            out = [p for p in self.pipelines[(project, q["ref"])] if not q.get("source") or p["source"] == q["source"]]
            return 200, json.dumps(out[: int(q.get("per_page", 20))]).encode()
        if parts[2:] == ["repository", "commits"]:
            out = [c for c in self.commits.get((project, q["ref_name"]), []) if not q.get("since") or c["committed_date"] >= q["since"]]
            return 200, json.dumps(out).encode()
        return 404, b"{}"


def settings(*watches, **rules):
    s = GitLabSettings(pipelines=list(watches))
    s.rules.update(rules)
    return s


class PipelineWatchTest(unittest.TestCase):
    def setUp(self):
        self.gl = FakeBranches()
        self.watch = PipelineWatch("group/repo", "main", "schedule", "Nightly")
        self.settings = settings(self.watch, watch_fixed=True)
        self.state = {}

    def look(self, s=None):
        result = gitlab_check("https://gitlab.example.com", "tok", s or self.settings, {}, fetch=self.gl, pipelines=self.state)
        self.state = result.pipelines
        return result.events

    def test_a_failure_is_news_once(self):
        self.gl.pipe("group/repo", "main", 1, "success")
        self.assertEqual(self.look(), [], "the first look is the baseline")
        self.gl.pipe("group/repo", "main", 2, "failed")
        events = self.look()
        self.assertEqual([(e.kind, e.mr, e.title, e.source) for e in events], [("watch_failed", "group/repo@main", "Nightly", "gitlab")])
        self.assertEqual(events[0].url, "https://gitlab.example.com/group/repo/-/pipelines/2")
        self.assertEqual(events[0].args["project"], "group/repo")
        self.assertEqual(self.look(), [], "the same failed pipeline again stays silent")
        self.gl.pipe("group/repo", "main", 3, "failed")
        self.assertEqual([e.kind for e in self.look()], ["watch_failed"], "the next night red again is news again")

    def test_the_list_has_no_finished_at_so_updated_at_stands_in(self):
        self.gl.pipe("group/repo", "main", 1, "failed")
        row = self.gl.pipelines[("group/repo", "main")][0]
        del row["finished_at"]
        row["updated_at"] = "2026-10-18T04:00:00Z"
        self.look()
        self.assertEqual(self.state[self.watch.key()]["finished_at"], "2026-10-18T04:00:00Z")

    def test_green_again_skips_a_running_pipeline(self):
        self.gl.pipe("group/repo", "main", 1, "failed")
        self.look()
        self.gl.pipe("group/repo", "main", 2, "running")
        self.assertEqual(self.look(), [])
        self.assertEqual(self.state[self.watch.key()]["id"], 1, "a running pipeline has no outcome yet")
        self.gl.pipe("group/repo", "main", 3, "success")
        self.assertEqual([e.kind for e in self.look()], ["watch_fixed"])
        self.assertEqual(self.look(), [])

    def test_green_again_is_off_by_default(self):
        self.assertEqual({k: on for k, (_l, on) in WATCH_RULES.items()}, {"watch_failed": True, "watch_fixed": False, "branch_commits": False})
        self.assertFalse(set(WATCH_RULES) & set(RULES), "the MR rules block of the settings lists RULES only")
        self.gl.pipe("group/repo", "main", 1, "failed")
        s = settings(self.watch)
        self.look(s)
        self.gl.pipe("group/repo", "main", 2, "success")
        self.assertEqual(self.look(s), [])

    def test_source_and_url_encoded_path(self):
        self.gl.pipe("group/repo", "main", 1, "failed", source="push")
        self.look()
        sent = [c for c in self.gl.calls if "/pipelines" in c]
        self.assertTrue(sent[0].startswith("https://gitlab.example.com/api/v4/projects/group%2Frepo/pipelines?"), sent[0])
        self.assertIn("source=schedule", sent[0])
        self.assertNotIn("status", self.state[self.watch.key()], "a push pipeline does not count for a schedule watch")
        self.assertEqual(project_path("group/sub/repo"), "group%2Fsub%2Frepo")

    def test_an_unknown_branch_spoils_only_its_row(self):
        self.gl.pipe("group/repo", "main", 1, "success")
        other = PipelineWatch("group/repo", "no-such-branch")
        s = settings(self.watch, other)
        self.assertEqual(self.look(s), [])
        self.assertIn("404", self.state[other.key()]["error"])
        self.assertEqual(self.state[self.watch.key()]["status"], "success")

    def test_the_rules_off_ask_nothing(self):
        self.gl.pipe("group/repo", "main", 1, "failed")
        self.look(settings(self.watch, watch_failed=False))
        self.assertFalse([c for c in self.gl.calls if "/projects/" in c])

    def test_without_the_old_state_the_branches_are_not_looked_at(self):
        result = gitlab_check("https://gitlab.example.com", "tok", self.settings, {}, fetch=self.gl)
        self.assertIsNone(result.pipelines)
        self.assertFalse([c for c in self.gl.calls if "/projects/" in c])


class BranchCommitsTest(unittest.TestCase):
    def setUp(self):
        self.gl = FakeBranches()
        self.watch = PipelineWatch("group/repo", "release/1.2")
        self.settings = settings(self.watch, watch_failed=False, branch_commits=True)
        self.state = {}
        self.gl.commit("group/repo", "release/1.2", "c1", at="2026-10-18T09:00:00Z")

    def look(self):
        result = gitlab_check("https://gitlab.example.com", "tok", self.settings, {}, fetch=self.gl, pipelines=self.state)
        self.state = result.pipelines
        return result.events

    def test_commits_by_other_people_only(self):
        self.assertEqual(self.look(), [])
        self.gl.commit("group/repo", "release/1.2", "c2", author="Me Myself", email="ME@example.com", at="2026-10-18T10:00:00Z")
        self.assertEqual(self.look(), [], "my own commit is not news")
        self.gl.commit("group/repo", "release/1.2", "c3", author="Ann", at="2026-10-18T11:00:00Z")
        self.gl.commit("group/repo", "release/1.2", "c4", author="Bob", email="bob@example.com", at="2026-10-18T12:00:00Z")
        self.gl.commit("group/repo", "release/1.2", "c5", author="Me Myself", email="other@example.com", at="2026-10-18T13:00:00Z")
        events = self.look()
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual((ev.kind, ev.mr, ev.title, ev.args["n"], ev.args["author"]), ("branch_commits", "group/repo@release/1.2", "group/repo · release/1.2", 2, "Bob"))
        self.assertEqual(ev.url, "https://gitlab.example.com/group/repo/-/commit/c4")
        self.assertEqual(self.state[self.watch.key()]["head"], "c5")
        self.assertEqual(self.look(), [], "nothing new since")
        sent = [c for c in self.gl.calls if "/repository/commits" in c][-1]
        self.assertIn("/projects/group%2Frepo/repository/commits?", sent)
        self.assertIn("ref_name=release%2F1.2", sent)
        self.assertIn("since=2026-10-18T13", sent)


class CatalogTest(unittest.TestCase):
    def test_rule_labels_are_translated(self):
        from daykeeper.locale_ru import RU

        self.assertEqual([label for label, _on in WATCH_RULES.values() if label not in RU], [])


class StoreTest(unittest.TestCase):
    def test_watches_and_state_round_trip(self):
        integ = Integrations()
        integ.gitlab.pipelines = [PipelineWatch("group/repo", "main", "schedule", "Nightly"), PipelineWatch("group/repo", "dev")]
        integ.gitlab_pipelines = {"group/repo@main:schedule": {"status": "failed", "id": 3}}
        back = Integrations.from_dict(integ.to_dict())
        self.assertEqual(back.gitlab.pipelines, integ.gitlab.pipelines)
        self.assertEqual(back.gitlab_pipelines, integ.gitlab_pipelines)
        self.assertEqual(back.gitlab.pipelines[1].label(), "group/repo · dev")

    def test_old_files_load(self):
        back = Integrations.from_dict({"gitlab": {"url": "https://gl", "rules": {"merged": False}}, "feed": [{"kind": "merged", "mr": "a!1"}]})
        self.assertEqual((back.gitlab.pipelines, back.gitlab_pipelines), ([], {}))
        self.assertEqual(back.feed[0].source, "gitlab")
        self.assertEqual((back.gitlab.rule("merged"), back.gitlab.rule("watch_failed"), back.gitlab.rule("watch_fixed")), (False, True, False))

    def test_bad_rows_are_dropped(self):
        s = GitLabSettings.from_dict({"pipelines": [{"project": "group/repo"}, "x", {"project": "g/r", "ref": "main", "source": "web"}]})
        self.assertEqual(s.pipelines, [PipelineWatch("g/r", "main")])
        self.assertEqual(Integrations.from_dict({"gitlab_pipelines": [1]}).gitlab_pipelines, {})

    def test_a_check_without_pipelines_keeps_the_state(self):
        from daykeeper.gitlab import CheckResult

        integ = Integrations(gitlab_pipelines={"k": {"status": "failed"}})
        integ.apply_check(CheckResult("me", {}, []), 1.0)
        self.assertEqual(integ.gitlab_pipelines, {"k": {"status": "failed"}})
        integ.apply_check(CheckResult("me", {}, [], pipelines={}), 2.0)
        self.assertEqual(integ.gitlab_pipelines, {})

    def test_event_source_round_trip(self):
        ev = Event("issue", "KEY-1", "t", "https://x.example.com", "y", source="jira")
        self.assertEqual(Event.from_dict(ev.to_dict()).source, "jira")


if __name__ == "__main__":
    unittest.main()
