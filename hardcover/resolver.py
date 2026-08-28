import copy
import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .client import HardcoverAPIError, HardcoverClient
from .normalization import (
    clean_title,
    detect_author_name,
    detect_series_names,
    extract_isbns,
    mam_original_metadata,
)
from .validator import pick_valid_candidate


logger = logging.getLogger(__name__)


@dataclass
class HardcoverEnrichmentConfig:
    match_threshold: float = 78.0
    concurrency: int = 6
    per_page: int = 5


def _listify(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [str(v) for v in value.values() if v]
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value if v]
    return [str(value)]


def _unique_list(value: Any) -> list[str]:
    seen: set[str] = set()
    items: list[str] = []
    for raw in _listify(value):
        text = str(raw).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _extract_image_url(image: Any) -> str:
    if not image:
        return ""
    if isinstance(image, str):
        return image
    if isinstance(image, dict):
        for key in ("url", "image_url", "large", "medium", "small", "original"):
            value = image.get(key)
            if isinstance(value, str) and value:
                return value
        for value in image.values():
            if isinstance(value, str) and value.startswith("http"):
                return value
    if isinstance(image, list):
        for item in image:
            value = _extract_image_url(item)
            if value:
                return value
    return ""


def _release_year(candidate: dict[str, Any]) -> int | None:
    raw = candidate.get("release_year") or candidate.get("release_date_i") or candidate.get("release_date")
    if raw is None:
        return None
    text = str(raw)
    if len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return None


def _author_names_and_slugs(contributions: Any) -> tuple[list[str], list[str]]:
    names: list[str] = []
    slugs: list[str] = []
    seen: set[tuple[str, str]] = set()

    if not isinstance(contributions, list):
        return names, slugs

    for item in contributions:
        if not isinstance(item, dict):
            continue
        author = item.get("author") or {}
        if not isinstance(author, dict):
            continue
        name = str(author.get("name") or "").strip()
        slug = str(author.get("slug") or "").strip()
        if not name:
            continue
        key = (name, slug)
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
        slugs.append(slug)

    return names, slugs


def _positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _non_negative_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _normalize_featured_series(featured: Any) -> dict[str, Any] | None:
    if not isinstance(featured, dict):
        return None

    series = featured.get("series")
    name = ""
    slug = ""
    if isinstance(series, dict):
        series_id = _positive_int(series.get("id"))
        name = str(series.get("name") or "").strip()
        slug = str(series.get("slug") or "").strip()
    else:
        series_id = None

    position = _non_negative_float(featured.get("position"))
    if position is None:
        position = _non_negative_float(featured.get("details"))

    if not name and position is None:
        return None
    return {
        "id": series_id,
        "name": name,
        "slug": slug,
        "position": position,
    }


def _normalize_user_book(user_books: Any) -> dict[str, Any] | None:
    if isinstance(user_books, dict):
        user_books = [user_books]
    if not isinstance(user_books, list):
        return None

    for item in user_books:
        if not isinstance(item, dict):
            continue
        try:
            user_book_id = int(item.get("id"))
            status_id = int(item.get("status_id"))
        except (TypeError, ValueError):
            continue
        if user_book_id <= 0 or status_id <= 0:
            continue

        privacy_setting_id = _positive_int(item.get("privacy_setting_id"))
        edition_id = _positive_int(item.get("edition_id"))
        rating = _non_negative_float(item.get("rating"))
        status_obj = item.get("user_book_status") or {}
        status_label = str(status_obj.get("status") or "").strip() if isinstance(status_obj, dict) else ""
        return {
            "id": user_book_id,
            "book_id": _positive_int(item.get("book_id")),
            "edition_id": edition_id,
            "user_id": _positive_int(item.get("user_id")),
            "status_id": status_id,
            "status": status_label,
            "privacy_setting_id": privacy_setting_id if privacy_setting_id is not None else 1,
            "rating": rating,
            "updated_at": str(item.get("updated_at") or "").strip(),
        }
    return None


