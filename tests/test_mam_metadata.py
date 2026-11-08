import unittest

from app import parse_mam_metadata, rank_results


class MamMetadataTests(unittest.TestCase):
    def test_numeric_author_survives_ranking_and_display(self):
        ranked = rank_results([{"author_info": '{"1": "123"}', "narrator_info": '{"2": "456"}'}])
        self.assertEqual(parse_mam_metadata(ranked[0]["author_info"]), "123")
        self.assertEqual(parse_mam_metadata(ranked[0]["narrator_info"]), "456")

    def test_flattened_json_like_names_remain_text(self):
        for name in ('0', '123', 'true', 'null', '[]', 'A &amp; B'):
            with self.subTest(name=name):
                self.assertEqual(parse_mam_metadata(name), name.replace('&amp;', '&'))

    def test_mappings_still_decode(self):
        self.assertEqual(parse_mam_metadata('{"1": "A &amp; B", "2": "C"}'), 'A & B, C')
        self.assertEqual(parse_mam_metadata('{"1": ["Series", "2", 3]}', is_series=True), 'Series #2')
        self.assertEqual(parse_mam_metadata('{}'), '')

    def test_empty_series_values_remain_empty(self):
        for value in ('null', '[]', '{}', ''):
            with self.subTest(value=value):
                self.assertEqual(parse_mam_metadata(value, is_series=True), '')
