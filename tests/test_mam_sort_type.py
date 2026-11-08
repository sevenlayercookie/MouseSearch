import unittest

from app import RESULTS_SORT_MODES, mam_sort_type_for_results_sort_mode


class MamSortTypeTests(unittest.TestCase):
    def test_maps_stat_sorts_to_mam_sort_types(self):
        self.assertEqual(mam_sort_type_for_results_sort_mode("snatches_desc"), "snatchedDesc")
        self.assertEqual(mam_sort_type_for_results_sort_mode("snatches_asc"), "snatchedAsc")
        self.assertEqual(mam_sort_type_for_results_sort_mode("seeders_desc"), "seedersDesc")
        self.assertEqual(mam_sort_type_for_results_sort_mode("size_asc"), "sizeAsc")
        self.assertEqual(mam_sort_type_for_results_sort_mode("date_uploaded_desc"), "dateDesc")
        self.assertEqual(mam_sort_type_for_results_sort_mode("title_asc"), "titleAsc")

    def test_client_only_sorts_use_mam_default(self):
        for mode in ("quality_desc", "author_asc", "author_desc"):
            self.assertEqual(mam_sort_type_for_results_sort_mode(mode), "default")

    def test_unknown_or_missing_sort_uses_mam_default(self):
        self.assertEqual(mam_sort_type_for_results_sort_mode("bogus"), "default")
        self.assertEqual(mam_sort_type_for_results_sort_mode(None), "default")

    def test_every_sort_mode_maps_to_a_value(self):
        for mode in RESULTS_SORT_MODES:
            self.assertTrue(mam_sort_type_for_results_sort_mode(mode))


class MamSearchSortRequestTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_forwards_explicit_saved_and_invalid_sorts(self):
        from unittest.mock import AsyncMock, Mock, patch
        import app as module

        response = Mock()
        response.json.return_value = {"data": []}
        with patch.dict(module.app.config, {
            **module.FALLBACK_CONFIG,
            "RESULTS_SORT_MODE": "seeders_desc",
        }), patch.object(module, "login_mam", AsyncMock(return_value=True)), \
                patch.object(module, "fetch_mam_json_load", AsyncMock(return_value={})), \
                patch.object(module, "request_mam", AsyncMock(return_value=response)) as upstream, \
                patch.object(module, "torrent_client", None), \
                patch.object(module, "load_database", return_value={}):
            client = module.app.test_client()
            cases = [("snatches_desc", "snatchedDesc"), (None, "seedersDesc"),
                     ("bogus", "default"), ("author_asc", "default")]
            for mode, expected in cases:
                with self.subTest(mode=mode):
                    args = {"sort_mode": mode} if mode is not None else {}
                    result = await client.get('/mam/search', query_string=args)
                    self.assertEqual(result.status_code, 200)
                    self.assertIn('No results found', await result.get_data(as_text=True))
                    self.assertEqual(upstream.call_args.kwargs['params']['tor[sortType]'], expected)
                    self.assertEqual(upstream.call_args.kwargs['timeout'].read, 60.0)
                    self.assertEqual(upstream.call_args.kwargs['timeout'].connect, 5.0)


if __name__ == "__main__":
    unittest.main()
