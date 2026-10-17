"""MR preview: a merge request as Markdown, and the client calls that bring it."""
import json
import unittest
import urllib.parse

from daykeeper import markdown
from daykeeper.gitlab import Event, GitLabClient, GitLabError, GitLabSettings, diff_snapshots
from daykeeper.mr_preview import diff_counts, mr_template_context, render_mr

URL = "https://gitlab.example.com/group/app/-/merge_requests/12"

MR = {
    "id": 900, "iid": 12, "project_id": 34, "title": "Add the export | import screen", "state": "opened", "draft": False,
    "web_url": URL, "description": "Adds a screen.\n\n- one\n- two", "source_branch": "feature/export", "target_branch": "main",
    "author": {"username": "kim", "name": "Kim Lee"}, "references": {"full": "group/app!12"},
    "head_pipeline": {"status": "success", "web_url": "https://gitlab.example.com/group/app/-/pipelines/5"},
}


def change(path, diff="", old=None, new=False, deleted=False, renamed=False, **extra):
    return {"old_path": old or path, "new_path": path, "new_file": new, "deleted_file": deleted, "renamed_file": renamed, "diff": diff, **extra}


EDIT = "@@ -1,3 +1,4 @@\n line one\n-line two\n+line 2\n+line 3\n line four\n"
CHANGES = {**MR, "changes": [
    change("src/export.py", EDIT),
    change("src/new_file.py", "@@ -0,0 +1,2 @@\n+a = 1\n+b = 2\n", new=True),
    change("src/old.py", "@@ -1,1 +0,0 @@\n-gone = True\n", deleted=True),
    change("src/renamed.py", "", old="src/before.py", renamed=True),
    change("assets/logo.png", "Binary files a/assets/logo.png and b/assets/logo.png differ\n"),
]}


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.text = render_mr(MR, CHANGES)

    def test_header(self):
        lines = self.text.splitlines()
        self.assertTrue(lines[0].startswith("# Add the export"))
        self.assertIn(f"[group/app!12]({URL})", self.text)
        self.assertIn("Kim Lee (@kim)", self.text)
        self.assertIn("`feature/export` → `main`", self.text)
        self.assertIn("[success](https://gitlab.example.com/group/app/-/pipelines/5)", self.text)
        self.assertIn("Adds a screen.", self.text)

    def test_files_table(self):
        rows = {r.split("|")[1].strip(): [c.strip() for c in r.split("|")[2:5]] for r in self.text.splitlines() if r.startswith("| ") and "---" not in r}
        self.assertEqual(rows["src/export.py"], ["changed", "2", "1"])
        self.assertEqual(rows["src/new_file.py"], ["new", "2", "0"])
        self.assertEqual(rows["src/old.py"], ["deleted", "0", "1"])
        self.assertEqual(rows["src/renamed.py"], ["renamed from src/before.py", "0", "0"])
        self.assertEqual(rows["assets/logo.png"], ["changed, binary", "0", "0"])
        self.assertIn("## Files (5, +4 −2)", self.text)

    def test_diffs_and_binary(self):
        self.assertIn("### `src/export.py`\n\n```diff\n@@ -1,3 +1,4 @@", self.text)
        self.assertIn("### `assets/logo.png`\n\n_Binary file, not shown._", self.text)
        self.assertNotIn("Binary files a/", self.text.split("## Files")[1].split("### `assets/logo.png`")[1])
        self.assertNotIn("rest in the browser", self.text)

    def test_the_viewer_reads_it(self):
        blocks = markdown.parse(self.text)
        tables = [b for b in blocks if b.kind == "table"]
        self.assertEqual(len(tables), 1)
        self.assertTrue(all(len(line.split(" │ ")) == 4 for line in markdown.table_lines(tables[0].raw) if "─" not in line))
        self.assertEqual(sum(1 for b in blocks if b.kind == "code"), 3)

    def test_mr_fields_may_come_from_changes_only(self):
        text = render_mr({}, CHANGES)
        self.assertIn("Kim Lee (@kim)", text)
        self.assertIn("src/export.py", text)


