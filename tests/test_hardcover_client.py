import unittest

import httpx

from hardcover.client import (
    AsyncTokenBucket,
    HardcoverAPIError,
    HardcoverClient,
    hardcover_api_error_from_response,
    parse_hardcover_rate_limit_headers,
)


class RecordingHardcoverClient(HardcoverClient):
    def __init__(self, responses=None):
        super().__init__("hc_pat_test", rate_limit=60)
        self.calls = []
        self.responses = list(responses or [])

    async def graphql(self, query, variables, *, cache_key=None, retry_5xx=2):
        self.calls.append((query, variables, cache_key))
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        if query == self.SEARCH_QUERY:
            return {"search": {"results": []}}
        return {}


class HardcoverHeaderTests(unittest.TestCase):
    def test_parses_advertised_burst_policy(self):
        policy = parse_hardcover_rate_limit_headers({
            "RateLimit-Policy": '"Free";q=60;w=60;burst=10',
            "RateLimit": '"Free";r=9;t=1',
        })

        self.assertEqual(policy, {
            "quota": 60.0,
            "window_seconds": 60.0,
            "burst": 10.0,
            "remaining": 9.0,
        })

    def test_structured_403_details_are_preserved(self):
        response = httpx.Response(
            403,
            json={
                "errors": ["top_level_limit_exceeded"],
            },
        )

        error = hardcover_api_error_from_response(response)

        self.assertEqual(error.status_code, 403)
        self.assertEqual(error.code, "top_level_limit_exceeded")
        self.assertIsNone(error.scope)
        self.assertIn("top_level_limit_exceeded", str(error))

    def test_insufficient_scope_details_are_preserved(self):
        response = httpx.Response(
            403,
            json={
                "error": "insufficient_scope",
                "error_description": "This operation requires another scope.",
                "scope": "read:me",
            },
        )

        error = hardcover_api_error_from_response(response)

        self.assertEqual(error.code, "insufficient_scope")
        self.assertEqual(error.scope, "read:me")
        self.assertIn("This operation requires another scope.", str(error))


class HardcoverTokenBucketTests(unittest.IsolatedAsyncioTestCase):
    async def test_bootstraps_from_headers_without_replacing_local_state(self):
        bucket = AsyncTokenBucket(60, 60.0)
        await bucket.acquire()

        await bucket.update_policy(
            quota=60,
            window_seconds=60,
            burst=10,
            remaining=9,
        )

        self.assertTrue(bucket.policy_loaded)
        self.assertEqual(bucket.capacity, 10.0)
        self.assertAlmostEqual(bucket.refill_rate, 1.0)
        self.assertLessEqual(bucket.tokens, 9.0)


class HardcoverClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefetch_searches_uses_one_search_field_per_request(self):
        client = RecordingHardcoverClient()

        await client.prefetch_searches([
            ("First Book", "Book", 5),
            ("Second Book", "Book", 5),
        ])

        self.assertEqual(len(client.calls), 2)
        for query, _, _ in client.calls:
            self.assertEqual(query, client.SEARCH_QUERY)
            self.assertEqual(query.count("\n      search("), 1)

    async def test_bulk_isbn_prefetch_maps_hits_and_caches_misses(self):
        edition = {
            "id": 42,
            "isbn_10": "123456789X",
            "isbn_13": "9781234567890",
            "book": {"id": 7},
        }
        client = RecordingHardcoverClient([{"editions": [edition]}])

        await client.prefetch_editions_by_isbns([
            "123456789X",
            "9781234567890",
            "9780000000000",
        ])

        self.assertEqual(len(client.calls), 1)
        query, variables, _ = client.calls[0]
        self.assertEqual(query.count("\n      editions("), 1)
        self.assertIn("_in", query)
        self.assertEqual(variables["isbn_10s"], ["123456789X"])
        self.assertCountEqual(
            variables["isbn_13s"],
            ["9781234567890", "9780000000000"],
        )
        self.assertEqual(client.cached_edition_by_isbn("9781234567890"), (True, edition))
        self.assertEqual(client.cached_edition_by_isbn("9780000000000"), (True, None))

    async def test_pat_identity_resolves_from_viewer_query(self):
        client = RecordingHardcoverClient([{
            "me": {"id": 123, "username": "reader"},
        }])

        user_id = await client.resolve_identity()

        self.assertEqual(user_id, 123)
        self.assertEqual(client.viewer_username, "reader")
        self.assertTrue(client.user_features_available)

    async def test_pat_without_viewer_scope_disables_user_features(self):
        client = RecordingHardcoverClient([
            HardcoverAPIError(
                "http_403: insufficient_scope",
                status_code=403,
                code="insufficient_scope",
                scope="read:me",
            ),
        ])

        self.assertIsNone(await client.resolve_identity())
        self.assertEqual(client.identity_state, "unavailable")
        self.assertFalse(client.user_features_available)
        with self.assertRaisesRegex(HardcoverAPIError, "identity_unavailable"):
            await client.create_user_book(42, status_id=1)


if __name__ == "__main__":
    unittest.main()
