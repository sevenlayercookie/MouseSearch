import unittest
from app import build_relative_path_from_template, relative_path_tokens_from_metadata


class SeriesNumberPaddingTests(unittest.TestCase):
    def test_padding_from_stored_metadata(self):
        for number, expected in [("1", "01"), ("01", "01"), ("23", "23"), ("123", "123"), ("1.5", "01.5"), ("0.5", "00.5"), ("0", "00"), ("", ""), (None, ""), ("-1", ""), ("Omnibus", "Omnibus")]:
            values = relative_path_tokens_from_metadata({"series_info": {"1": ["Series", number]}})
            self.assertEqual(build_relative_path_from_template("{SeriesNumber:02}", values), expected)
            self.assertEqual(build_relative_path_from_template("Book{ #SeriesNumber:02}", values), "Book" + (" #" + expected if expected else ""))

    def test_width_and_unchanged_plain_token(self):
        values = {"{SeriesNumber}": "1.5", "{Title}": "Book"}
        self.assertEqual(build_relative_path_from_template("{SeriesNumber:03} - {Title}", values), "001.5 - Book")
        self.assertEqual(build_relative_path_from_template("{SeriesNumber}", values), "1.5")
        self.assertEqual(build_relative_path_from_template("{SeriesNumber:010}", values), "0000000001.5")
        self.assertEqual(build_relative_path_from_template("{SeriesNumber:011}", values), "1.5:011")
        self.assertEqual(build_relative_path_from_template("{Title:02}", values), "Book:02")