class TruncationTest(unittest.TestCase):
    def test_whole_files_while_they_fit(self):
        big = "@@ -1,1 +1,200 @@\n" + "".join(f"+line {i}\n" for i in range(200))
        changes = {"changes": [change("a.py", EDIT), change("b.py", big), change("c.py", EDIT)]}
        text = render_mr(MR, changes, limit_bytes=len(EDIT) + 100)
        self.assertIn("First 1 of 3 files shown, the rest in the browser.", text)
        self.assertIn("### `a.py`", text)
        self.assertNotIn("### `b.py`", text)
        self.assertNotIn("### `c.py`", text)
        self.assertIn("| c.py |", text, "the table still lists every file")
        self.assertIn(f"[Open]({URL})", text)

    def test_one_huge_file_shows_its_head(self):
        big = "@@ -1,1 +1,200 @@\n" + "".join(f"+line {i}\n" for i in range(200))
        text = render_mr(MR, {"changes": [change("b.py", big), change("c.py", EDIT)]}, limit_bytes=200)
        self.assertIn("### `b.py`", text)
        self.assertIn("The diff is cut here.", text)
        self.assertIn("First 1 of 2 files shown", text)
        self.assertNotIn("+line 199", text)
        self.assertLess(len(text.split("```diff")[1].split("```")[0].encode()), 220)

    def test_server_overflow_and_too_large(self):
        text = render_mr(MR, {"overflow": True, "changes": [change("gen.json", "", too_large=True)]})
        self.assertIn("GitLab sent only part of the diffs", text)
        self.assertIn("too large for GitLab to send", text)


