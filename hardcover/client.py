import asyncio
import base64
import copy
import collections
import hashlib
import json
import logging
import re
import time
from datetime import date
from typing import Any

import httpx


logger = logging.getLogger(__name__)
_ISBN_PREFETCH_CHUNK_SIZE = 100
_CACHE_TTL_USER_LIBRARY = 10 * 60.0
_USER_LIBRARY_PAGE_SIZE = 500


class HardcoverAPIError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        scope: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.code = str(code or "").strip() or None
        self.scope = str(scope or "").strip() or None


def _parse_rate_limit_entries(value: str | None) -> list[tuple[str, dict[str, float]]]:
    entries: list[tuple[str, dict[str, float]]] = []
    for raw_entry in str(value or "").split(","):
        parts = [part.strip() for part in raw_entry.split(";") if part.strip()]
        if not parts:
            continue
        name = parts[0].strip('"').strip()
        params: dict[str, float] = {}
        for part in parts[1:]:
            key, separator, raw_number = part.partition("=")
            if not separator:
                continue
            try:
                params[key.strip().lower()] = float(raw_number.strip())
            except (TypeError, ValueError):
                continue
        entries.append((name, params))
    return entries


def parse_hardcover_rate_limit_headers(headers: Any) -> dict[str, float] | None:
    policy_entries = _parse_rate_limit_entries(headers.get("RateLimit-Policy"))
    policy = next((params for _, params in policy_entries if params.get("burst", 0) > 0), None)
    if not policy or policy.get("q", 0) <= 0 or policy.get("w", 0) <= 0:
        return None

    policy_name = next(
        (name for name, params in policy_entries if params is policy),
        "",
    )
    standing_entries = _parse_rate_limit_entries(headers.get("RateLimit"))
    standing = next(
        (params for name, params in standing_entries if name.lower() == policy_name.lower()),
        None,
    )
    if standing is None:
        standing = next((params for _, params in standing_entries if "r" in params), None)

    parsed = {
        "quota": policy["q"],
        "window_seconds": policy["w"],
        "burst": policy["burst"],
    }
    if standing and standing.get("r") is not None:
        parsed["remaining"] = max(0.0, standing["r"])
    return parsed


class AsyncTokenBucket:
    def __init__(self, limit: int, period_seconds: float):
        self.configured_limit = max(1, int(limit))
        self.period_seconds = float(period_seconds)
        self.refill_rate = float(self.configured_limit) / self.period_seconds
        # Bootstrap conservatively until Hardcover advertises this token's burst.
        self.capacity = 1.0
        self.tokens = 1.0
        self.updated_at = time.monotonic()
        self.policy_loaded = False
        self.condition = asyncio.Condition()

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self.updated_at)
        self.updated_at = now
        self.tokens = min(self.capacity, self.tokens + (elapsed * self.refill_rate))

    async def acquire(self) -> float:
        started_at = time.monotonic()
        async with self.condition:
            while True:
                self._refill(time.monotonic())
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return max(0.0, time.monotonic() - started_at)

                wait_for = (1.0 - self.tokens) / max(self.refill_rate, 1e-9)
                try:
                    await asyncio.wait_for(self.condition.wait(), timeout=wait_for)
                except asyncio.TimeoutError:
                    pass

    async def update_policy(
        self,
        *,
        quota: float,
        window_seconds: float,
        burst: float,
        remaining: float | None = None,
    ) -> None:
        if quota <= 0 or window_seconds <= 0 or burst <= 0:
            return
        async with self.condition:
            self._refill(time.monotonic())
            first_policy = not self.policy_loaded
            configured_rate = float(self.configured_limit) / self.period_seconds
            advertised_rate = float(quota) / float(window_seconds)
            self.refill_rate = min(configured_rate, advertised_rate)
            self.capacity = max(1.0, float(burst))
            if remaining is not None:
                reported_remaining = min(self.capacity, max(0.0, float(remaining)))
                self.tokens = reported_remaining if first_policy else min(self.tokens, reported_remaining)
            else:
                # Do not mint burst tokens when the server did not report its
                # current standing; newly available capacity fills naturally.
                self.tokens = min(self.tokens, self.capacity)
            self.policy_loaded = True
            self.condition.notify_all()


