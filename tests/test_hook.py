"""Unit tests of the hook rules (daykeeper/hookrules.py) and the hook runner (daykeeper/cli.py)."""

import json
import tempfile
import unittest
from pathlib import Path

from daykeeper import cli, hookrules
from daykeeper.hookrules import HookConfig, Rule
from daykeeper.journal import read_inbox
from daykeeper.locale_ru import RU
from daykeeper.settings import Links

LINKS = Links(jira_url="https://tracker.example.com", gitlab_url="https://gitlab.example.com", gitlab_project="team/testing")


class hook:  # noqa: N801 — the shape the cases below were written against
    @staticmethod
    def classify(payload, own_mr=None, config=None):
        return hookrules.classify(payload, LINKS, own_mr, config)

    @staticmethod
    def _mr_project_and_number(item):
        return cli.OwnMr(LINKS, Path("unused")).project_and_number(item)


MR = "https://gitlab.example.com/team/testing/-/merge_requests/"
OTHER_MR = "https://gitlab.example.com/team/product/-/merge_requests/17"


def bash(command, response=""):
    return {"tool_name": "Bash", "tool_input": {"command": command}, "tool_response": {"stdout": response, "stderr": ""}}


class HookClassifyTest(unittest.TestCase):
    def test_mr_create(self):
        got = hook.classify(bash("cd E:/repos/wt && glab mr create --title 'x' --fill", f"Creating merge request...\n{MR}45\n"))
        self.assertEqual(got, [("mr-open", "!45")])

    def test_mr_create_other_project_keeps_url(self):
        got = hook.classify(bash("glab mr create -R team/product --fill", OTHER_MR))
        self.assertEqual(got, [("mr-open", OTHER_MR)])

    def test_mr_merge_and_approve(self):
        self.assertEqual(hook.classify(bash("glab mr merge 44", "✓ Merged")), [("mr-merged", "!44")])
        self.assertEqual(hook.classify(bash("glab mr approve !42", "✓ Approved")), [("review", "!42")])
        self.assertEqual(hook.classify(bash('glab api -X POST "projects/5/merge_requests/42/discussions" -f body=@note.md', '{"id":"abc"}')), [("review", "!42")])

    def test_reply_in_thread_and_own_mr_are_not_review(self):
        reply = 'glab api -X POST "projects/5/merge_requests/42/discussions/abc123/notes" -f body="fixed"'
        self.assertEqual(hook.classify(bash(reply, '{"id":1}')), [])
        resolve = 'glab api -X PUT "projects/5/merge_requests/42/discussions/abc123?resolved=true"'
        self.assertEqual(hook.classify(bash(resolve, '{"id":1}')), [])
        new_thread = 'glab api -X POST "projects/5/merge_requests/42/discussions" -f body=@note.md'
        self.assertEqual(hook.classify(bash(new_thread, "{}"), own_mr=lambda item: True), [])  # own MR — not a review
        self.assertEqual(hook.classify(bash(new_thread, "{}"), own_mr=lambda item: False), [("review", "!42")])
        self.assertEqual(hook.classify(bash(new_thread, "{}"), own_mr=lambda item: None), [("review", "!42")])  # GitLab did not answer — as before
        self.assertEqual(hook.classify(bash("glab mr create --fill", f"{MR}45"), own_mr=lambda item: True), [("mr-open", "!45")])  # opening an own MR — stays

    def test_verdict_comment_in_russian(self):
        head, fail = RU["tested on"].capitalize(), RU["needs rework"]
        cmd = f'curl -X POST ".../rest/api/3/issue/APP-7/comment" -d \'{{"body": "{head} dev. {fail}."}}\''
        self.assertEqual(hook.classify(bash(cmd, '{"id":"3"}')), [("fail", "APP-7")])
        cmd = f'curl -X POST ".../rest/api/3/issue/APP-8/comment" -d \'{{"body": "{head} qa."}}\''
        self.assertEqual(hook.classify(bash(cmd, '{"id":"4"}')), [("pass", "APP-8")])

    def test_mr_project_and_number(self):
        self.assertEqual(hook._mr_project_and_number("!42"), ("team/testing", "42"))
        self.assertEqual(hook._mr_project_and_number(OTHER_MR), ("team/product", "17"))

    def test_mr_view_is_not_an_event(self):
        self.assertEqual(hook.classify(bash("glab mr view 44", "...")), [])
        self.assertEqual(hook.classify(bash("glab api projects/5/merge_requests/42/discussions", "[]")), [])
        self.assertEqual(hook.classify(bash("glab mr merge 44", "error: pipeline failed")), [])

    def test_jira_issue_created(self):
        cmd = 'curl -s -u "$JIRA_USER:$JIRA_TOKEN" -X POST "https://tracker.example.com/rest/api/3/issue" -H "Content-Type: application/json" -d @body.json'
        self.assertEqual(hook.classify(bash(cmd, '{"id":"10001","key":"APP-4127","self":"..."}')), [("bug", "APP-4127")])
        # GET of the issue — not an event
        self.assertEqual(hook.classify(bash('curl -s "https://tracker.example.com/rest/api/3/issue/APP-4127?fields=summary"', '{"key":"APP-4127"}')), [])

    def test_jira_verdict_comment_inline_and_file(self):
        cmd = 'curl -X POST ".../rest/api/3/issue/APP-2938/comment" -d \'{"body": "Tested on dev. Needs rework."}\''
        self.assertEqual(hook.classify(bash(cmd, '{"id":"1"}')), [("fail", "APP-2938")])
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "comment.json"
            body.write_text('{"body":{"type":"doc","content":[{"text":"Tested on dev qa. Works correctly."}]}}', encoding="utf-8")
            cmd = f'curl -X POST ".../rest/api/3/issue/APP-202/comment" --data-binary @{body}'
            self.assertEqual(hook.classify({**bash(cmd, '{"id":"2"}'), "cwd": tmp}), [("pass", "APP-202")])  # the file is in the command's folder
        # an ordinary comment without the verdict head — not an event
        self.assertEqual(hook.classify(bash('curl -X POST ".../rest/api/3/issue/APP-1/comment" -d \'{"body":"A question about the steps"}\'', "")), [])

    def test_jira_transition_test_failed(self):
        cmd = 'curl -X POST ".../rest/api/3/issue/APP-101/transitions" -d \'{"transition":{"id":"31"}}\'  # Test failed'
        self.assertEqual(hook.classify(bash(cmd, "")), [("fail", "APP-101")])
        self.assertEqual(hook.classify(bash('curl -X POST ".../rest/api/3/issue/APP-101/transitions" -d \'{"transition":{"id":"10"}}\'', "")), [])

    def test_mcp_atlassian(self):
        created = {"tool_name": "mcp__claude_ai_Atlassian_Rovo__createJiraIssue", "tool_input": {"summary": "x"}, "tool_response": {"key": "APP-303", "id": "1"}}
        self.assertEqual(hook.classify(created), [("bug", "APP-303")])
        comment = {"tool_name": "mcp__claude_ai_Atlassian_Rovo__addCommentToJiraIssue", "tool_input": {"issueIdOrKey": "APP-202", "commentBody": "Tested on dev. Works."}, "tool_response": {}}
        self.assertEqual(hook.classify(comment), [("pass", "APP-202")])

    def test_unrelated(self):
        self.assertEqual(hook.classify(bash("npm run typecheck", "ok")), [])
        self.assertEqual(hook.classify({"tool_name": "Read", "tool_input": {"file_path": "x"}, "tool_response": "..."}), [])


