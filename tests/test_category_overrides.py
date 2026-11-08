import unittest

from app import (
    get_organized_destination_root,
    migrate_type_specific_paths_to_overrides,
    normalize_category_overrides,
)


class CategoryOverrideTests(unittest.TestCase):
    def setUp(self):
        self.config = {
            "ORGANIZED_PATH": "/library/default",
            "DESTINATION_PATHS": [
                {"path": "/library/default", "default_main_cat": ""},
                {"path": "/library/audio-legacy", "default_main_cat": "13"},
                {"path": "/library/extra", "default_main_cat": ""},
            ],
            "CATEGORY_OVERRIDES": [
                {"main_cat": "13", "root_path": "/library/audio", "rel_path_template": "", "link_mode": ""},
            ],
        }

    def test_override_root_beats_type_specific_path(self):
        self.assertEqual(get_organized_destination_root({"main_cat": "13"}, self.config), "/library/audio")

    def test_explicit_choice_beats_override(self):
        meta = {"main_cat": "13", "custom_destination_path": "/library/picked"}
        self.assertEqual(get_organized_destination_root(meta, self.config), "/library/picked")

    def test_override_without_root_falls_through(self):
        self.config["CATEGORY_OVERRIDES"][0]["root_path"] = ""
        self.assertEqual(get_organized_destination_root({"main_cat": "13"}, self.config), "/library/audio-legacy")

    def test_migration_only_fills_gaps_and_is_idempotent(self):
        self.config["CATEGORY_OVERRIDES"] = []
        once = migrate_type_specific_paths_to_overrides(self.config)
        self.assertEqual(once, [{"main_cat": "13", "root_path": "/library/audio-legacy", "rel_path_template": "", "link_mode": ""}])
        self.config["CATEGORY_OVERRIDES"] = once
        self.assertEqual(migrate_type_specific_paths_to_overrides(self.config), once)

        # An override the user already set is never replaced by a legacy path.
        self.config["CATEGORY_OVERRIDES"] = [{"main_cat": "13", "root_path": "/library/mine"}]
        self.assertEqual(migrate_type_specific_paths_to_overrides(self.config)[0]["root_path"], "/library/mine")

    def test_normalize_drops_unknown_and_duplicate_categories(self):
        rows = normalize_category_overrides([
            {"main_cat": "99", "root_path": "/x"},
            {"main_cat": "14", "link_mode": "Copy"},
            {"main_cat": "14", "root_path": "/dupe"},
            "junk",
        ])
        self.assertEqual(rows, [{"main_cat": "14", "root_path": "", "rel_path_template": "", "link_mode": "copy"}])


if __name__ == "__main__":
    unittest.main()
