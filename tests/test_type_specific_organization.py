import unittest

from app import get_organized_destination_root


class TypeSpecificOrganizationDestinationTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "ORGANIZED_PATH": "/library/default",
            "DESTINATION_PATHS": [
                {"path": "/library/default", "default_main_cat": ""},
                {"path": "/library/audio", "default_main_cat": "13"},
                {"path": "/library/ebooks", "default_main_cat": "14"},
            ],
        }

    def test_explicit_per_book_destination_takes_precedence(self):
        destination = get_organized_destination_root(
            {"main_cat": "13", "custom_destination_path": "/library/custom"},
            self.config,
        )
        self.assertEqual(destination, "/library/custom")

    def test_uses_destination_mapped_to_books_main_category(self):
        self.assertEqual(
            get_organized_destination_root({"main_cat": "13"}, self.config),
            "/library/audio",
        )
        self.assertEqual(
            get_organized_destination_root({"main_cat": 14}, self.config),
            "/library/ebooks",
        )

    def test_falls_back_to_default_for_unmapped_category(self):
        self.assertEqual(
            get_organized_destination_root({"main_cat": "16"}, self.config),
            "/library/default",
        )