def metadata_from_search_candidate(candidate: dict[str, Any], query_type: str) -> dict[str, Any]:
    is_book = query_type.lower() == "book"
    is_series = query_type.lower() == "series"
    is_author = query_type.lower() == "author"
    title = candidate.get("title") if is_book else candidate.get("name")
    contribution_authors, contribution_author_slugs = _author_names_and_slugs(candidate.get("contributions"))
    authors = contribution_authors or _listify(candidate.get("author_names"))
    if not authors and candidate.get("author_name"):
        authors = [str(candidate.get("author_name"))]
    featured_series = _normalize_featured_series(candidate.get("featured_series") or candidate.get("featured_book_series"))
    url_path = "books"
    if is_series:
        url_path = "series"
    elif is_author:
        url_path = "authors"

    return {
        "title": title or "",
        "authors": authors,
        "author_slugs": contribution_author_slugs if contribution_authors else [],
        "cover_image": _extract_image_url(candidate.get("image")),
        "subtitle": candidate.get("subtitle") or "",
        "description": candidate.get("description") or "",
        "rating": candidate.get("rating"),
        "ratings_count": candidate.get("ratings_count"),
        "reviews_count": _positive_int(candidate.get("reviews_count")),
        "users_read_count": _positive_int(candidate.get("users_read_count")),
        "users_count": _positive_int(candidate.get("users_count")),
        "release_date": candidate.get("release_date") or "",
        "release_year": _release_year(candidate),
        "pages": _positive_int(candidate.get("pages")),
        "slug": candidate.get("slug") or "",
        "book_id": candidate.get("id") if is_book else None,
        "series_id": candidate.get("id") if is_series else (featured_series or {}).get("id"),
        "series_names": _unique_list(candidate.get("series_names")) if is_book else ([title] if is_series and title else []),
        "featured_series": featured_series,
        "user_book": _normalize_user_book(candidate.get("user_books")),
        "user_book_known": isinstance(candidate.get("user_books"), list),
        "genres": _unique_list(candidate.get("genres")),
        "moods": _unique_list(candidate.get("moods")),
        "has_audiobook": bool(candidate.get("has_audiobook")),
        "has_ebook": bool(candidate.get("has_ebook")),
        "compilation": bool(candidate.get("compilation")) if is_book else False,
        "object_type": candidate.get("object_type") or query_type,
        "url_path": url_path,
    }


def metadata_from_edition(edition: dict[str, Any], original: dict[str, Any] | None = None) -> dict[str, Any]:
    book = edition.get("book") or {}
    original = original or {}
    contribution_authors, contribution_author_slugs = _author_names_and_slugs(book.get("contributions"))
    authors = contribution_authors or _unique_list(book.get("author_names"))
    if not authors and original.get("author_info"):
        authors = [name.strip() for name in str(original.get("author_info")).split(",") if name.strip()]
    series_names = _unique_list(book.get("series_names"))
    if not series_names and original.get("series_info"):
        series_names = [name.strip() for name in str(original.get("series_info")).split(",") if name.strip()]
    featured_series = _normalize_featured_series(book.get("featured_series") or book.get("featured_book_series"))
    return {
        "title": book.get("title") or edition.get("title") or "",
        "authors": authors,
        "author_slugs": contribution_author_slugs if contribution_authors else [],
        "cover_image": _extract_image_url(book.get("image")),
        "subtitle": book.get("subtitle") or "",
        "description": book.get("description") or "",
        "rating": book.get("rating"),
        "ratings_count": book.get("ratings_count"),
        "reviews_count": _positive_int(book.get("reviews_count")),
        "users_read_count": _positive_int(book.get("users_read_count")),
        "users_count": _positive_int(book.get("users_count")),
        "release_date": book.get("release_date") or edition.get("release_date") or "",
        "release_year": _release_year(book) or _release_year(edition),
        "pages": _positive_int(book.get("pages")),
        "slug": book.get("slug") or "",
        "book_id": book.get("id"),
        "series_id": (featured_series or {}).get("id"),
        "series_names": series_names,
        "featured_series": featured_series,
        "user_book": _normalize_user_book(book.get("user_books")),
        "user_book_known": isinstance(book.get("user_books"), list),
        "genres": _unique_list(book.get("genres")),
        "moods": _unique_list(book.get("moods")),
        "has_audiobook": bool(book.get("has_audiobook")),
        "has_ebook": bool(book.get("has_ebook")),
        "compilation": bool(book.get("compilation")),
        "object_type": "Book",
        "url_path": "books",
    }