class HardcoverRateController:
    def __init__(self, limit: int, period_seconds: float):
        self.limit = max(1, int(limit))
        self.period_seconds = float(period_seconds)
        self.bucket = AsyncTokenBucket(self.limit, self.period_seconds)
        self.cooldown_until = 0.0
        self.cooldown_lock = asyncio.Lock()
        self.request_history: collections.deque[float] = collections.deque()
        self.history_lock = asyncio.Lock()

    async def _record_request(self) -> int:
        now = time.monotonic()
        cutoff = now - 60.0
        async with self.history_lock:
            self.request_history.append(now)
            while self.request_history and self.request_history[0] < cutoff:
                self.request_history.popleft()
            return len(self.request_history)

    async def current_requests_per_minute(self) -> int:
        now = time.monotonic()
        cutoff = now - 60.0
        async with self.history_lock:
            while self.request_history and self.request_history[0] < cutoff:
                self.request_history.popleft()
            return len(self.request_history)

    async def acquire(self) -> float:
        waited_total = 0.0
        while True:
            async with self.cooldown_lock:
                wait_for = self.cooldown_until - time.monotonic()
            if wait_for > 0:
                waited_total += wait_for
                await asyncio.sleep(wait_for)
                continue

            waited_total += await self.bucket.acquire()

            async with self.cooldown_lock:
                wait_for = self.cooldown_until - time.monotonic()
            if wait_for <= 0:
                await self._record_request()
                return waited_total

    async def update_from_headers(self, headers: Any) -> None:
        policy = parse_hardcover_rate_limit_headers(headers)
        if not policy:
            return
        await self.bucket.update_policy(
            quota=policy["quota"],
            window_seconds=policy["window_seconds"],
            burst=policy["burst"],
            remaining=policy.get("remaining"),
        )

    async def note_rate_limited(self, attempt: int, retry_after: str | None = None) -> float:
        delay_seconds = self._retry_delay_seconds(attempt, retry_after)
        async with self.cooldown_lock:
            self.cooldown_until = max(self.cooldown_until, time.monotonic() + delay_seconds)
        return delay_seconds

    def _retry_delay_seconds(self, attempt: int, retry_after: str | None) -> float:
        if retry_after is not None:
            try:
                parsed = float(str(retry_after).strip())
            except (TypeError, ValueError):
                parsed = None
            if parsed is not None and parsed > 0:
                return parsed
        return min(15.0, 1.0 * (2 ** max(0, int(attempt))))


_HARDCOVER_RATE_CONTROLLERS: dict[str, HardcoverRateController] = {}


def get_hardcover_rate_controller(endpoint: str, authorization_header: str, limit: int) -> HardcoverRateController:
    scope = f"{str(endpoint or '').strip().lower()}:{hashlib.sha256(str(authorization_header or '').encode('utf-8')).hexdigest()}"
    controller = _HARDCOVER_RATE_CONTROLLERS.get(scope)
    if controller is None or controller.limit != max(1, int(limit)):
        controller = HardcoverRateController(limit, 60.0)
        _HARDCOVER_RATE_CONTROLLERS[scope] = controller
    return controller


def normalize_search_results(results: Any) -> list[dict[str, Any]]:
    if isinstance(results, dict):
        for key in ("hits", "results", "documents"):
            nested = results.get(key)
            if isinstance(nested, list):
                results = nested
                break
        else:
            results = [results]

    if not isinstance(results, list):
        return []

    normalized = []
    for item in results:
        candidate = item
        if isinstance(item, dict):
            if isinstance(item.get("document"), dict):
                candidate = item["document"]
            elif isinstance(item.get("book"), dict):
                candidate = item["book"]
            elif isinstance(item.get("series"), dict):
                candidate = item["series"]
            elif isinstance(item.get("author"), dict):
                candidate = item["author"]

        if isinstance(candidate, dict):
            normalized.append(candidate)
    return normalized


def graphql_operation_name(query: str) -> str:
    match = re.search(r"\b(query|mutation)\s+([A-Za-z0-9_]+)", str(query or ""))
    if match:
        return match.group(2)
    return "anonymous"


def graphql_operation_type(query: str) -> str:
    match = re.search(r"\b(query|mutation|subscription)\b", str(query or ""))
    if match:
        return match.group(1).lower()
    return "query"


def graphql_inflight_key(query: str, variables: dict[str, Any], cache_key: str | None = None) -> str | None:
    operation_type = graphql_operation_type(query)
    if operation_type != "query":
        return None
    if cache_key:
        return f"cache:{cache_key}"
    try:
        normalized_variables = json.dumps(variables, sort_keys=True, separators=(",", ":"), default=str)
    except TypeError:
        normalized_variables = repr(variables)
    digest = hashlib.sha256(f"{str(query or '').strip()}::{normalized_variables}".encode("utf-8")).hexdigest()
    return f"query:{digest}"


def _consume_future_exception(future: asyncio.Future[Any]) -> None:
    if not future.cancelled():
        future.exception()


def _hardcover_error_code(value: Any) -> str | None:
    candidate = str(value or "").strip()
    if re.fullmatch(r"[a-z][a-z0-9_]+", candidate):
        return candidate
    return None


def hardcover_api_error_from_response(response: Any) -> HardcoverAPIError:
    try:
        payload = response.json()
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    first_error: Any = None
    errors = payload.get("errors")
    if isinstance(errors, list) and errors:
        first_error = errors[0]

    code = str(payload.get("error") or "").strip()
    detail = str(payload.get("error_description") or payload.get("message") or "").strip()
    scope = str(payload.get("scope") or "").strip()
    if isinstance(first_error, dict):
        extensions = first_error.get("extensions") or {}
        if not code:
            code = str(first_error.get("code") or (extensions.get("code") if isinstance(extensions, dict) else "") or "").strip()
        if not detail:
            detail = str(first_error.get("message") or "").strip()
        if not scope and isinstance(extensions, dict):
            scope = str(extensions.get("scope") or "").strip()
    elif first_error is not None and not detail:
        detail = str(first_error).strip()
    if not code:
        code = _hardcover_error_code(detail) or ""

    status_code = int(response.status_code)
    parts = [f"http_{status_code}"]
    if code:
        parts.append(code)
    if detail and detail.lower() != code.lower():
        parts.append(detail)
    if scope:
        parts.append(f"required_scope={scope}")
    return HardcoverAPIError(
        ": ".join(parts),
        status_code=status_code,
        code=code or None,
        scope=scope or None,
    )


