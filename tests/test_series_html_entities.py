import unittest
from app import build_relative_path_from_template, relative_path_tokens_from_metadata


class SeriesHtmlEntityTests(unittest.TestCase):
    def test_decodes_before_sanitization_for_stored_dict_and_json(self):
        import json
        for name, expected in [("Derek&#039;s Dungeon Depot", "Derek's Dungeon Depot"), ("A &amp; B", "A & B"), ("A &#x2F; B &quot;C&quot;", "A  B C"), ("Plain O'Brien", "Plain O'Brien"), ("&lt;script&gt;", "script")]:
            series = {"1": [name, "1"]}
            for raw in (series, json.dumps(series)):
                values = relative_path_tokens_from_metadata({"series_info": raw, "title": "Book"})
                self.assertEqual(values["{Series}"], expected)
                self.assertEqual(build_relative_path_from_template("{Series}/{Title}", values), expected + "/Book")

    def test_missing_series_remains_blank(self):
        self.assertEqual(relative_path_tokens_from_metadata({})["{Series}"], "")
