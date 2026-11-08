import unittest

from app import (
    build_relative_path_from_template,
    invert_author_name,
    relative_path_tokens_from_metadata,
    split_author_names,
)


SHELFMARK_STYLE = "{Author}/{Title}{ - Series}{ #SeriesNumber}{ (Year)}"


def tokens(**overrides):
    values = {
        "{Author}": "",
        "{AuthorLastFirst}": "",
        "{FirstAuthor}": "",
        "{FirstAuthorLastFirst}": "",
        "{Series}": "",
        "{SeriesNumber}": "",
        "{Title}": "",
        "{Genre}": "",
        "{Format}": "",
        "{Category}": "",
        "{Year}": "",
    }
    values.update({f"{{{key}}}": value for key, value in overrides.items()})
    return values


class RelativePathTemplateTests(unittest.TestCase):
    def test_plain_tokens_collapse_empty_levels(self):
        values = tokens(Author="Frank Herbert", Title="Dune")
        self.assertEqual(build_relative_path_from_template("{Author}/{Series}/{Title}", values), "Frank Herbert/Dune")
        self.assertEqual(build_relative_path_from_template("/{Author}//{Title}/", values), "Frank Herbert/Dune")

    def test_optional_groups_render_with_values(self):
        values = tokens(
            Author="Brandon Sanderson",
            Title="Oathbringer",
            Series="The Stormlight Archive",
            SeriesNumber="3",
            Year="2017",
        )
        self.assertEqual(
            build_relative_path_from_template(SHELFMARK_STYLE, values),
            "Brandon Sanderson/Oathbringer - The Stormlight Archive #3 (2017)",
        )

    def test_optional_groups_drop_when_empty(self):
        self.assertEqual(
            build_relative_path_from_template(
                SHELFMARK_STYLE, tokens(Author="Andy Weir", Title="Project Hail Mary", Year="2021")
            ),
            "Andy Weir/Project Hail Mary (2021)",
        )
        self.assertEqual(
            build_relative_path_from_template(SHELFMARK_STYLE, tokens(Author="Andy Weir", Title="Project Hail Mary")),
            "Andy Weir/Project Hail Mary",
        )

    def test_series_number_is_not_read_as_series(self):
        values = tokens(Title="T", Series="S", SeriesNumber="")
        self.assertEqual(build_relative_path_from_template("{Title}{ #SeriesNumber}", values), "T")

    def test_optional_group_can_add_a_folder(self):
        template = "{Author}/{Series/}{Title}"
        self.assertEqual(
            build_relative_path_from_template(template, tokens(Author="A", Series="S", Title="T")), "A/S/T"
        )
        self.assertEqual(build_relative_path_from_template(template, tokens(Author="A", Title="T")), "A/T")

    def test_segments_are_trimmed(self):
        self.assertEqual(
            build_relative_path_from_template("{Author}/{Title} {(Year)}", tokens(Author="A", Title="T")), "A/T"
        )

    def test_unknown_groups_are_left_alone(self):
        self.assertEqual(
            build_relative_path_from_template("{Author}/{Unknown}", tokens(Author="A")), "A/{Unknown}"
        )

    def test_backslashes_are_normalized(self):
        self.assertEqual(
            build_relative_path_from_template("{Author}\\{Title}", tokens(Author="A", Title="T")), "A/T"
        )


class RelativePathTokensFromMetadataTests(unittest.TestCase):
    def test_year_token_from_metadata(self):
        meta = {"author": "Andy Weir", "title": "Project Hail Mary", "year": "2021"}
        self.assertEqual(relative_path_tokens_from_metadata(meta)["{Year}"], "2021")
        self.assertEqual(
            build_relative_path_from_template(SHELFMARK_STYLE, relative_path_tokens_from_metadata(meta)),
            "Andy Weir/Project Hail Mary (2021)",
        )

    def test_missing_or_invalid_year_is_blank(self):
        self.assertEqual(relative_path_tokens_from_metadata({})["{Year}"], "")
        self.assertEqual(relative_path_tokens_from_metadata({"year": "unknown"})["{Year}"], "")
        self.assertEqual(relative_path_tokens_from_metadata({"year": 2017})["{Year}"], "2017")
        self.assertEqual(relative_path_tokens_from_metadata({"year": "2017-11-14"})["{Year}"], "2017")


