"""Text templates: the placeholders, the questions, the storage in settings.json and the
ready-made templates."""

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from daykeeper import templates as tp
from daykeeper.i18n import set_language
from daykeeper.locale_ru import RU
from daykeeper.settings import read_settings, write_settings
from tests.helpers import reset_globals

NOW = datetime(2026, 10, 14, 9, 30)
CONTEXT = {
    "key": "DEMO-1",
    "key_url": "https://tracker.example/browse/DEMO-1",
    "mr": "!42",
    "mr_url": "https://git.example/group/repo/-/merge_requests/42",
    "me": "someone",
    "clipboard": "copied text",
    "note_title": "Release",
    "note_items": "- one\n- two",
}


def t(text: str) -> tp.Template:
    return tp.Template("x", "X", text)


class RenderTest(unittest.TestCase):
    def test_every_context_placeholder(self):
        for name, value in CONTEXT.items():
            self.assertEqual(tp.render(t(f"<{{{name}}}>"), CONTEXT, {}, NOW), f"<{value}>", name)

    def test_dates(self):
        self.assertEqual(tp.render(t("{today}"), {}, {}, NOW), "2026-10-14")
        self.assertEqual(tp.render(t("{date}"), {}, {}, NOW), "2026-10-14")
        self.assertEqual(tp.render(t("{date:%d.%m.%Y}"), {}, {}, NOW), "14.10.2026")
        self.assertEqual(tp.render(t("{date:%H:%M}"), {}, {}, NOW), "09:30")
        self.assertEqual(tp.render(t("{today}"), {"today": "given"}, {}, NOW), "given")

    def test_every_documented_placeholder_is_filled(self):
        text = " ".join("{date:%Y}" if name == "date" else f"{{{name}}}" for name in tp.PLACEHOLDERS)
        self.assertNotIn("{", tp.render(t(text), CONTEXT, {}, NOW))

    def test_unknown_braces_stay_as_typed(self):
        text = "{unknown} {Key} {} {key:x} { key } {json: 1} {{key}}"
        self.assertEqual(tp.render(t(text), {}, {}, NOW), text)
        self.assertEqual(tp.render(t("{key}"), {}, {}, NOW), "{key}")  # known, but not given

    def test_questions(self):
        tmpl = t("{?Environment} and {?Build} then {? Environment } again {?Build}")
        self.assertEqual(tp.questions(tmpl), ["Environment", "Build"])
        self.assertEqual(tp.render(tmpl, {}, {"Environment": "qa", "Build": "7"}, NOW), "qa and 7 then qa again 7")

    def test_question_without_answer_is_empty(self):
        tmpl = t("On {?Environment}: {?Result}.")
        self.assertEqual(tp.render(tmpl, CONTEXT, {"Environment": "qa"}, NOW), "On qa: .")

    def test_no_questions(self):
        self.assertEqual(tp.questions(t("{key} {date:%d}")), [])


class StorageTest(unittest.TestCase):
    def test_round_trip_through_settings_file(self):
        items = [
            tp.Template("a", "First", "{key}", ["journal"], open_link=True),
            tp.Template("b", "Second", "{?Q}", ["notes", "quick"]),
        ]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "settings.json"
            write_settings(path, tp.save({"journal": {"preset": "qa"}}, items))
            back = read_settings(path)
        self.assertEqual(back["journal"], {"preset": "qa"})
        self.assertEqual(tp.load(back), items)

    def test_save_does_not_change_the_given_settings(self):
        settings = {"lang": "en"}
        out = tp.save(settings, [t("x")])
        self.assertEqual(settings, {"lang": "en"})
        self.assertEqual(out["lang"], "en")
        self.assertEqual(json.loads(json.dumps(out))["templates"][0]["text"], "x")

    def test_no_key_or_not_a_list(self):
        self.assertEqual(tp.load({}), [])
        self.assertEqual(tp.load({"templates": "text"}), [])
        self.assertEqual(tp.load({"templates": {"a": 1}}), [])

    def test_garbage_entries_are_dropped(self):
        raw = {"templates": [
            "text",
            None,
            {"name": "No text"},
            {"text": "No name"},
            {"name": "  ", "text": "blank name"},
            {"name": "Number text", "text": 5},
            {"id": "ok", "name": " Kept ", "text": "{key}", "places": ["quick", "nowhere", "journal"], "open_link": 1},
            {"id": "ok", "name": "Same id", "text": "y"},
            {"name": "No id, no places", "text": "z"},
            {"id": "p", "name": "Places not a list", "text": "z", "places": "journal"},
        ]}
        items = tp.load(raw)
        self.assertEqual([i.name for i in items], ["Kept", "Same id", "No id, no places", "Places not a list"])
        self.assertEqual(items[0].places, ["journal", "quick"])
        self.assertTrue(items[0].open_link)
        self.assertEqual(len({i.id for i in items}), 4)
        self.assertEqual(items[2].places, list(tp.DEFAULT_PLACES))
        self.assertEqual(items[3].places, list(tp.DEFAULT_PLACES))


class PresetsTest(unittest.TestCase):
    def setUp(self):
        reset_globals()
        self.addCleanup(reset_globals)

    def test_three_presets_with_unique_ids(self):
        items = tp.presets()
        self.assertEqual(len(items), 3)
        self.assertEqual(len({i.id for i in items}), 3)
        for item in items:
            self.assertTrue(item.name and item.text)
            self.assertTrue(set(item.places) <= set(tp.PLACES))
            self.assertIn("{key_url}", item.text)

    def test_presets_survive_the_settings_file(self):
        self.assertEqual(tp.load(json.loads(json.dumps(tp.save({}, tp.presets())))), tp.presets())

    def test_preset_questions_render(self):
        verification = tp.presets()[0]
        self.assertEqual(tp.questions(verification), ["Environment", "Result"])
        text = tp.render(verification, CONTEXT, {"Environment": "qa", "Result": "works"}, NOW)
        self.assertEqual(text, "Tested on qa, 14.10.2026.\nResult: works\nhttps://tracker.example/browse/DEMO-1")

    def test_presets_in_russian_ask_in_russian(self):
        set_language("ru")
        items = tp.presets()
        self.assertEqual(items[0].name, RU["Verification comment"])
        self.assertEqual(tp.questions(items[0]), [RU["Environment"], RU["Result"]])
        self.assertNotIn("Steps to reproduce", items[1].text)
        self.assertIn(f"{{?{RU['Environment']}}}", items[1].text)

    def test_place_labels_are_translated(self):
        self.assertEqual(list(tp.PLACE_LABELS), list(tp.PLACES))
        self.assertEqual([v for v in tp.PLACE_LABELS.values() if v not in RU], [])


if __name__ == "__main__":
    unittest.main()
