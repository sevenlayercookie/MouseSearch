import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import asyncio
import app as module


class ManualKindleTests(unittest.IsolatedAsyncioTestCase):
    async def test_sending_preserves_concurrent_metadata_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            book = Path(directory) / 'book.epub'
            book.write_bytes(b'book')
            started, release = threading.Event(), threading.Event()
            def send(*args, **kwargs):
                started.set()
                release.wait(5)
                return 'SMTP server accepted the email. Kindle delivery is not confirmed.'
            config = dict(KINDLE_ENABLED=True, KINDLE_SMTP_HOST='smtp.example.com',
                          KINDLE_EMAIL='reader@kindle.com', KINDLE_FROM_EMAIL='sender@example.com')
            with patch.object(module, 'DATABASE_FILE', str(Path(directory) / 'db.json')), \
                 patch.dict(module.app.config, config), \
                 patch.object(module, 'torrent_client', AsyncMock()) as client, \
                 patch.object(module, 'resolve_local_content_path', return_value=book), \
                 patch.object(module.kindle, 'send_file_to_kindle', side_effect=send):
                client.get_torrent_info.return_value = {'progress': 1, 'name': book.name}
                module.save_database({'abc': {'status': 'pending'}})
                task = asyncio.create_task(module.send_torrent_to_kindle('abc'))
                try:
                    self.assertTrue(await asyncio.to_thread(started.wait, 5))
                    updated = {'abc': {'status': 'organized'}, 'new': {'status': 'pending'}}
                    module.save_database(updated)
                finally:
                    release.set()
                success, message = await task
                self.assertTrue(success)
                self.assertIn('SMTP', message)
                self.assertEqual(module.load_database(), updated)

    async def test_disabled_manual_send_is_rejected(self):
        with patch.dict(module.app.config, KINDLE_ENABLED=False):
            success, message = await module.send_torrent_to_kindle('abc')
            self.assertFalse(success)
            self.assertIn('disabled', message)