class GitHubPresetTest(unittest.TestCase):
    def test_pull_requests(self):
        url = "https://github.com/acme/tool/pull/12"
        self.assertEqual(hook.classify(bash("gh pr create --fill", url + "\n")), [("mr-open", url)])
        self.assertEqual(hook.classify(bash("gh pr merge 12 --squash", "✓ Merged")), [("mr-merged", "#12")])
        self.assertEqual(hook.classify(bash("gh pr review 12 -R acme/tool --approve", "✓ Approved")), [("review", url)])
        self.assertEqual(hook.classify(bash("gh pr view 12", url)), [])

    def test_report_shortens_a_pull_request(self):
        from daykeeper.journal import mr_short
        self.assertEqual(mr_short("https://github.com/acme/tool/pull/12"), "tool#12")


class PresetSwitchTest(unittest.TestCase):
    def test_a_switched_off_set_finds_nothing(self):
        off = HookConfig(presets={"gitlab": False})
        self.assertEqual(hook.classify(bash("glab mr merge 44", "✓ Merged"), config=off), [])
        self.assertEqual(hook.classify(bash("glab mr merge 44", "✓ Merged"), config=HookConfig()), [("mr-merged", "!44")])


class OwnRuleTest(unittest.TestCase):
    def test_fixed_text(self):
        rule = Rule(name="nightly", status="note", pattern="nightly.sh", take="text", text="nightly run")
        cfg = HookConfig(rules=[rule])
        self.assertEqual(hook.classify(bash("bash scripts/NIGHTLY.SH qa", "done"), config=cfg), [("note", "nightly run")])
        self.assertEqual(hook.classify(bash("ls", "nightly.sh"), config=cfg), [], "the answer is not the command")

    def test_key_mr_and_match(self):
        key = Rule(status="manual", pattern="write-cases", take="key")
        self.assertEqual(hook.classify(bash("python write-cases.py --issue APP-101", "ok"), config=HookConfig(rules=[key])), [("manual", "APP-101")])
        mr = Rule(status="review", pattern="my-review-tool", take="mr")
        self.assertEqual(hook.classify(bash("my-review-tool !42", ""), config=HookConfig(rules=[mr])), [("review", "!42")])
        grp = Rule(status="note", pattern=r"triage (\d+)", regex=True, take="match", where="any")
        self.assertEqual(hook.classify(bash("echo", "nightly triage 1042 done"), config=HookConfig(rules=[grp])), [("note", "1042")])

    def test_also_and_disabled(self):
        rule = Rule(status="note", pattern="deploy", take="text", text="deploy", also="success")
        self.assertEqual(hook.classify(bash("deploy qa", "failed"), config=HookConfig(rules=[rule])), [])
        self.assertEqual(hook.classify(bash("deploy qa", "SUCCESS"), config=HookConfig(rules=[rule])), [("note", "deploy")])
        rule.enabled = False
        self.assertEqual(hook.classify(bash("deploy qa", "SUCCESS"), config=HookConfig(rules=[rule])), [])

    def test_problems(self):
        self.assertTrue(Rule(pattern="").problem())
        self.assertTrue(Rule(pattern="(", regex=True).problem())
        self.assertTrue(Rule(pattern="x", take="text", text=" ").problem())
        self.assertEqual(Rule(pattern="x", take="key").problem(), "")

    def test_config_round_trip(self):
        cfg = HookConfig(presets={"github": False}, rules=[Rule(name="a", pattern="b", take="key")], only_folders=["E:/work"], skip_folders=["E:/work/private"])
        back = HookConfig.from_settings(json.loads(json.dumps(cfg.to_settings())))
        self.assertFalse(back.presets["github"])
        self.assertTrue(back.presets["gitlab"], "a set the file does not mention stays on")
        self.assertEqual(back.rules[0].pattern, "b")
        self.assertEqual((back.only_folders, back.skip_folders), (["E:/work"], ["E:/work/private"]))
        odd = Rule.from_dict({"status": " Meeting ", "where": "x", "take": "y", "unknown": 1})
        self.assertEqual((odd.status, odd.where, odd.take), ("meeting", "command", "text"), "the journal decides the word; the rest falls back")


