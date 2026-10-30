import unittest
from unittest.mock import AsyncMock, Mock, patch
import app as module


class NarratorPathTests(unittest.IsolatedAsyncioTestCase):
    def test_render_narrators_and_optional_text(self):
        for narrator, expected in [("Stephen Fry", "Stephen Fry"), ('{"1":"Stephen Fry","2":"Jim Dale"}', "Stephen Fry, Jim Dale"), ("A/B: C", "AB C")]:
            values = module.relative_path_tokens_from_metadata({"narrator": narrator, "title": "Book"})
            self.assertEqual(values["{Narrator}"], expected)
            self.assertEqual(module.build_relative_path_from_template("{Title}{ (Narrator)}", values), f"Book ({expected})")
        self.assertEqual(module.build_relative_path_from_template("{Title}{ (Narrator)}", module.relative_path_tokens_from_metadata({"title": "Book"})), "Book")

    async def test_download_persists_narrator_for_scheduled_organization(self):
        client = Mock(login=AsyncMock(), add_torrent=AsyncMock(return_value={"status": "success", "message": "Added", "hash": "ab" * 20}))
        config = {"TORRENT_CLIENT_TYPE": "deluge", "BLOCK_DOWNLOAD_ON_LOW_BUFFER": False,
                  "AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD": False, "AUTO_ORGANIZE_ON_ADD": False,
                  "AUTO_ORGANIZE_ON_SCHEDULE": True}
        with patch.dict(module.app.config, config), patch.object(module, "torrent_client", client), patch.object(module, "start_monitoring_loop"), patch.object(module, "load_database", return_value={}), patch.object(module, "save_database") as save:
            _, status = await module.add_torrent_to_client({"id": "5", "torrent_url": "https://mam.example/download?tid=5", "narrator": "Stephen Fry", "title": "Book"})
        self.assertEqual(status, 200)
        self.assertEqual(save.call_args.args[0]["ab" * 20]["narrator"], "Stephen Fry")

    async def test_watch_passes_narrator(self):
        with patch.object(module, "add_torrent_to_client", AsyncMock(return_value=({}, 200))) as add:
            await module.watch_add_torrent({"id": "5", "narrator_info": '{"1":"Stephen Fry"}'}, category="", use_wedge=False)
        self.assertEqual(add.call_args.args[0]["narrator"], "Stephen Fry")
