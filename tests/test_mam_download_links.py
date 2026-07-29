import unittest

from app import append_personal_freeleech_flag, build_mam_download_link


class MamDownloadLinkTests(unittest.TestCase):
    BASE_URL = "https://www.myanonamouse.net/tor/download.php/"

    def test_adds_tid_when_dl_value_has_no_query(self):
        result = build_mam_download_link(self.BASE_URL, "download-token", 12345)

        self.assertEqual(
            result,
            "https://www.myanonamouse.net/tor/download.php/download-token?tid=12345",
        )

    def test_replaces_existing_tid_in_dl_value(self):
        result = build_mam_download_link(
            self.BASE_URL,
            "download-token?tid=12345",
            12345,
        )

        self.assertEqual(result.count("tid="), 1)
        self.assertTrue(result.endswith("?tid=12345"))

    def test_collapses_malformed_duplicate_tid(self):
        result = build_mam_download_link(
            self.BASE_URL,
            "download-token?tid=12345?tid=12345",
            12345,
        )

        self.assertEqual(result.count("tid="), 1)
        self.assertTrue(result.endswith("?tid=12345"))

    def test_preserves_other_query_parameters(self):
        result = build_mam_download_link(
            self.BASE_URL,
            "download-token?source=search&tid=999",
            12345,
        )

        self.assertEqual(
            result,
            "https://www.myanonamouse.net/tor/download.php/"
            "download-token?source=search&tid=12345",
        )

    def test_requires_both_dl_value_and_torrent_id(self):
        self.assertEqual(build_mam_download_link(self.BASE_URL, "", 12345), "")
        self.assertEqual(build_mam_download_link(self.BASE_URL, "download-token", None), "")

    def test_freeleech_flag_preserves_normalized_tid(self):
        result = build_mam_download_link(
            self.BASE_URL,
            "download-token?tid=12345",
            12345,
        )
        with_freeleech = append_personal_freeleech_flag(result)

        self.assertTrue(with_freeleech.endswith("?tid=12345&fl"))
        self.assertEqual(
            append_personal_freeleech_flag(with_freeleech),
            with_freeleech,
        )


if __name__ == "__main__":
    unittest.main()