class FolderTest(unittest.TestCase):
    def test_only_and_skip(self):
        cfg = HookConfig(only_folders=[r"E:\repos\work"], skip_folders=["E:/repos/work/private"])
        self.assertTrue(cfg.folder_allowed("e:/repos/work"))
        self.assertTrue(cfg.folder_allowed("/e/repos/work/app"), "Git Bash form of the path")
        self.assertFalse(cfg.folder_allowed("E:/repos/work-other"))
        self.assertFalse(cfg.folder_allowed("E:/repos/work/private/x"))
        self.assertTrue(HookConfig().folder_allowed("C:/anything"))


class RunHookTest(unittest.TestCase):
    def test_writes_the_inbox_and_the_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox, log = Path(tmp) / "inbox.jsonl", Path(tmp) / "log.jsonl"
            settings = {**LINKS.to_settings(), **HookConfig(skip_folders=[str(Path(tmp) / "private")]).to_settings()}
            payload = {**bash("glab mr merge 44", "✓ Merged"), "cwd": tmp}
            cli.run_hook(payload, settings, inbox, log)
            self.assertEqual([(i.kind, i.item) for i in read_inbox(inbox)], [("mr_merged", "!44")])
            cli.run_hook({**payload, "cwd": str(Path(tmp) / "private" / "repo")}, settings, inbox, log)
            self.assertEqual(len(read_inbox(inbox)), 1, "a skipped folder writes nothing")
            rows = cli.read_log(log)
            self.assertEqual(rows[0]["skipped"], "folder")
            self.assertEqual(rows[1]["events"], [["mr-merged", "!44", "gitlab"]])
            self.assertNotIn("glab", json.dumps(rows), "commands never reach the log")
            cli.run_hook(bash("ls", ""), settings, inbox, log)
            self.assertEqual(len(cli.read_log(log)), 2, "a call without events is not logged")

    def test_the_log_keeps_its_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "log.jsonl"
            for i in range(4000):
                cli.write_log(log, {"i": i, "pad": "x" * 100})
            self.assertLess(log.stat().st_size, cli.LOG_LIMIT + 1000)
            self.assertEqual(cli.read_log(log, 1)[0]["i"], 3999)


class DataFolderTest(unittest.TestCase):
    def test_data_points_the_helper_at_another_vault_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                self.assertEqual(cli.main(["--data", tmp, "note", "triage", "--work"]), 0)
            finally:
                cli._data_dir = None
            self.assertEqual([i.item for i in read_inbox(Path(tmp) / "inbox.jsonl")], ["triage"])

    def test_the_command_names_the_folder_only_when_it_is_not_the_default(self):
        from daykeeper import claudecode
        from daykeeper.settings import app_dir
        self.assertNotIn("--data", claudecode.helper_command(app_dir()))
        self.assertEqual(claudecode.helper_command(Path("E:/vaults"))[-2:], ["--data", str(Path("E:/vaults"))])


if __name__ == "__main__":
    unittest.main()
