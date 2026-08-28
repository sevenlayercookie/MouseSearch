import types
import unittest

from hardcover.client import HardcoverAPIError
from hardcover.resolver import HardcoverBatchRunner


class PrefetchFailureClient:
    user_features_available = False

    async def resolve_identity(self):
        return None

    async def prefetch_editions_by_isbns(self, isbns):
        raise HardcoverAPIError(
            "http_403: unsupported_operation",
            status_code=403,
            code="unsupported_operation",
        )

    def cached_edition_by_isbn(self, isbn):
        return False, None

    async def prefetch_searches(self, searches):
        raise AssertionError("Search prefetch should wait until the ISBN retry resolves")


class SearchPrefetchFailureClient:
    user_features_available = False

    async def resolve_identity(self):
        return None

    async def prefetch_editions_by_isbns(self, isbns):
        raise AssertionError("No ISBN prefetch was expected")

    async def prefetch_searches(self, searches):
        raise HardcoverAPIError(
            "http_403: search_query_limit_exceeded",
            status_code=403,
            code="search_query_limit_exceeded",
        )


class StubResolver:
    def __init__(self, client=None):
        self.client = client or PrefetchFailureClient()
        self.config = types.SimpleNamespace(per_page=5)

    def dedupe_key(self, result):
        return str(result.get("id"))

    async def enrich_result(self, result):
        return {"status": "resolved", "source_id": result["id"]}


class HardcoverBatchRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefetch_api_failure_falls_back_to_per_item_enrichment(self):
        resolver = StubResolver()
        runner = HardcoverBatchRunner(resolver, concurrency=2)
        published = []

        async def on_result(index, result, enrichment):
            published.append((index, result, enrichment))

        result = {
            "id": 1,
            "title": "A Book",
            "isbn": "9780306406157",
        }
        await runner.run([result], on_result)

        self.assertEqual(len(published), 1)
        self.assertEqual(published[0][2]["status"], "resolved")

    async def test_search_prefetch_403_falls_back_to_per_item_enrichment(self):
        resolver = StubResolver(SearchPrefetchFailureClient())
        runner = HardcoverBatchRunner(resolver, concurrency=2)
        published = []

        async def on_result(index, result, enrichment):
            published.append((index, result, enrichment))

        result = {"id": 2, "title": "A Book Without an ISBN"}
        await runner.run([result], on_result)

        self.assertEqual(len(published), 1)
        self.assertEqual(published[0][2]["status"], "resolved")


if __name__ == "__main__":
    unittest.main()
