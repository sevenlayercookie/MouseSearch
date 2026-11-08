import tempfile
import unittest
from pathlib import Path
from unittest import mock

import kindle


def make_config(**overrides):
    config = {
        "KINDLE_EMAIL": "reader@kindle.com",
        "KINDLE_FROM_EMAIL": "me@example.com",
        "KINDLE_SMTP_HOST": "smtp.example.com",
        "KINDLE_SMTP_PORT": 587,
        "KINDLE_SMTP_SECURITY": "starttls",
        "KINDLE_SMTP_USERNAME": "me@example.com",
        "KINDLE_SMTP_PASSWORD": "secret",
    }
    config.update(overrides)
    return config


class FindKindleFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, size=10):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        return path

    def test_prefers_epub_over_other_formats(self):
        self.write("book.pdf", 500)
        self.write("book.mobi", 500)
        epub = self.write("nested/book.epub", 100)
        self.assertEqual(kindle.find_kindle_file(self.root), epub)

    def test_largest_file_wins_within_a_format(self):
        self.write("sample.epub", 10)
        full = self.write("full.epub", 1000)
        self.assertEqual(kindle.find_kindle_file(self.root), full)

    def test_returns_none_when_only_unsupported_formats(self):
        self.write("book.mobi")
        self.write("book.azw3")
        self.assertIsNone(kindle.find_kindle_file(self.root))

    def test_single_file_torrent(self):
        pdf = self.write("book.PDF")
        self.assertEqual(kindle.find_kindle_file(pdf), pdf)

    def test_missing_path(self):
        self.assertIsNone(kindle.find_kindle_file(self.root / "missing"))


class ConfigTests(unittest.TestCase):
    def test_parse_recipients(self):
        self.assertEqual(
            kindle.parse_recipients("a@kindle.com; b@kindle.com, a@kindle.com\n"),
            ["a@kindle.com", "b@kindle.com"],
        )

    def test_config_problem(self):
        self.assertIsNone(kindle.kindle_config_problem(make_config()))
        self.assertIn("SMTP", kindle.kindle_config_problem(make_config(KINDLE_SMTP_HOST="")))
        self.assertIn("Kindle", kindle.kindle_config_problem(make_config(KINDLE_EMAIL=" ")))
        self.assertIn(
            "Sender",
            kindle.kindle_config_problem(make_config(KINDLE_FROM_EMAIL="", KINDLE_SMTP_USERNAME="")),
        )

    def test_sender_falls_back_to_username(self):
        self.assertIsNone(kindle.kindle_config_problem(make_config(KINDLE_FROM_EMAIL="")))

    def test_normalize_security(self):
        self.assertEqual(kindle.normalize_smtp_security("SSL"), "ssl")
        self.assertEqual(kindle.normalize_smtp_security("bogus"), "starttls")


class SendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.book = Path(self.tmp.name) / "My Book.epub"
        self.book.write_bytes(b"epub-bytes")

    def tearDown(self):
        self.tmp.cleanup()

    def test_sends_attachment_with_starttls_and_login(self):
        with mock.patch("kindle.smtplib.SMTP") as smtp_cls:
            server = smtp_cls.return_value.__enter__.return_value = smtp_cls.return_value
            server.send_message.return_value = {}
            message = kindle.send_file_to_kindle(make_config(), self.book, title="My Book - Author")

        smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=60.0)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("me@example.com", "secret")
        sent = server.send_message.call_args.args[0]
        self.assertEqual(sent["To"], "reader@kindle.com")
        self.assertEqual(sent["Subject"], "My Book - Author")
        attachment = next(sent.iter_attachments())
        self.assertEqual(attachment.get_filename(), "My Book.epub")
        self.assertEqual(attachment.get_content_type(), "application/epub+zip")
        self.assertEqual(attachment.get_content(), b"epub-bytes")
        self.assertIn("reader@kindle.com", message)

    def test_ssl_mode_uses_smtp_ssl_and_default_port(self):
        config = make_config(KINDLE_SMTP_SECURITY="ssl", KINDLE_SMTP_PORT=0)
        with mock.patch("kindle.smtplib.SMTP_SSL") as smtp_ssl_cls:
            smtp_ssl_cls.return_value.__enter__.return_value = smtp_ssl_cls.return_value
            smtp_ssl_cls.return_value.send_message.return_value = {}
            kindle.send_file_to_kindle(config, self.book)
        self.assertEqual(smtp_ssl_cls.call_args.args[:2], ("smtp.example.com", 465))
        smtp_ssl_cls.return_value.starttls.assert_not_called()

    def test_partial_recipient_rejection_is_not_success(self):
        with mock.patch("kindle.smtplib.SMTP") as smtp:
            server = smtp.return_value
            server.send_message.return_value = {"second@kindle.com": (550, b"rejected")}
            with self.assertRaisesRegex(kindle.KindleSendError, "second@kindle.com") as error:
                kindle.send_file_to_kindle(make_config(KINDLE_EMAIL="reader@kindle.com,second@kindle.com"), self.book)
            self.assertIn("duplicates", str(error.exception))

    def test_connection_does_not_send_email_or_require_recipient(self):
        with mock.patch("kindle.smtplib.SMTP") as smtp:
            server = smtp.return_value
            result = kindle.test_connection(make_config(KINDLE_EMAIL="", KINDLE_FROM_EMAIL=""))
            server.starttls.assert_called_once()
            server.login.assert_called_once()
            server.send_message.assert_not_called()
            self.assertIn("No email was sent", result)

    def test_rejects_oversized_file(self):
        with mock.patch.object(kindle, "KINDLE_MAX_ATTACHMENT_BYTES", 3):
            with self.assertRaises(kindle.KindleSendError):
                kindle.send_file_to_kindle(make_config(), self.book)

    def test_smtp_errors_are_wrapped(self):
        with mock.patch("kindle.smtplib.SMTP", side_effect=OSError("connection refused")):
            with self.assertRaises(kindle.KindleSendError) as ctx:
                kindle.send_file_to_kindle(make_config(), self.book)
        self.assertIn("connection refused", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