class EscapingTest(unittest.TestCase):
    def test_a_pipe_in_a_path_or_title_does_not_break_the_table(self):
        text = render_mr({**MR, "title": "Line\nbreak"}, {"changes": [change("docs/a|b.md", EDIT, old="docs/x|y.md", renamed=True)]})
        self.assertTrue(text.startswith("# Line break\n"))
        row = next(r for r in text.splitlines() if r.startswith("| docs"))
        self.assertEqual(row.count("|"), 5)
        self.assertIn("docs/a¦b.md", row)
        self.assertIn("renamed from docs/x¦y.md", row)

    def test_backticks_in_a_path_and_fences_in_a_diff(self):
        diff = "@@ -1,2 +1,2 @@\n ```\n-old\n+new\n ```\n"
        text = render_mr(MR, {"changes": [change("we`ird.md", diff)]})
        self.assertIn("### ``we`ird.md``", text)
        code = [b for b in markdown.parse(text) if b.kind == "code"]
        self.assertEqual(len(code), 1, "a ``` line inside the diff must not close the block")
        self.assertEqual(code[0].raw.replace("\u200b", ""), diff.rstrip("\n"), "the whole diff, as sent, in one block")

    def test_a_diff_with_both_fence_kinds_stays_in_one_block(self):
        diff = "@@ -1,4 +1,4 @@\n ```\n ~~~\n-old\n+new\n````\n   ~~~ more\n"
        text = render_mr(MR, {"changes": [change("a.md", diff), change("b.py", EDIT)]})
        blocks = markdown.parse(text)
        code = [b for b in blocks if b.kind == "code"]
        self.assertEqual(len(code), 2)
        self.assertEqual(code[0].raw.replace("\u200b", ""), diff.rstrip("\n"))
        self.assertEqual(code[1].raw, EDIT.rstrip("\n"))
        self.assertIn("b.py", [b.text for b in blocks if b.kind == "heading"], "the next file's heading is still there")

    def test_a_lone_carriage_return_in_a_diff_does_not_close_the_block(self):
        text = render_mr(MR, {"changes": [change("a.md", "@@ -1 +1 @@\n+x\r```\n"), change("b.py", EDIT)]})
        blocks = markdown.parse(text)
        self.assertEqual(sum(1 for b in blocks if b.kind == "code"), 2)
        self.assertIn("b.py", [b.text for b in blocks if b.kind == "heading"])

    def test_a_fence_left_open_in_the_description_is_closed_with_its_own_marker(self):
        for description in ("See:\n~~~\ncode\n```\nmore", "~~~\na\n~~~\n```py\nb", "```\na\n```\n  ~~~\nb", "x\r~~~\ny"):
            with self.subTest(description=description):
                headings = [b.text for b in markdown.parse(render_mr({**MR, "description": description}, CHANGES)) if b.kind == "heading"]
                self.assertIn("Files (5, +4 −2)", headings)
                self.assertIn("src/export.py", headings)

    def test_an_unclosed_fence_in_the_description_is_closed(self):
        text = render_mr({**MR, "description": "See:\n```\ncode"}, CHANGES)
        self.assertIn("src/export.py", [b.text for b in markdown.parse(text) if b.kind == "heading"])

    def test_no_local_links_from_the_server(self):
        traps = ["[x](../settings.json)", "[x](file:///C:/x)", "![i](c:\\x.png)", "[x](HTTP://up.case)",
                 '[a](https://ok.example "[b](../in-title.json)")', "[[a](../one)](../two)"]
        for trap in traps:
            mr = {**MR, "title": f"Title {trap}", "author": {"name": f"Kim {trap}", "username": f"kim{trap}"},
                  "description": f"Text {trap}\n\n- item {trap}\n> {trap}\n\n**{trap}**\nsplit [over\nlines](../x.json)"}
            with self.subTest(trap=trap):
                text = render_mr(mr, CHANGES)
                for block in markdown.parse(text):
                    for span in block.spans:
                        if "link" in span.styles:
                            self.assertTrue(span.href.startswith(("http://", "https://")), (span.href, block.text))
                for gone in ("settings.json", "file:", "c:\\x.png", "../", "HTTP:"):
                    self.assertNotIn(gone, text)

    def test_visible_text_stays_and_web_links_keep_working(self):
        mr = {**MR, "title": "Fix [the config](../settings.json)", "description": "See ![shot](c:/x.png) and [docs](https://docs.example/a)."}
        text = render_mr(mr, CHANGES)
        self.assertTrue(text.startswith("# Fix the config\n"))
        self.assertIn("See shot and [docs](https://docs.example/a).", text)
        links = [s.href for b in markdown.parse(text) for s in b.spans if "link" in s.styles]
        self.assertIn("https://docs.example/a", links)
        self.assertIn(URL, links)

    def test_server_text_cannot_form_a_link_with_the_markup_around_it(self):
        mr = {**MR, "references": {"full": "x](../settings.json!12"}, "author": {"name": "[a", "username": "b](../settings.json"},
              "state": "[s](../settings.json)", "head_pipeline": {"status": "ok](../p", "web_url": "https://gitlab.example.com/p/1"}}
        text = render_mr(mr, CHANGES)
        hrefs = [s.href for b in markdown.parse(text) for s in b.spans if "link" in s.styles]
        self.assertTrue(all(h.startswith(("http://", "https://")) for h in hrefs), hrefs)
        self.assertNotIn("](../", text)

    def test_only_web_links(self):
        text = render_mr({**MR, "web_url": "javascript:alert(1)", "head_pipeline": {"status": "failed", "web_url": "file:///x"}}, {"changes": []})
        self.assertNotIn("javascript:", text)
        self.assertNotIn("file:", text)
        self.assertIn("**Pipeline:** failed", text)


class CountsTest(unittest.TestCase):
    def test_header_lines_are_not_counted_but_dashes_in_hunks_are(self):
        diff = "--- a/x\n+++ b/x\n@@ -1,2 +1,1 @@\n--- a removed rule\n keep\n"
        self.assertEqual(diff_counts(diff), (0, 1))
        self.assertEqual(diff_counts(EDIT), (2, 1))
        self.assertEqual(diff_counts(""), (0, 0))


class ContextTest(unittest.TestCase):
    def test_from_the_mr(self):
        self.assertEqual(mr_template_context(MR), {"mr_url": URL, "mr_title": "Add the export | import screen", "mr_project": "group/app", "mr_iid": "12"})

    def test_from_the_link_alone(self):
        ctx = mr_template_context({"web_url": "https://gitlab.example.com/group/sub/app/-/merge_requests/7", "title": "T"})
        self.assertEqual((ctx["mr_project"], ctx["mr_iid"]), ("group/sub/app", "7"))

    def test_from_nothing(self):
        self.assertEqual(mr_template_context(None), {"mr_url": "", "mr_title": "", "mr_project": "", "mr_iid": ""})  # type: ignore[arg-type]


