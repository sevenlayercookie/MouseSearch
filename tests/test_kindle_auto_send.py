import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import app as module

HASH = "ab" * 20
CONFIG = dict(KINDLE_ENABLED=True, KINDLE_AUTO_SEND=True, KINDLE_SMTP_HOST="smtp.example.com",
              KINDLE_EMAIL="reader@kindle.com", KINDLE_FROM_EMAIL="sender@example.com")


class KindleAutoSendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(module, "DATABASE_FILE", str(Path(self.tmp.name) / "db.json")),
            patch.dict(module.app.config, CONFIG),
            patch.object(module, "broadcast_toast", AsyncMock()),
        ]
        for p in self.patches:
            p.start()
        self.send = patch.object(module, "send_torrent_to_kindle",
                                 AsyncMock(return_value=(True, "SMTP server accepted book.epub")))
        self.send_mock = self.send.start()

    def tearDown(self):
        self.send.stop()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_needs_both_switches(self):
        self.assertTrue(module.kindle_auto_send_enabled())
        with patch.dict(module.app.config, KINDLE_AUTO_SEND=False):
            self.assertFalse(module.kindle_auto_send_enabled())
        with patch.dict(module.app.config, KINDLE_ENABLED=False):
            self.assertFalse(module.kindle_auto_send_enabled())

    async def test_sends_ebooks_once_and_remembers(self):
        module.save_database({HASH: {"title": "Book", "main_cat": "14", "status": "pending"}})
        self.assertTrue(await module.auto_send_to_kindle(HASH))
        self.send_mock.assert_awaited_once()
        self.assertTrue(self.send_mock.await_args.kwargs["wait_for_stable_source"])
        self.assertIn("kindle_sent_at", module.load_database()[HASH])
        module.broadcast_toast.assert_awaited_with("Sent 'Book' to Kindle.", "success")

        # A later completion of the same download (e.g. after a restart) does not resend.
        self.assertFalse(await module.auto_send_to_kindle(HASH))
        self.send_mock.assert_awaited_once()

    async def test_skips_audiobooks_and_unknown_categories(self):
        self.assertFalse(await module.auto_send_to_kindle(HASH, {"title": "Audio", "main_cat": "13"}))
        self.assertFalse(await module.auto_send_to_kindle(HASH, None))  # no metadata at all
        self.send_mock.assert_not_awaited()

    async def test_uses_book_info_from_the_monitor_when_not_in_database(self):
        self.assertTrue(await module.auto_send_to_kindle(HASH, {"title": "Book", "main_cat": 14}))
        self.send_mock.assert_awaited_once()

    async def test_failure_is_reported_and_not_marked_sent(self):
        module.save_database({HASH: {"title": "Book", "main_cat": "14"}})
        self.send_mock.return_value = (False, "No Kindle-compatible file")
        self.assertFalse(await module.auto_send_to_kindle(HASH))
        self.assertNotIn("kindle_sent_at", module.load_database()[HASH])
        module.broadcast_toast.assert_awaited_with(
            "Auto Send to Kindle failed for 'Book': No Kindle-compatible file", "warning")


class MonitorLoopHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_finished_ebook_is_sent_and_audiobook_is_not(self):
        import asyncio

        audio_hash = "cd" * 20
        client = AsyncMock()
        client.get_torrent_info_batch.return_value = {"torrents": {
            HASH: {"state": "uploading", "progress": 1, "eta": 0, "name": "Book"},
            audio_hash: {"state": "uploading", "progress": 1, "eta": 0, "name": "Audio"},
        }}
        send = AsyncMock(return_value=(True, "accepted"))
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(module, "DATABASE_FILE", str(Path(directory) / "db.json")), \
                patch.dict(module.app.config, {**CONFIG, "AUTO_ORGANIZE_ON_ADD": False}), \
                patch.object(module, "torrent_client", client), \
                patch.object(module, "send_torrent_to_kindle", send), \
                patch.object(module, "broadcast_toast", AsyncMock()), \
                patch.object(module, "broadcast_payload", AsyncMock()), \
                patch.object(module, "push_mam_stats", AsyncMock()), \
                patch.dict(module.pending_mid_resolutions, clear=True), \
                patch.dict(module.monitoring_state, {
                    HASH: {"added_at": 0, "book": {"title": "Book", "main_cat": "14"}},
                    audio_hash: {"added_at": 0, "book": {"title": "Audio", "main_cat": "13"}},
                }, clear=True):
            loop_task = asyncio.create_task(module.monitor_downloads_loop())
            try:
                for _ in range(100):
                    if send.await_count or not module.monitoring_state:
                        break
                    await asyncio.sleep(0.05)
                await asyncio.gather(*module._kindle_auto_send_tasks)
            finally:
                loop_task.cancel()
                await asyncio.gather(loop_task, return_exceptions=True)
            self.assertEqual(module.monitoring_state, {})
        send.assert_awaited_once()
        self.assertEqual(send.await_args.args[0], HASH)

    async def test_hook_is_off_unless_enabled(self):
        import asyncio

        client = AsyncMock()
        client.get_torrent_info_batch.return_value = {"torrents": {
            HASH: {"state": "uploading", "progress": 1, "eta": 0, "name": "Book"}}}
        send = AsyncMock(return_value=(True, "accepted"))
        with patch.dict(module.app.config, {**CONFIG, "KINDLE_AUTO_SEND": False, "AUTO_ORGANIZE_ON_ADD": False}), \
                patch.object(module, "torrent_client", client), \
                patch.object(module, "send_torrent_to_kindle", send), \
                patch.object(module, "broadcast_payload", AsyncMock()), \
                patch.object(module, "push_mam_stats", AsyncMock()), \
                patch.dict(module.pending_mid_resolutions, clear=True), \
                patch.dict(module.monitoring_state, {
                    HASH: {"added_at": 0, "book": {"title": "Book", "main_cat": "14"}}}, clear=True):
            loop_task = asyncio.create_task(module.monitor_downloads_loop())
            try:
                for _ in range(100):
                    if not module.monitoring_state:
                        break
                    await asyncio.sleep(0.05)
            finally:
                loop_task.cancel()
                await asyncio.gather(loop_task, return_exceptions=True)
            self.assertEqual(module.monitoring_state, {})
        send.assert_not_awaited()


class MonitoringBookInfoTests(unittest.IsolatedAsyncioTestCase):
    async def test_added_downloads_remember_their_category(self):
        client = Mock()
        client.login = AsyncMock()
        client.add_torrent = AsyncMock(return_value={"status": "success", "message": "Added", "hash": HASH})
        config = {"TORRENT_CLIENT_TYPE": "deluge", "BLOCK_DOWNLOAD_ON_LOW_BUFFER": False,
                  "AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD": False, "AUTO_ORGANIZE_ON_ADD": False,
                  "AUTO_ORGANIZE_ON_SCHEDULE": False}
        with patch.dict(module.app.config, config), \
                patch.object(module, "torrent_client", client), \
                patch.object(module, "start_monitoring_loop", Mock()), \
                patch.dict(module.monitoring_state, clear=True):
            await module.add_torrent_to_client({
                "torrent_url": "https://mam.example/tor/download.php/x?tid=5", "id": "5",
                "title": "Book", "author": "Author", "size": "1 MiB", "main_cat": "14",
            })
            self.assertEqual(module.monitoring_state[HASH]["book"]["main_cat"], "14")


if __name__ == "__main__":
    unittest.main()