class HardcoverClient:
    USER_BOOK_CORE_FIELDS = """
        id
        book_id
        edition_id
        user_id
        status_id
        rating
        privacy_setting_id
        updated_at
        user_book_status {
          id
          status
        }
    """

    SEARCH_FIELDS = """
        ids
        results
        query
        query_type
        page
        per_page
    """

    # Hardcover's roadmap mentions a future maximum query depth of three.
    # These nested projections will need splitting if that policy is activated.
    BOOK_FIELDS = """
      id
      title
      subtitle
      description
      slug
      contributions(limit: 5) {
        author {
          name
          slug
        }
      }
      rating
      ratings_count
      reviews_count
      users_read_count
      users_count
      release_date
      release_year
      pages
      compilation
      image {
        url
      }
      featured_book_series {
        position
        series {
          id
          name
          slug
        }
      }
    """

    USER_BOOK_FIELDS = """
      user_books(
        where: {user_id: {_eq: $user_id}}
        order_by: {updated_at: desc}
        limit: 1
      ) {
""" + USER_BOOK_CORE_FIELDS + """
      }
    """

    SEARCH_QUERY = """
    query HardcoverSearch($query: String!, $query_type: String!, $per_page: Int!, $page: Int!) {
      search(query: $query, query_type: $query_type, per_page: $per_page, page: $page) {
""" + SEARCH_FIELDS + """
      }
    }
    """

    VIEWER_QUERY = """
    query HardcoverViewer {
      me {
        id
        username
      }
    }
    """

    EDITIONS_BY_ISBNS_QUERY = """
    query EditionsByISBNs($isbn_10s: [String!]!, $isbn_13s: [String!]!) {
      editions(where: {
        _or: [
          {isbn_10: {_in: $isbn_10s}}
          {isbn_13: {_in: $isbn_13s}}
        ]
      }) {
        id
        title
        isbn_10
        isbn_13
        release_date
        book {
""" + BOOK_FIELDS + """
        }
      }
    }
    """

    EDITIONS_BY_ISBNS_QUERY_WITH_USER = """
    query EditionsByISBNs($isbn_10s: [String!]!, $isbn_13s: [String!]!, $user_id: Int!) {
      editions(where: {
        _or: [
          {isbn_10: {_in: $isbn_10s}}
          {isbn_13: {_in: $isbn_13s}}
        ]
      }) {
        id
        title
        isbn_10
        isbn_13
        release_date
        book {
""" + BOOK_FIELDS + USER_BOOK_FIELDS + """
        }
      }
    }
    """

    EDITION_BY_ISBN_13_QUERY = """
    query EditionByISBN13($isbn: String!) {
      editions(where: {isbn_13: {_eq: $isbn}}, limit: 1) {
        id
        title
        isbn_10
        isbn_13
        release_date
        book {
""" + BOOK_FIELDS + """
        }
      }
    }
    """

    EDITION_BY_ISBN_13_QUERY_WITH_USER = """
    query EditionByISBN13($isbn: String!, $user_id: Int!) {
      editions(where: {isbn_13: {_eq: $isbn}}, limit: 1) {
        id
        title
        isbn_10
        isbn_13
        release_date
        book {
""" + BOOK_FIELDS + USER_BOOK_FIELDS + """
        }
      }
    }
    """

    EDITION_BY_ISBN_10_QUERY = """
    query EditionByISBN10($isbn: String!) {
      editions(where: {isbn_10: {_eq: $isbn}}, limit: 1) {
        id
        title
        isbn_10
        isbn_13
        release_date
        book {
""" + BOOK_FIELDS + """
        }
      }
    }
    """

    EDITION_BY_ISBN_10_QUERY_WITH_USER = """
    query EditionByISBN10($isbn: String!, $user_id: Int!) {
      editions(where: {isbn_10: {_eq: $isbn}}, limit: 1) {
        id
        title
        isbn_10
        isbn_13
        release_date
        book {
""" + BOOK_FIELDS + USER_BOOK_FIELDS + """
        }
      }
    }
    """

    BOOK_DETAILS_QUERY = """
    query BookDetails($id: Int!) {
      books(where: {id: {_eq: $id}}, limit: 1) {
""" + BOOK_FIELDS + """
      }
    }
    """

    BOOK_DETAILS_QUERY_WITH_USER = """
    query BookDetails($id: Int!, $user_id: Int!) {
      books(where: {id: {_eq: $id}}, limit: 1) {
""" + BOOK_FIELDS + USER_BOOK_FIELDS + """
      }
    }
    """

    USER_BOOK_FOR_BOOK_QUERY = """
    query UserBookForBook($book_id: Int!, $user_id: Int!) {
      user_books(
        where: {
          book_id: {_eq: $book_id}
          user_id: {_eq: $user_id}
        }
        order_by: {updated_at: desc}
        limit: 1
      ) {
""" + USER_BOOK_CORE_FIELDS + """
      }
    }
    """

    USER_BOOKS_FOR_BOOKS_QUERY = """
    query UserBooksForBooks($book_ids: [Int!]!, $user_id: Int!) {
      user_books(
        where: {
          book_id: {_in: $book_ids}
          user_id: {_eq: $user_id}
        }
        distinct_on: book_id
        order_by: [{book_id: asc}, {updated_at: desc}]
      ) {
""" + USER_BOOK_CORE_FIELDS + """
      }
    }
    """

    USER_LIBRARY_QUERY = """
    query UserLibrary($user_id: Int!, $limit: Int!, $offset: Int!) {
      user_books(
        where: {
          user_id: {_eq: $user_id}
        }
        distinct_on: book_id
        order_by: [{book_id: asc}, {updated_at: desc}]
        limit: $limit
        offset: $offset
      ) {
""" + USER_BOOK_CORE_FIELDS + """
      }
    }
    """

    SERIES_DETAILS_QUERY = """
    query SeriesDetails($id: Int!) {
      series(where: {id: {_eq: $id}}, limit: 1) {
        id
        name
        slug
        books_count
        author {
          name
          slug
        }
        book_series(
          distinct_on: position
          order_by: [{position: asc}, {book: {users_count: desc}}]
          where: {
            book: {canonical_id: {_is_null: true}}
            compilation: {_eq: false}
          }
        ) {
          position
          book {
            id
            slug
            title
            release_date
            release_year
            rating
            ratings_count
            users_read_count
            users_count
            image {
              url
            }
          }
        }
      }
    }
    """

    UPDATE_USER_BOOK_MUTATION = """
    mutation UpdateUserBook($id: Int!, $object: UserBookUpdateInput!) {
      updateResponse: update_user_book(id: $id, object: $object) {
        error
        userBook: user_book {
          id
          book_id
          edition_id
          user_id
          status_id
          rating
          privacy_setting_id
          updated_at
          user_book_status {
            id
            status
          }
        }
      }
    }
    """

    DELETE_USER_BOOK_MUTATION = """
    mutation DestroyUserBook($id: Int!) {
      deleteResponse: delete_user_book(id: $id) {
        id
        bookId: book_id
        userId: user_id
      }
    }
    """

    CREATE_USER_BOOK_MUTATION = """
    mutation CreateUserBook($object: UserBookCreateInput!) {
      createResponse: insert_user_book(object: $object) {
        error
        id
        userBook: user_book {
          id
          book_id
          edition_id
          user_id
          status_id
          rating
          privacy_setting_id
          updated_at
          user_book_status {
            id
            status
          }
        }
      }
    }
    """

    def __init__(
        self,
        token: str,
        *,
        endpoint: str = "https://api.hardcover.app/v1/graphql",
        user_agent: str = "MouseSearch Hardcover Enrichment",
        timeout_seconds: float = 30.0,
        rate_limit: int = 60,
    ):
        self.token = token
        self.endpoint = endpoint
        self.user_agent = user_agent
        self.timeout_seconds = timeout_seconds
        self.rate_limit_per_minute = max(1, int(rate_limit))
        self.user_id = self._extract_user_id(token)
        self.viewer_username: str | None = None
        self.identity_state = "resolved" if self.user_id else "unresolved"
        self.identity_error: str | None = None
        self._identity_lock = asyncio.Lock()
        self.rate_controller = get_hardcover_rate_controller(
            self.endpoint,
            self.authorization_header(),
            self.rate_limit_per_minute,
        )
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, Any] = {}
        self._inflight: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._inflight_lock = asyncio.Lock()
        self._user_book_map: dict[int, dict[str, Any]] | None = None
        self._user_book_map_expires_at = 0.0
        self._user_book_map_lock = asyncio.Lock()

    def authorization_header(self) -> str:
        token = str(self.token or "").strip()
        if token.lower().startswith("bearer "):
            return token
        return f"Bearer {token}"

    @staticmethod
    def _extract_user_id(token: str) -> int | None:
        raw = str(token or "").strip()
        if raw.lower().startswith("bearer "):
            raw = raw[7:].strip()
        parts = raw.split(".")
        if len(parts) < 2:
            return None

        payload_part = parts[1]
        payload_part += "=" * (-len(payload_part) % 4)
        try:
            payload = json.loads(base64.urlsafe_b64decode(payload_part.encode("utf-8")).decode("utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None

        candidates = [
            ((payload.get("user") or {}) if isinstance(payload.get("user"), dict) else {}).get("id"),
            payload.get("id"),
            payload.get("sub"),
            ((payload.get("https://hasura.io/jwt/claims") or {}) if isinstance(payload.get("https://hasura.io/jwt/claims"), dict) else {}).get("x-hasura-user-id"),
        ]
        for candidate in candidates:
            try:
                normalized = int(str(candidate).strip())
            except (TypeError, ValueError):
                continue
            if normalized > 0:
                return normalized
        return None

    @property
    def user_features_available(self) -> bool:
        return self.identity_state == "resolved" and bool(self.user_id)

    async def resolve_identity(self) -> int | None:
        if self.identity_state != "unresolved":
            return self.user_id

        async with self._identity_lock:
            if self.identity_state != "unresolved":
                return self.user_id
            try:
                data = await self.graphql(self.VIEWER_QUERY, {}, cache_key="viewer")
            except HardcoverAPIError as exc:
                self.identity_error = str(exc)
                if exc.status_code in {401, 403}:
                    self.identity_state = "unavailable"
                return None

            me = data.get("me") if isinstance(data, dict) else None
            if isinstance(me, list):
                me = me[0] if me else None
            try:
                resolved_user_id = int((me or {}).get("id")) if isinstance(me, dict) else 0
            except (TypeError, ValueError):
                resolved_user_id = 0

            if resolved_user_id > 0:
                self.user_id = resolved_user_id
                self.viewer_username = str(me.get("username") or "").strip() or None
                self.identity_state = "resolved"
                self.identity_error = None
                return self.user_id

            self.identity_state = "unavailable"
            self.identity_error = "Hardcover responded without a usable viewer ID."
            return None

    async def require_user_identity(self) -> int:
        user_id = await self.resolve_identity()
        if user_id:
            return user_id
        detail = self.identity_error or "The token cannot read the signed-in Hardcover account."
        raise HardcoverAPIError(
            f"identity_unavailable: {detail}",
            code="identity_unavailable",
        )

    async def __aenter__(self):
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.aclose()

    async def open(self) -> None:
        if self._client is not None:
            return
        headers = {
            "Authorization": self.authorization_header(),
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        }
        self._client = httpx.AsyncClient(headers=headers, timeout=self.timeout_seconds)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _invalidate_personalized_query_cache(self) -> None:
        stale_keys = [
            key for key in self._cache
            if key.startswith("edition:isbn:") or key.startswith("book:")
        ]
        for key in stale_keys:
            self._cache.pop(key, None)

    def _normalize_user_book_mapping(self, items: Any) -> dict[int, dict[str, Any]]:
        mapping: dict[int, dict[str, Any]] = {}
        if not isinstance(items, list):
            return mapping

        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                book_id = int(item.get("book_id"))
            except (TypeError, ValueError):
                continue
            if book_id <= 0 or book_id in mapping:
                continue
            mapping[book_id] = copy.deepcopy(item)
        return mapping

    def _set_user_book_map(self, mapping: dict[int, dict[str, Any]]) -> None:
        self._user_book_map = copy.deepcopy(mapping)
        self._user_book_map_expires_at = time.monotonic() + _CACHE_TTL_USER_LIBRARY

    def _user_book_map_snapshot(self) -> dict[int, dict[str, Any]] | None:
        if self._user_book_map is None:
            return None
        if time.monotonic() >= self._user_book_map_expires_at:
            self._user_book_map = None
            return None
        return copy.deepcopy(self._user_book_map)

    def _upsert_user_book_map_entry(self, user_book: dict[str, Any] | None) -> None:
        if not isinstance(user_book, dict):
            return
        try:
            book_id = int(user_book.get("book_id"))
        except (TypeError, ValueError):
            return
        if book_id <= 0:
            return
        current = self._user_book_map_snapshot() or {}
        current[book_id] = copy.deepcopy(user_book)
        self._set_user_book_map(current)

    def _remove_user_book_map_entry(self, book_id: Any) -> None:
        try:
            normalized_book_id = int(book_id)
        except (TypeError, ValueError):
            return
        if normalized_book_id <= 0:
            return
        current = self._user_book_map_snapshot() or {}
        if normalized_book_id in current:
            current.pop(normalized_book_id, None)
            self._set_user_book_map(current)

    async def user_book_map(self, *, force_refresh: bool = False) -> dict[int, dict[str, Any]]:
        await self.require_user_identity()

        if not force_refresh:
            cached = self._user_book_map_snapshot()
            if cached is not None:
                return cached

        async with self._user_book_map_lock:
            if not force_refresh:
                cached = self._user_book_map_snapshot()
                if cached is not None:
                    return cached

            mapping: dict[int, dict[str, Any]] = {}
            offset = 0

            while True:
                data = await self.graphql(
                    self.USER_LIBRARY_QUERY,
                    {
                        "user_id": self.user_id,
                        "limit": _USER_LIBRARY_PAGE_SIZE,
                        "offset": offset,
                    },
                    cache_key=None,
                )
                page = data.get("user_books") or []
                mapping.update(self._normalize_user_book_mapping(page))

                page_size = len(page) if isinstance(page, list) else 0
                if page_size < _USER_LIBRARY_PAGE_SIZE:
                    break
                offset += _USER_LIBRARY_PAGE_SIZE

            self._set_user_book_map(mapping)
            return copy.deepcopy(mapping)

    async def graphql(
        self,
        query: str,
        variables: dict[str, Any],
        *,
        cache_key: str | None = None,
        retry_5xx: int = 2,
    ) -> dict[str, Any]:
        if cache_key and cache_key in self._cache:
            return copy.deepcopy(self._cache[cache_key])

        await self.open()
        assert self._client is not None
        operation_name = graphql_operation_name(query)
        inflight_key = graphql_inflight_key(query, variables, cache_key)
        owns_inflight = False
        inflight_future: asyncio.Future[dict[str, Any]] | None = None

        if inflight_key:
            async with self._inflight_lock:
                existing_future = self._inflight.get(inflight_key)
                if existing_future is not None:
                    inflight_future = existing_future
                else:
                    inflight_future = asyncio.get_running_loop().create_future()
                    inflight_future.add_done_callback(_consume_future_exception)
                    self._inflight[inflight_key] = inflight_future
                    owns_inflight = True
            if not owns_inflight and inflight_future is not None:
                return copy.deepcopy(await inflight_future)

        retry_429 = 3
        attempt = 0
        try:
            while True:
                waited_for = await self.rate_controller.acquire()
                if waited_for >= 1.0:
                    logger.warning(
                        "[HARDCOVER-RATE] Delayed request op=%s wait_s=%.3f cache_key=%s endpoint=%s",
                        operation_name,
                        waited_for,
                        cache_key or "",
                        self.endpoint,
                    )
                try:
                    response = await self._client.post(
                        self.endpoint,
                        json={"query": query, "variables": variables},
                    )
                except httpx.TimeoutException as exc:
                    raise HardcoverAPIError(f"timeout: {exc}") from exc
                except httpx.RequestError as exc:
                    raise HardcoverAPIError(f"request_error: {exc}") from exc

                await self.rate_controller.update_from_headers(response.headers)

                if response.status_code == 429 and attempt < retry_429:
                    retry_after = response.headers.get("Retry-After")
                    cooldown_seconds = await self.rate_controller.note_rate_limited(attempt, retry_after)
                    logger.warning(
                        "[HARDCOVER-RATE] Received 429 op=%s attempt=%s retry_after=%s cooldown_s=%.3f endpoint=%s",
                        operation_name,
                        attempt + 1,
                        retry_after or "",
                        cooldown_seconds,
                        self.endpoint,
                    )
                    attempt += 1
                    continue

                if 500 <= response.status_code <= 599 and attempt < retry_5xx:
                    await asyncio.sleep(min(4.0, 0.5 * (2 ** attempt)))
                    attempt += 1
                    continue

                if response.status_code >= 400:
                    raise hardcover_api_error_from_response(response)

                try:
                    payload = response.json()
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise HardcoverAPIError("invalid_json_response") from exc
                if not isinstance(payload, dict):
                    raise HardcoverAPIError("invalid_graphql_response")
                if payload.get("errors"):
                    first = payload["errors"][0]
                    message = first.get("message") if isinstance(first, dict) else str(first)
                    extensions = first.get("extensions") if isinstance(first, dict) else {}
                    code = extensions.get("code") if isinstance(extensions, dict) else None
                    code = code or _hardcover_error_code(message)
                    raise HardcoverAPIError(f"graphql_error: {message}", code=code)

                data = payload.get("data") or {}
                if cache_key:
                    self._cache[cache_key] = copy.deepcopy(data)
                if owns_inflight and inflight_future is not None and not inflight_future.done():
                    inflight_future.set_result(copy.deepcopy(data))
                return data
        except Exception as exc:
            if owns_inflight and inflight_future is not None and not inflight_future.done():
                inflight_future.set_exception(exc)
            raise
        finally:
            if owns_inflight and inflight_key:
                async with self._inflight_lock:
                    current_future = self._inflight.get(inflight_key)
                    if current_future is inflight_future:
                        self._inflight.pop(inflight_key, None)

    async def search(self, query: str, query_type: str = "Book", per_page: int = 5) -> list[dict[str, Any]]:
        normalized_type = str(query_type or "Book").strip().title()
        normalized_query = str(query or "").strip()
        if not normalized_query:
            return []

        data = await self.graphql(
            self.SEARCH_QUERY,
            {
                "query": normalized_query,
                "query_type": normalized_type,
                "per_page": int(per_page),
                "page": 1,
            },
            cache_key=f"search:{normalized_type.lower()}:{normalized_query.lower()}:{int(per_page)}",
        )
        search_data = data.get("search") or {}
        return normalize_search_results(search_data.get("results") or [])

    async def prefetch_searches(self, searches: list[tuple[str, str, int]]) -> None:
        pending: list[tuple[str, str, int]] = []
        seen: set[str] = set()

        for raw_query, raw_query_type, raw_per_page in searches:
            normalized_query = str(raw_query or "").strip()
            normalized_type = str(raw_query_type or "Book").strip().title()
            try:
                normalized_per_page = int(raw_per_page)
            except (TypeError, ValueError):
                normalized_per_page = 5
            if not normalized_query:
                continue
            cache_key = f"search:{normalized_type.lower()}:{normalized_query.lower()}:{normalized_per_page}"
            if cache_key in self._cache or cache_key in seen:
                continue
            seen.add(cache_key)
            pending.append((normalized_query, normalized_type, normalized_per_page))

        if not pending:
            return

        # Hardcover permits only one top-level `search` field per request. Run
        # the first request alone so its response can bootstrap the token's
        # advertised burst policy, then let the shared rate controller pace the
        # remaining legal requests.
        first_query, first_type, first_per_page = pending[0]
        await self.search(first_query, first_type, first_per_page)
        if len(pending) > 1:
            await asyncio.gather(*(
                self.search(query, query_type, per_page)
                for query, query_type, per_page in pending[1:]
            ))

    async def edition_by_isbn(self, isbn: str) -> dict[str, Any] | None:
        isbn = str(isbn or "").strip().upper()
        if not isbn:
            return None
        if len(isbn) == 13:
            query = self.EDITION_BY_ISBN_13_QUERY_WITH_USER if self.user_id else self.EDITION_BY_ISBN_13_QUERY
        else:
            query = self.EDITION_BY_ISBN_10_QUERY_WITH_USER if self.user_id else self.EDITION_BY_ISBN_10_QUERY
        variables = {"isbn": isbn}
        if self.user_id:
            variables["user_id"] = self.user_id
        data = await self.graphql(
            query,
            variables,
            cache_key=f"edition:isbn:{isbn}",
        )
        editions = data.get("editions") or []
        if isinstance(editions, list) and editions:
            return editions[0]
        return None

    def cached_edition_by_isbn(self, isbn: str) -> tuple[bool, dict[str, Any] | None]:
        normalized_isbn = str(isbn or "").strip().upper()
        cache_key = f"edition:isbn:{normalized_isbn}"
        if not normalized_isbn or cache_key not in self._cache:
            return False, None
        editions = (self._cache.get(cache_key) or {}).get("editions") or []
        if isinstance(editions, list) and editions and isinstance(editions[0], dict):
            return True, copy.deepcopy(editions[0])
        return True, None

    async def prefetch_editions_by_isbns(self, isbns: list[str]) -> None:
        pending: list[tuple[str, str]] = []
        seen: set[str] = set()

        for raw_isbn in isbns:
            isbn = str(raw_isbn or "").strip().upper()
            if not isbn:
                continue
            cache_key = f"edition:isbn:{isbn}"
            if cache_key in self._cache or cache_key in seen:
                continue
            seen.add(cache_key)
            pending.append((isbn, cache_key))

        for start in range(0, len(pending), _ISBN_PREFETCH_CHUNK_SIZE):
            chunk = pending[start:start + _ISBN_PREFETCH_CHUNK_SIZE]
            if not chunk:
                continue

            requested = {isbn for isbn, _ in chunk}
            variables: dict[str, Any] = {
                "isbn_10s": [isbn for isbn in requested if len(isbn) == 10],
                "isbn_13s": [isbn for isbn in requested if len(isbn) == 13],
            }
            if self.user_id:
                variables["user_id"] = self.user_id
            query = self.EDITIONS_BY_ISBNS_QUERY_WITH_USER if self.user_id else self.EDITIONS_BY_ISBNS_QUERY
            data = await self.graphql(query, variables, cache_key=None)
            editions = data.get("editions") or []

            matches: dict[str, dict[str, Any]] = {}
            if isinstance(editions, list):
                for edition in editions:
                    if not isinstance(edition, dict):
                        continue
                    for field in ("isbn_10", "isbn_13"):
                        edition_isbn = str(edition.get(field) or "").strip().upper()
                        if edition_isbn in requested and edition_isbn not in matches:
                            matches[edition_isbn] = edition

            for isbn, cache_key in chunk:
                match = matches.get(isbn)
                self._cache[cache_key] = copy.deepcopy({"editions": [match] if match else []})

    async def book_details(self, book_id: int, *, use_cache: bool = True) -> dict[str, Any] | None:
        try:
            normalized_id = int(book_id)
        except (TypeError, ValueError):
            return None
        if normalized_id <= 0:
            return None

        query = self.BOOK_DETAILS_QUERY_WITH_USER if self.user_id else self.BOOK_DETAILS_QUERY
        variables: dict[str, Any] = {"id": normalized_id}
        if self.user_id:
            variables["user_id"] = self.user_id
        data = await self.graphql(
            query,
            variables,
            cache_key=f"book:{normalized_id}" if use_cache else None,
        )
        books = data.get("books") or []
        if isinstance(books, list) and books:
            return books[0]
        return None

    async def user_book_for_book(self, book_id: int) -> dict[str, Any] | None:
        await self.require_user_identity()
        try:
            normalized_id = int(book_id)
        except (TypeError, ValueError):
            return None
        if normalized_id <= 0:
            return None

        mapping_loaded = False
        try:
            mapping = await self.user_book_map()
        except HardcoverAPIError:
            mapping = {}
        else:
            mapping_loaded = True

        cached_user_book = mapping.get(normalized_id)
        if isinstance(cached_user_book, dict):
            return copy.deepcopy(cached_user_book)
        if mapping_loaded:
            return None

        data = await self.graphql(
            self.USER_BOOK_FOR_BOOK_QUERY,
            {"book_id": normalized_id, "user_id": self.user_id},
            cache_key=None,
        )
        user_books = data.get("user_books") or []
        if isinstance(user_books, list) and user_books:
            user_book = user_books[0]
            if isinstance(user_book, dict):
                self._upsert_user_book_map_entry(user_book)
                return user_book
        return None

    async def user_books_for_books(self, book_ids: list[int]) -> dict[int, dict[str, Any]]:
        await self.require_user_identity()

        normalized_ids: list[int] = []
        seen_ids: set[int] = set()
        for raw_book_id in book_ids:
            try:
                normalized_id = int(raw_book_id)
            except (TypeError, ValueError):
                continue
            if normalized_id <= 0 or normalized_id in seen_ids:
                continue
            seen_ids.add(normalized_id)
            normalized_ids.append(normalized_id)

        if not normalized_ids:
            return {}

        mapping_loaded = False
        try:
            library_map = await self.user_book_map()
        except HardcoverAPIError:
            library_map = {}
        else:
            mapping_loaded = True

        if mapping_loaded:
            return {
                book_id: copy.deepcopy(library_map[book_id])
                for book_id in normalized_ids
                if book_id in library_map
            }

        data = await self.graphql(
            self.USER_BOOKS_FOR_BOOKS_QUERY,
            {"book_ids": normalized_ids, "user_id": self.user_id},
            cache_key=None,
        )
        mapping = self._normalize_user_book_mapping(data.get("user_books") or [])
        for item in mapping.values():
            self._upsert_user_book_map_entry(item)
        return mapping

    async def update_user_book_status(
        self,
        user_book_id: int,
        status_id: int,
        *,
        edition_id: int | None = None,
        privacy_setting_id: int | None = None,
        rating: float | int | None = None,
        user_date: str | None = None,
    ) -> dict[str, Any] | None:
        await self.require_user_identity()
        try:
            normalized_user_book_id = int(user_book_id)
            normalized_status_id = int(status_id)
        except (TypeError, ValueError):
            return None
        if normalized_user_book_id <= 0 or normalized_status_id <= 0:
            return None

        payload = {
            "edition_id": int(edition_id) if edition_id not in (None, "") else None,
            "status_id": normalized_status_id,
            "rating": float(rating) if rating not in (None, "") else None,
            "privacy_setting_id": int(privacy_setting_id) if privacy_setting_id not in (None, "") else 1,
            "user_date": str(user_date or date.today().isoformat()),
        }

        data = await self.graphql(
            self.UPDATE_USER_BOOK_MUTATION,
            {"id": normalized_user_book_id, "object": payload},
            cache_key=None,
        )
        response = data.get("updateResponse") or {}
        error_message = str(response.get("error") or "").strip()
        if error_message:
            raise HardcoverAPIError(error_message)

        user_book = response.get("userBook")
        if isinstance(user_book, dict):
            self._upsert_user_book_map_entry(user_book)
            self._invalidate_personalized_query_cache()
            return user_book
        return None

    async def delete_user_book(self, user_book_id: int) -> dict[str, Any] | None:
        await self.require_user_identity()
        try:
            normalized_user_book_id = int(user_book_id)
        except (TypeError, ValueError):
            return None
        if normalized_user_book_id <= 0:
            return None

        data = await self.graphql(
            self.DELETE_USER_BOOK_MUTATION,
            {"id": normalized_user_book_id},
            cache_key=None,
        )
        deleted_user_book = data.get("deleteResponse")
        if isinstance(deleted_user_book, dict):
            self._remove_user_book_map_entry(deleted_user_book.get("bookId"))
            self._invalidate_personalized_query_cache()
        return deleted_user_book if isinstance(deleted_user_book, dict) else None

    async def create_user_book(
        self,
        book_id: int,
        *,
        status_id: int | None = None,
        edition_id: int | None = None,
        privacy_setting_id: int | None = None,
        rating: float | int | None = None,
        user_date: str | None = None,
    ) -> dict[str, Any] | None:
        await self.require_user_identity()
        try:
            normalized_book_id = int(book_id)
        except (TypeError, ValueError):
            return None
        if normalized_book_id <= 0:
            return None

        normalized_status_id = None
        if status_id not in (None, ""):
            try:
                normalized_status_id = int(status_id)
            except (TypeError, ValueError):
                return None
            if normalized_status_id <= 0:
                return None

        payload = {
            "book_id": normalized_book_id,
            "edition_id": int(edition_id) if edition_id not in (None, "") else None,
            "status_id": normalized_status_id,
            "rating": float(rating) if rating not in (None, "") else None,
            "privacy_setting_id": int(privacy_setting_id) if privacy_setting_id not in (None, "") else 1,
            "user_date": str(user_date or date.today().isoformat()),
        }

        data = await self.graphql(
            self.CREATE_USER_BOOK_MUTATION,
            {"object": payload},
            cache_key=None,
        )
        response = data.get("createResponse") or {}
        error_message = str(response.get("error") or "").strip()
        if error_message:
            raise HardcoverAPIError(error_message)

        user_book = response.get("userBook")
        if isinstance(user_book, dict):
            self._upsert_user_book_map_entry(user_book)
            self._invalidate_personalized_query_cache()
            return user_book

        inserted_id = response.get("id")
        if inserted_id:
            created = await self.user_book_for_book(normalized_book_id)
            if isinstance(created, dict):
                self._upsert_user_book_map_entry(created)
                self._invalidate_personalized_query_cache()
            return created
        return None

    async def series_details(self, series_id: int) -> dict[str, Any] | None:
        try:
            normalized_id = int(series_id)
        except (TypeError, ValueError):
            return None
        if normalized_id <= 0:
            return None

        data = await self.graphql(
            self.SERIES_DETAILS_QUERY,
            {"id": normalized_id},
            cache_key=f"series:{normalized_id}",
        )
        series = data.get("series") or []
        if isinstance(series, list) and series:
            return series[0]
        return None