class AuthorNameTests(unittest.TestCase):
    def test_invert_simple_names(self):
        self.assertEqual(invert_author_name("Frank Herbert"), "Herbert, Frank")
        self.assertEqual(invert_author_name("J.R.R. Tolkien"), "Tolkien, J.R.R.")
        self.assertEqual(invert_author_name("Kevin J. Anderson"), "Anderson, Kevin J.")

    def test_invert_keeps_particles_with_surname(self):
        self.assertEqual(invert_author_name("Ursula K. Le Guin"), "Le Guin, Ursula K.")
        self.assertEqual(invert_author_name("Ludwig van Beethoven"), "van Beethoven, Ludwig")
        self.assertEqual(invert_author_name("Charles de Lint"), "de Lint, Charles")
        # A particle is never taken as the whole given name.
        self.assertEqual(invert_author_name("Van Morrison"), "Morrison, Van")

    def test_invert_keeps_suffixes_at_end(self):
        self.assertEqual(invert_author_name("Kurt Vonnegut Jr."), "Vonnegut, Kurt, Jr.")
        self.assertEqual(invert_author_name("Kurt Vonnegut, Jr."), "Vonnegut, Kurt, Jr.")
        self.assertEqual(invert_author_name("Henry Ford II"), "Ford, Henry, II")

    def test_invert_single_and_empty_names(self):
        self.assertEqual(invert_author_name("Plato"), "Plato")
        self.assertEqual(invert_author_name("Plato Jr."), "Plato Jr.")
        self.assertEqual(invert_author_name(""), "")

    def test_split_author_names(self):
        self.assertEqual(split_author_names("Frank Herbert, Kevin J. Anderson"), ["Frank Herbert", "Kevin J. Anderson"])
        self.assertEqual(split_author_names("Douglas Preston & Lincoln Child"), ["Douglas Preston", "Lincoln Child"])
        self.assertEqual(split_author_names("Kurt Vonnegut, Jr., Joe Hill"), ["Kurt Vonnegut Jr.", "Joe Hill"])
        self.assertEqual(split_author_names("  "), [])
        self.assertEqual(split_author_names(None), [])

    def test_author_tokens_from_metadata(self):
        values = relative_path_tokens_from_metadata({"author": "Brian Herbert, Kevin J. Anderson", "title": "Dune"})
        self.assertEqual(values["{Author}"], "Brian Herbert, Kevin J. Anderson")
        # sanitize_filename drops the trailing "." a folder name cannot end with on Windows.
        self.assertEqual(values["{AuthorLastFirst}"], "Herbert, Brian & Anderson, Kevin J")
        self.assertEqual(values["{FirstAuthor}"], "Brian Herbert")
        self.assertEqual(values["{FirstAuthorLastFirst}"], "Herbert, Brian")
        self.assertEqual(
            build_relative_path_from_template("{FirstAuthorLastFirst}/{Title}", values), "Herbert, Brian/Dune"
        )
        self.assertEqual(
            build_relative_path_from_template("{AuthorLastFirst}/{Title}", values), "Herbert, Brian & Anderson, Kevin J/Dune"
        )

    def test_author_tokens_are_not_confused_with_author(self):
        values = tokens(Author="A", AuthorLastFirst="B", FirstAuthor="C", FirstAuthorLastFirst="D")
        self.assertEqual(
            build_relative_path_from_template("{Author}/{AuthorLastFirst}/{FirstAuthor}/{FirstAuthorLastFirst}", values),
            "A/B/C/D",
        )
        self.assertEqual(build_relative_path_from_template("{by FirstAuthor}", values), "by C")

    def test_missing_author_leaves_author_tokens_blank(self):
        values = relative_path_tokens_from_metadata({})
        for token in ("{Author}", "{AuthorLastFirst}", "{FirstAuthor}", "{FirstAuthorLastFirst}"):
            self.assertEqual(values[token], "")


if __name__ == "__main__":
    unittest.main()