class FakeFetch:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        path = urllib.parse.urlsplit(url).path.replace("/api/v4", "")
        if path in self.answers:
            return 200, json.dumps(self.answers[path]).encode()
        return 404, b"{}"


class ClientTest(unittest.TestCase):
    def test_mr_and_changes_by_id_or_path(self):
        fetch = FakeFetch({"/projects/34/merge_requests/12": MR, "/projects/group%2Fapp/merge_requests/12/changes": CHANGES})
        client = GitLabClient("https://gitlab.example.com", "tok", fetch=fetch)
        self.assertEqual(client.mr(34, 12)["title"], MR["title"])
        self.assertEqual(len(client.mr_changes("group/app", 12)["changes"]), 5)
        self.assertEqual(fetch.calls[0][1]["PRIVATE-TOKEN"], "tok")
        self.assertTrue(fetch.calls[1][0].startswith("https://gitlab.example.com/api/v4/projects/group%2Fapp/"))

    def test_errors(self):
        client = GitLabClient("https://gitlab.example.com", "tok", fetch=FakeFetch({"/projects/34/merge_requests/1": [1]}))
        with self.assertRaises(GitLabError):
            client.mr(34, 99)  # 404
        with self.assertRaises(GitLabError):
            client.mr(34, 1)  # not a dict
        with self.assertRaises(GitLabError):
            client.mr_changes("", 1)

    def test_a_merge_request_number_that_is_no_number(self):
        fetch = FakeFetch({"/projects/34/merge_requests/12": MR})
        client = GitLabClient("https://gitlab.example.com", "tok", fetch=fetch)
        for iid in ("abc", "", "12a", "0", "-3", None):
            with self.subTest(iid=iid):
                with self.assertRaises(GitLabError) as caught:
                    client.mr(34, iid)  # type: ignore[arg-type]
                self.assertIn("Not a merge request number", str(caught.exception))
                with self.assertRaises(GitLabError):
                    client.mr_changes(34, iid)  # type: ignore[arg-type]
        self.assertEqual(fetch.calls, [])
        self.assertEqual(client.mr(34, " 12 ")["iid"], 12)  # type: ignore[arg-type]

    def test_plain_http_never_gets_the_token(self):
        fetch = FakeFetch({"/projects/34/merge_requests/12": MR})
        with self.assertRaises(GitLabError):
            GitLabClient("http://gitlab.example.com", "tok", fetch=fetch).mr(34, 12)
        self.assertEqual(fetch.calls, [])


class EventFieldsTest(unittest.TestCase):
    def test_project_id_and_role_on_mr_events(self):
        old = {"group/app!12": {"role": "author", "title": "T", "url": URL, "project_id": 34, "iid": 12, "approved_by": []}}
        new = {"group/app!12": {**old["group/app!12"], "approved_by": ["ann"]},
               "group/app!13": {"role": "reviewer", "reviewer": True, "title": "U", "url": URL, "project_id": 34, "iid": 13, "author": "ann"}}
        events = {e.kind: e for e in diff_snapshots(old, new, GitLabSettings())}
        self.assertEqual((events["approved"].project_id, events["approved"].role), (34, "author"))
        self.assertEqual((events["review_requested"].project_id, events["review_requested"].role), (34, "reviewer"))
        self.assertEqual(events["approved"].args, {"user": "ann", "mr": "group/app!12"}, "args stay the text's own")

    def test_old_records_still_load_and_new_ones_round_trip(self):
        old = Event.from_dict({"kind": "merged", "mr": "group/app!1", "title": "t", "url": URL, "text": "x"})
        self.assertEqual((old.project_id, old.role), (0, ""))
        self.assertNotIn("project_id", old.to_dict())
        ev = Event("approved", "group/app!12", "t", URL, "x", project_id=34, role="author")
        back = Event.from_dict(ev.to_dict())
        self.assertEqual((back.project_id, back.role), (34, "author"))
        self.assertEqual(Event.from_dict({"project_id": "x", "role": "boss"}).role, "")


if __name__ == "__main__":
    unittest.main()