def metadata_from_book(book: dict[str, Any], fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = fallback or {}
    contribution_authors, contribution_author_slugs = _author_names_and_slugs(book.get("contributions"))
    featured_series = _normalize_featured_series(book.get("featured_series") or book.get("featured_book_series"))
    return {
        "title": book.get("title") or "",
        "authors": contribution_authors or _listify(fallback.get("author_names")),
        "author_slugs": contribution_author_slugs if contribution_authors else [],
        "cover_image": _extract_image_url(book.get("image")),
        "subtitle": book.get("subtitle") or "",
        "description": book.get("description") or "",
        "rating": book.get("rating"),
        "ratings_count": book.get("ratings_count"),
        "reviews_count": _positive_int(book.get("reviews_count")),
        "users_read_count": _positive_int(book.get("users_read_count")),
        "users_count": _positive_int(book.get("users_count")),
        "release_date": book.get("release_date") or "",
        "release_year": _release_year(book),
        "pages": _positive_int(book.get("pages")),
        "slug": book.get("slug") or "",
        "book_id": book.get("id"),
        "series_id": (featured_series or {}).get("id"),
        "series_names": _unique_list(book.get("series_names") or fallback.get("series_names")),
        "featured_series": featured_series,
        "user_book": _normalize_user_book(book.get("user_books")),
        "user_book_known": isinstance(book.get("user_books"), list),
        "genres": _unique_list(book.get("genres") or fallback.get("genres")),
        "moods": _unique_list(book.get("moods") or fallback.get("moods")),
        "has_audiobook": bool(book.get("has_audiobook") or fallback.get("has_audiobook")),
        "has_ebook": bool(book.get("has_ebook") or fallback.get("has_ebook")),
        "compilation": bool(book.get("compilation")),
        "object_type": "Book",
        "url_path": "books",
    }


def _weighted_series_rating(series: dict[str, Any]) -> tuple[float | None, int | None]:
    if not isinstance(series, dict):
        return None, None

    weighted_total = 0.0
    total_ratings = 0
    rated_books = 0
    for entry in series.get("book_series") or []:
        if not isinstance(entry, dict):
            continue
        book = entry.get("book") or {}
        if not isinstance(book, dict):
            continue
        try:
            rating = float(book.get("rating"))
        except (TypeError, ValueError):
            continue
        ratings_count = _positive_int(book.get("ratings_count"))
        if ratings_count is None or ratings_count <= 0 or rating <= 0:
            continue
        weighted_total += rating * ratings_count
        total_ratings += ratings_count
        rated_books += 1

    if total_ratings <= 0 or rated_books <= 0:
        return None, None
    average_ratings_count = max(1, round(total_ratings / rated_books))
    return round(weighted_total / total_ratings, 4), average_ratings_count


def _series_publication_range(series: dict[str, Any]) -> str:
    if not isinstance(series, dict):
        return ""

    points: list[tuple[str, str]] = []
    for entry in series.get("book_series") or []:
        if not isinstance(entry, dict):
            continue
        book = entry.get("book") or {}
        if not isinstance(book, dict):
            continue

        release_date = str(book.get("release_date") or "").strip()
        if release_date:
            points.append((release_date, release_date))
            continue

        release_year = _positive_int(book.get("release_year"))
        if release_year is not None:
            year_text = str(release_year)
            points.append((f"{release_year:04d}", year_text))

    if not points:
        return ""

    points.sort(key=lambda item: item[0])
    start = points[0][1]
    end = points[-1][1]
    return start if start == end else f"{start} to {end}"


def _series_max_readers(series: dict[str, Any]) -> int | None:
    if not isinstance(series, dict):
        return None

    max_readers: int | None = None
    for entry in series.get("book_series") or []:
        if not isinstance(entry, dict):
            continue
        book = entry.get("book") or {}
        if not isinstance(book, dict):
            continue

        readers = _positive_int(book.get("users_read_count"))
        if readers is None:
            readers = _positive_int(book.get("users_count"))
        if readers is None:
            continue
        if max_readers is None or readers > max_readers:
            max_readers = readers

    return max_readers


def _resolution_key_text(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"&", " and ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clone_enrichment_for_result(result: dict[str, Any], enrichment: dict[str, Any]) -> dict[str, Any]:
    cloned = copy.deepcopy(enrichment)
    cloned["original_mam"] = mam_original_metadata(result)
    cloned["cleaned_query"] = clean_title(result.get("title"))
    return cloned


class HardcoverResolver:
    def __init__(self, client: HardcoverClient, config: HardcoverEnrichmentConfig):
        self.client = client
        self.config = config

    def unresolved(self, result: dict[str, Any], cleaned_query: str, reason: str, path: str = "unresolved") -> dict[str, Any]:
        return {
            "original_mam": mam_original_metadata(result),
            "hardcover": None,
            "match_score": 0.0,
            "query_path": path,
            "failure_reason": reason,
            "cleaned_query": cleaned_query,
        }

    def dedupe_key(self, result: dict[str, Any]) -> str | None:
        isbn_values = sorted({str(isbn or "").strip().upper() for isbn in extract_isbns(result) if str(isbn or "").strip()})
        if isbn_values:
            return f"isbn:{'|'.join(isbn_values)}"

        cleaned_query = _resolution_key_text(clean_title(result.get("title")))
        author_name = _resolution_key_text(detect_author_name(result))

        if not cleaned_query and not author_name:
            return None
        return f"query:{cleaned_query}|author:{author_name}"

    async def enrich_result(self, result: dict[str, Any]) -> dict[str, Any]:
        cleaned_query = clean_title(result.get("title"))
        original = mam_original_metadata(result)
        author_name = detect_author_name(result)
        series_names = detect_series_names(result)
        isbn_failure_reason = ""

        try:
            for isbn in extract_isbns(result):
                try:
                    edition = await self.client.edition_by_isbn(isbn)
                except HardcoverAPIError as exc:
                    isbn_failure_reason = str(exc)
                    continue
                if edition:
                    return {
                        "original_mam": original,
                        "hardcover": metadata_from_edition(edition, original),
                        "match_score": 100.0,
                        "query_path": "ISBN",
                        "failure_reason": "",
                        "cleaned_query": cleaned_query,
                    }

            if not cleaned_query:
                return self.unresolved(result, cleaned_query, "missing_clean_title")

            book_candidates = await self.client.search(cleaned_query, "Book", self.config.per_page)
            candidate, score, _ = pick_valid_candidate(
                cleaned_query,
                book_candidates,
                self.config.match_threshold,
                author_name=author_name,
                series_names=series_names,
            )
            if candidate:
                return {
                    "original_mam": original,
                    "hardcover": metadata_from_search_candidate(candidate, "Book"),
                    "match_score": score,
                    "query_path": "book",
                    "failure_reason": "",
                    "cleaned_query": cleaned_query,
                }

            best_score = score
            series_queries = [cleaned_query] + [
                name for name in series_names
                if name.lower() != cleaned_query.lower()
            ]
            for series_query in series_queries:
                series_candidates = await self.client.search(series_query, "Series", self.config.per_page)
                candidate, score, _ = pick_valid_candidate(
                    series_query,
                    series_candidates,
                    self.config.match_threshold,
                    author_name=author_name,
                    series_names=series_names,
                )
                if candidate:
                    metadata = metadata_from_search_candidate(candidate, "Series")
                    try:
                        series_details = await self.client.series_details(candidate.get("id"))
                    except HardcoverAPIError:
                        series_details = None
                    weighted_rating, weighted_ratings_count = _weighted_series_rating(series_details or {})
                    if weighted_rating is not None and weighted_ratings_count is not None:
                        metadata["rating"] = weighted_rating
                        metadata["ratings_count"] = weighted_ratings_count
                    publication_range = _series_publication_range(series_details or {})
                    if publication_range:
                        metadata["release_date"] = publication_range
                    max_readers = _series_max_readers(series_details or {})
                    if max_readers is not None:
                        metadata["users_read_count"] = max_readers
                    return {
                        "original_mam": original,
                        "hardcover": metadata,
                        "match_score": score,
                        "query_path": "series",
                        "failure_reason": "",
                        "cleaned_query": cleaned_query,
                    }
                best_score = max(best_score, score)

            if author_name:
                author_candidates = await self.client.search(author_name, "Author", self.config.per_page)
                candidate, score, _ = pick_valid_candidate(
                    author_name,
                    author_candidates,
                    self.config.match_threshold,
                    author_name=author_name,
                    series_names=series_names,
                )
                if candidate:
                    metadata = metadata_from_search_candidate(candidate, "Author")
                    metadata["authors"] = [metadata.get("title") or author_name]
                    return {
                        "original_mam": original,
                        "hardcover": metadata,
                        "match_score": score,
                        "query_path": "author",
                        "failure_reason": "",
                        "cleaned_query": cleaned_query,
                    }
                best_score = max(best_score, score)

            failure = "low_confidence" if best_score else "no_match"
            if isbn_failure_reason:
                failure = f"{failure}; isbn_lookup_error: {isbn_failure_reason}"
            unresolved = self.unresolved(result, cleaned_query, failure)
            unresolved["match_score"] = best_score
            return unresolved
        except HardcoverAPIError as exc:
            return self.unresolved(result, cleaned_query, str(exc), "failed")
        except Exception as exc:
            return self.unresolved(result, cleaned_query, f"unexpected_error: {exc}", "failed")


class HardcoverBatchRunner:
    def __init__(self, resolver: HardcoverResolver, concurrency: int):
        self.resolver = resolver
        self.concurrency = max(1, int(concurrency))

    async def run(
        self,
        results: list[dict[str, Any]],
        on_result: Callable[[int, dict[str, Any], dict[str, Any]], Awaitable[None]],
        *,
        shared_cache: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        semaphore = asyncio.Semaphore(self.concurrency)
        shared_cache = shared_cache if shared_cache is not None else {}
        grouped_results: dict[str, list[tuple[int, dict[str, Any]]]] = {}

        for index, result in enumerate(results):
            dedupe_key = self.resolver.dedupe_key(result)
            group_key = dedupe_key or f"row:{index}"
            grouped_results.setdefault(group_key, []).append((index, result))

        primary_results = [items[0][1] for items in grouped_results.values()]
        await self.resolver.client.resolve_identity()
        await self._prime_client_cache(primary_results)

        async def worker(group_key: str, items: list[tuple[int, dict[str, Any]]]) -> list[tuple[int, dict[str, Any], dict[str, Any]]]:
            async with semaphore:
                base_enrichment = shared_cache.get(group_key)
                if base_enrichment is None:
                    primary_result = items[0][1]
                    base_enrichment = await self.resolver.enrich_result(primary_result)
                    if not group_key.startswith("row:"):
                        shared_cache[group_key] = copy.deepcopy(base_enrichment)
                return [
                    (index, result, clone_enrichment_for_result(result, base_enrichment))
                    for index, result in items
                ]

        tasks = [
            asyncio.create_task(worker(group_key, items))
            for group_key, items in grouped_results.items()
        ]
        grouped_enrichments = await asyncio.gather(*tasks)
        entries = [
            item
            for group in grouped_enrichments
            for item in group
        ]
        await self._hydrate_user_books(entries)
        for index, result, enrichment in sorted(entries, key=lambda item: int(item[0])):
            await on_result(index, result, enrichment)

    async def _prime_client_cache(self, results: list[dict[str, Any]]) -> None:
        if not results:
            return

        isbns: list[str] = []
        for result in results:
            isbns.extend(extract_isbns(result))

        if isbns:
            try:
                await self.resolver.client.prefetch_editions_by_isbns(isbns)
            except HardcoverAPIError as exc:
                logger.warning("[HARDCOVER] ISBN prefetch failed; falling back per item: %s", exc)

        searches: list[tuple[str, str, int]] = []
        for result in results:
            result_isbns = extract_isbns(result)
            cached_isbns = [
                self.resolver.client.cached_edition_by_isbn(isbn)
                for isbn in result_isbns
            ]
            if any(known and edition is not None for known, edition in cached_isbns):
                continue
            # Unknown ISBNs indicate a failed/partial prefetch. Let the normal
            # per-item ISBN-first resolver retry before spending a search call.
            if cached_isbns and not all(known for known, _ in cached_isbns):
                continue

            cleaned_query = clean_title(result.get("title"))
            if cleaned_query:
                searches.append((cleaned_query, "Book", self.resolver.config.per_page))

        if searches:
            try:
                await self.resolver.client.prefetch_searches(searches)
            except HardcoverAPIError as exc:
                logger.warning("[HARDCOVER] Search prefetch failed; falling back per item: %s", exc)

    async def _hydrate_user_books(self, entries: list[tuple[int, dict[str, Any], dict[str, Any]]]) -> None:
        book_metadata = []
        for _, _, enrichment in entries:
            metadata = enrichment.get("hardcover") or {}
            if isinstance(metadata, dict) and str(metadata.get("object_type") or "").strip().lower() == "book":
                book_metadata.append(metadata)

        if not book_metadata:
            return

        await self.resolver.client.resolve_identity()
        if not self.resolver.client.user_features_available:
            for metadata in book_metadata:
                metadata["user_actions_available"] = False
            return

        for metadata in book_metadata:
            metadata["user_actions_available"] = True

        book_ids: list[int] = []
        seen_ids: set[int] = set()
        for _, _, enrichment in entries:
            metadata = enrichment.get("hardcover") or {}
            if not isinstance(metadata, dict):
                continue
            if str(metadata.get("object_type") or "").strip().lower() != "book":
                continue
            if metadata.get("user_book_known") is True:
                continue
            try:
                book_id = int(metadata.get("book_id"))
            except (TypeError, ValueError):
                continue
            if book_id <= 0 or book_id in seen_ids:
                continue
            seen_ids.add(book_id)
            book_ids.append(book_id)

        if not book_ids:
            return

        try:
            user_books = await self.resolver.client.user_books_for_books(book_ids)
        except HardcoverAPIError as exc:
            logger.warning("[HARDCOVER] User library hydration unavailable: %s", exc)
            for metadata in book_metadata:
                metadata["user_actions_available"] = False
            return

        for _, _, enrichment in entries:
            metadata = enrichment.get("hardcover") or {}
            if not isinstance(metadata, dict):
                continue
            if str(metadata.get("object_type") or "").strip().lower() != "book":
                continue
            if metadata.get("user_book_known") is True:
                continue
            try:
                book_id = int(metadata.get("book_id"))
            except (TypeError, ValueError):
                continue
            if book_id <= 0:
                continue
            metadata["user_book"] = _normalize_user_book(user_books.get(book_id))
            metadata["user_book_known"] = True
