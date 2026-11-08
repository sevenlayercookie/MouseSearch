import types
import unittest

from hardcover.client import HardcoverAPIError
from hardcover.resolver import HardcoverBatchRunner, HardcoverEnrichmentConfig, HardcoverResolver


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

class TitleAuthorRetryClient:
    """Title-only search misses the book; title + author finds it."""

    def __init__(self):
        self.searches = []

    async def search(self, query, query_type="Book", per_page=5):
        self.searches.append((query, query_type))
        if query_type != "Book":
            return []
        if query == "Passengers":
            return [{"id": 1, "title": "The Passengers", "author_names": ["John Marrs"], "release_year": 2019}]
        if query == "Passengers Robert Silverberg":
            return [{"id": 2, "title": "Passengers", "author_names": ["Robert Silverberg"], "release_year": 1968}]
        return []


class HardcoverResolverBookSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_api_error_preserves_series_and_author_fallbacks(self):
        for fallback_type in ("Series", "Author"):
            for error in ("timeout: retry timed out", "http_429: rate limit exceeded"):
                with self.subTest(fallback_type=fallback_type, error=error):
                    class Client(TitleAuthorRetryClient):
                        async def search(self, query, query_type="Book", per_page=5):
                            if query == "Passengers Robert Silverberg":
                                self.searches.append((query, query_type))
                                raise HardcoverAPIError(error)
                            if query_type == fallback_type:
                                self.searches.append((query, query_type))
                                return [{"id": 3, "name": query}]
                            return await super().search(query, query_type, per_page)

                        async def series_details(self, series_id):
                            return {}

                    client = Client()
                    resolver = HardcoverResolver(client, HardcoverEnrichmentConfig())
                    enrichment = await resolver.enrich_result({
                        "title": "Passengers",
                        "author_info": '{"1": "Robert Silverberg"}',
                    })
                    self.assertEqual(enrichment["query_path"], fallback_type.lower())
                    self.assertEqual(enrichment["failure_reason"], "")
                    expected = [("Passengers", "Book"), ("Passengers Robert Silverberg", "Book"), ("Passengers", "Series")]
                    if fallback_type == "Author":
                        expected.append(("Robert Silverberg", "Author"))
                    self.assertEqual(client.searches, expected)

    async def test_retries_book_search_with_author_before_falling_back(self):
        client = TitleAuthorRetryClient()
        resolver = HardcoverResolver(client, HardcoverEnrichmentConfig(match_threshold=78, concurrency=1, per_page=5))

        enrichment = await resolver.enrich_result({
            "id": "1273969",
            "title": "Passengers",
            "author_info": '{"1": "Robert Silverberg"}',
        })

        self.assertEqual(enrichment["query_path"], "book")
        self.assertEqual(enrichment["hardcover"]["book_id"], 2)
        self.assertEqual(enrichment["hardcover"]["release_year"], 1968)
        self.assertEqual(
            client.searches,
            [("Passengers", "Book"), ("Passengers Robert Silverberg", "Book")],
        )


if __name__ == "__main__":
    unittest.main()
