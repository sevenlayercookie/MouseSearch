import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import app as app_module
from watches import store


def fake_client():
    client = Mock()
    client.login = AsyncMock()
    client.add_torrent = AsyncMock(return_value={"status": "success", "message": "Added", "hash": "ab" * 20})
    return client


class AddTorrentHelperTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.config = patch.dict(app_module.app.config, {
            "TORRENT_CLIENT_TYPE": "deluge",
            "BLOCK_DOWNLOAD_ON_LOW_BUFFER": False,
            "AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD": True,
            "AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_ENABLED": False,
            "AUTO_ORGANIZE_ON_ADD": False,
            "AUTO_ORGANIZE_ON_SCHEDULE": False,
        })
        self.config.start()
        self.client = fake_client()
        self.client_patch = patch.object(app_module, "torrent_client", self.client)
        self.client_patch.start()
        self.monitor_patch = patch.object(app_module, "start_monitoring_loop", Mock())
        self.monitor_patch.start()

    def tearDown(self):
        self.monitor_patch.stop()
        self.client_patch.stop()
        self.config.stop()

    def payload(self, **overrides):
        data = {"torrent_url": "https://mam.example/tor/download.php/abc?tid=5", "id": "5",
                "title": "Book", "author": "Author", "size": "1 GiB"}
        data.update(overrides)
        return data

    def added_url(self):
        return self.client.add_torrent.await_args.args[0]

    async def test_route_behaviour_is_unchanged(self):
        async with app_module.app.test_request_context("/client/add", method="POST", json=self.payload()):
            response, status = await app_module.client_add_torrent()
        self.assertEqual(status, 200)
        body = await response.get_json()
        self.assertEqual(body["message"], "Added")
        self.assertTrue(body["personal_freeleech_applied"])
        self.assertTrue(self.added_url().endswith("&fl"))

    async def test_watches_can_opt_out_of_global_auto_wedge(self):
        result, status = await app_module.add_torrent_to_client(self.payload(), allow_auto_personal_freeleech=False)
        self.assertEqual(status, 200)
        self.assertFalse(result["personal_freeleech_applied"])
        self.assertNotIn("fl", self.added_url().split("?", 1)[1].split("&"))

    async def test_watch_adapter_requests_a_wedge_only_when_told(self):
        item = {"id": 5, "title": "Book", "author_info": "Author", "size": "1 GiB",
                "download_link": "https://mam.example/tor/download.php/abc?tid=5"}
        await app_module.watch_add_torrent(item, category="", use_wedge=False)
        self.assertFalse(self.added_url().endswith("&fl"))
        await app_module.watch_add_torrent(item, category="books", use_wedge=True)
        self.assertTrue(self.added_url().endswith("&fl"))
        self.assertEqual(self.client.add_torrent.await_args.args[1], "books")

    async def test_buffer_block_still_applies(self):
        with patch.dict(app_module.app.config, {"BLOCK_DOWNLOAD_ON_LOW_BUFFER": True}), \
                patch.object(app_module, "login_mam", AsyncMock(return_value=True)), \
                patch.object(app_module, "get_user_stats", AsyncMock(return_value={"buffer_gb": 0.5, "seedbonus": 0})):
            result, status = await app_module.add_torrent_to_client(self.payload(), allow_auto_personal_freeleech=False)
        self.assertEqual(status, 400)
        self.assertEqual(result["status"], "insufficient_buffer")
        self.client.add_torrent.assert_not_awaited()


class AccountSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def snapshot(self, data):
        response = Mock()
        response.raise_for_status = Mock()
        response.json = Mock(return_value=data)
        with patch.dict(app_module.app.config, {"MAM_API_URL": "https://mam.example"}), \
                patch.object(app_module, "login_mam", AsyncMock(return_value=True)), \
                patch.object(app_module, "request_mam", AsyncMock(return_value=response)) as request_mam:
            result = await app_module.watch_account_snapshot()
        self.assertEqual(request_mam.await_args.kwargs["params"], {"snatch_summary": "true"})
        return result

    async def test_reads_unsat_wedges_and_bytes(self):
        result = await self.snapshot({
            "uploaded_bytes": 2 * 1024 ** 3, "downloaded_bytes": 1024 ** 3, "wedges": 4,
            "vip_until": "2999-01-01 00:00:00",
            "snatch_summary": {"unsat": {"count": 3, "limit": 50, "size": 0}},
        })
        self.assertEqual(result["unsat_count"], 3)
        self.assertEqual(result["unsat_limit"], 50)
        self.assertEqual(result["wedges"], 4)
        self.assertAlmostEqual(result["uploaded_gb"], 2.0)
        self.assertAlmostEqual(result["downloaded_gb"], 1.0)
        self.assertTrue(result["vip_active"])

    async def test_missing_unsat_summary_fails_closed(self):
        self.assertIsNone(await self.snapshot({"uploaded_bytes": 1, "downloaded_bytes": 1, "wedges": 4}))


class WatchesApiGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        store.configure(Path(self.tmp.name) / "watches.db")

    def tearDown(self):
        store.close()
        self.tmp.cleanup()

    async def test_api_is_off_unless_enabled(self):
        client = app_module.app.test_client()
        with patch.dict(app_module.app.config, {"WATCHES_ENABLED": False}):
            response = await client.get("/api/watches")
            self.assertEqual(response.status_code, 403)
        with patch.dict(app_module.app.config, {"WATCHES_ENABLED": True}):
            response = await client.post("/api/watches", json={"name": "Klune", "query": '"T J Klune"'})
            self.assertEqual(response.status_code, 201)
            response = await client.post("/api/watches", json={"name": "Bad", "query": "x", "cost": "points"})
            self.assertEqual(response.status_code, 400)
            body = await (await client.get("/api/watches")).get_json()
            self.assertEqual([w["name"] for w in body["watches"]], ["Klune"])
            self.assertEqual(body["daily_limit"], app_module.FALLBACK_CONFIG["WATCHES_DAILY_GRAB_LIMIT"])


if __name__ == "__main__":
    unittest.main()
