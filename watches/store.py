"""
sqlite persistence for watches, stored at ``<DATA_PATH>/watches.db``.

The connection is opened lazily and every access goes through one lock, so the
(small, fast) synchronous sqlite calls are safe to make from the async app.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

SEARCH_FIELDS = ("title", "author", "series", "narrator")
COSTS = ("free", "wedge", "ratio")
EVENT_KINDS = {"baseline", "grabbed", "error"}

MIN_INTERVAL_MINUTES = 30
MAX_GRABS_PER_RUN_LIMIT = 10
MAX_EVENTS_PER_WATCH = 200

WATCH_DEFAULTS: dict[str, Any] = {
    "name": "",
    "enabled": True,
    "query": "",
    "search_in": ["author"],
    "title_regex": "",
    "main_cat": [],
    "language_ids": ["1"],
    "cost": "free",
    "interval_minutes": 60,
    "max_grabs_per_run": 2,
    "grab_existing": False,
    "torrent_category": "",
}
LIST_COLUMNS = ("search_in", "main_cat", "language_ids")
WATCH_COLUMNS = tuple(WATCH_DEFAULTS)

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None
_db_path: Path | None = None


class WatchValidationError(ValueError):
    """A watch definition failed validation (surfaces as HTTP 400)."""


# --------------------------------------------------------------------------- setup
def configure(db_path: str | Path | None) -> None:
    global _db_path
    with _lock:
        close()
        _db_path = Path(db_path) if db_path else None


def close() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is not None:
            return _conn
        if _db_path is None:
            raise RuntimeError("watch store is not configured")
        _db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(_db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS watches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                query TEXT NOT NULL DEFAULT '',
                search_in TEXT NOT NULL DEFAULT '["author"]',
                title_regex TEXT NOT NULL DEFAULT '',
                main_cat TEXT NOT NULL DEFAULT '[]',
                language_ids TEXT NOT NULL DEFAULT '[]',
                cost TEXT NOT NULL DEFAULT 'free',
                interval_minutes INTEGER NOT NULL DEFAULT 60,
                max_grabs_per_run INTEGER NOT NULL DEFAULT 2,
                grab_existing INTEGER NOT NULL DEFAULT 0,
                torrent_category TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_run_at TEXT,
                last_summary TEXT NOT NULL DEFAULT '',
                baseline_done INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS watch_seen (
                watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
                mam_id TEXT NOT NULL,
                grabbed INTEGER NOT NULL DEFAULT 0,
                title TEXT,
                seen_at TEXT NOT NULL,
                PRIMARY KEY (watch_id, mam_id)
            );
            CREATE INDEX IF NOT EXISTS idx_watch_seen_grabbed ON watch_seen(mam_id, grabbed);
            CREATE TABLE IF NOT EXISTS watch_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
                ts TEXT NOT NULL,
                kind TEXT NOT NULL,
                mam_id TEXT,
                title TEXT,
                detail TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_watch_events_ts ON watch_events(watch_id, ts DESC);
            CREATE INDEX IF NOT EXISTS idx_watch_events_kind_ts ON watch_events(kind, ts);
            """
        )
        conn.commit()
        _conn = conn
        return conn


# --------------------------------------------------------------------------- helpers
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _as_str_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple, set)):
        value = [value]
    return [str(v).strip() for v in value if str(v).strip()]


def _as_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in ("1", "true", "on", "yes")


def _as_int(value, *, field: str, default: int, minimum: int, maximum: int | None = None) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise WatchValidationError(f"{field} must be a whole number")
    if parsed < minimum:
        raise WatchValidationError(f"{field} must be at least {minimum}")
    if maximum is not None and parsed > maximum:
        raise WatchValidationError(f"{field} must be at most {maximum}")
    return parsed


def normalize_watch(data: dict, *, existing: dict | None = None) -> dict:
    """Validate an incoming watch; unspecified fields fall back to ``existing`` or defaults."""
    base = dict(WATCH_DEFAULTS)
    if existing:
        base.update({key: existing[key] for key in WATCH_COLUMNS if key in existing})
    data = data or {}

    def pick(key):
        return data[key] if key in data else base[key]

    name = str(pick("name") or "").strip()
    if not name:
        raise WatchValidationError("Name is required.")

    query = str(pick("query") or "").strip()
    if not query:
        raise WatchValidationError("A search query is required.")

    title_regex = str(pick("title_regex") or "").strip()
    if title_regex:
        try:
            re.compile(title_regex, re.IGNORECASE)
        except re.error as exc:
            raise WatchValidationError(f"Title filter is not a valid regular expression: {exc}")

    search_in = [f for f in _as_str_list(pick("search_in")) if f in SEARCH_FIELDS] or ["author"]

    cost = str(pick("cost") or "free").strip().lower()
    if cost not in COSTS:
        raise WatchValidationError(f"Cost must be one of: {', '.join(COSTS)}.")

    return {
        "name": name,
        "enabled": _as_bool(pick("enabled"), True),
        "query": query,
        "search_in": list(dict.fromkeys(search_in)),
        "title_regex": title_regex,
        "main_cat": [m for m in _as_str_list(pick("main_cat")) if m.isdigit()],
        "language_ids": [m for m in _as_str_list(pick("language_ids")) if m.isdigit()],
        "cost": cost,
        "interval_minutes": _as_int(
            pick("interval_minutes"), field="Check interval", default=WATCH_DEFAULTS["interval_minutes"],
            minimum=MIN_INTERVAL_MINUTES,
        ),
        "max_grabs_per_run": _as_int(
            pick("max_grabs_per_run"), field="Max grabs per run", default=WATCH_DEFAULTS["max_grabs_per_run"],
            minimum=1, maximum=MAX_GRABS_PER_RUN_LIMIT,
        ),
        "grab_existing": _as_bool(pick("grab_existing"), False),
        "torrent_category": str(pick("torrent_category") or "").strip(),
    }


def _row_to_watch(row: sqlite3.Row) -> dict:
    watch = dict(row)
    for column in LIST_COLUMNS:
        try:
            watch[column] = json.loads(watch.get(column) or "[]")
        except (TypeError, json.JSONDecodeError):
            watch[column] = []
    for column in ("enabled", "grab_existing", "baseline_done"):
        watch[column] = bool(watch.get(column))
    return watch


def _to_row(watch: dict) -> dict:
    row = {}
    for column in WATCH_COLUMNS:
        value = watch.get(column)
        if column in LIST_COLUMNS:
            value = json.dumps(list(value or []))
        elif isinstance(value, bool):
            value = int(value)
        row[column] = value
    return row


# --------------------------------------------------------------------------- watches
def list_watches() -> list[dict]:
    with _lock:
        rows = _get_conn().execute("SELECT * FROM watches ORDER BY name COLLATE NOCASE, id").fetchall()
    return [_row_to_watch(row) for row in rows]


def get_watch(watch_id: int) -> dict | None:
    with _lock:
        row = _get_conn().execute("SELECT * FROM watches WHERE id = ?", (int(watch_id),)).fetchone()
    return _row_to_watch(row) if row else None


def create_watch(data: dict) -> dict:
    row = _to_row(normalize_watch(data))
    now = utc_now_iso()
    columns = list(row) + ["created_at", "updated_at"]
    with _lock:
        conn = _get_conn()
        cursor = conn.execute(
            f"INSERT INTO watches ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            [*row.values(), now, now],
        )
        conn.commit()
        watch_id = cursor.lastrowid
    return get_watch(watch_id)


# Changing any of these changes which torrents match, so the next run baselines again
# instead of grabbing the new search's back-catalog.
SEARCH_DEFINING_COLUMNS = ("query", "search_in", "title_regex", "main_cat", "language_ids")


def update_watch(watch_id: int, data: dict) -> dict | None:
    existing = get_watch(watch_id)
    if existing is None:
        return None
    normalized = normalize_watch(data, existing=existing)
    search_changed = any(normalized[c] != existing[c] for c in SEARCH_DEFINING_COLUMNS)
    row = _to_row(normalized)
    assignments = ", ".join(f"{column} = ?" for column in row)
    with _lock:
        conn = _get_conn()
        conn.execute(
            f"UPDATE watches SET {assignments}, updated_at = ?,"
            f" baseline_done = CASE WHEN ? THEN 0 ELSE baseline_done END WHERE id = ?",
            [*row.values(), utc_now_iso(), int(search_changed), int(watch_id)],
        )
        conn.commit()
    return get_watch(watch_id)


def delete_watch(watch_id: int) -> bool:
    with _lock:
        conn = _get_conn()
        cursor = conn.execute("DELETE FROM watches WHERE id = ?", (int(watch_id),))
        conn.commit()
    return cursor.rowcount > 0


def record_run(watch_id: int, *, summary: str, searched: bool) -> None:
    """Stamp a run. ``searched`` (the MAM search succeeded) also completes the baseline."""
    with _lock:
        conn = _get_conn()
        conn.execute(
            "UPDATE watches SET last_run_at = ?, last_summary = ?,"
            " baseline_done = CASE WHEN ? THEN 1 ELSE baseline_done END WHERE id = ?",
            (utc_now_iso(), summary, int(searched), int(watch_id)),
        )
        conn.commit()


# --------------------------------------------------------------------------- seen
def seen_ids(watch_id: int) -> set[str]:
    with _lock:
        rows = _get_conn().execute("SELECT mam_id FROM watch_seen WHERE watch_id = ?", (int(watch_id),)).fetchall()
    return {row["mam_id"] for row in rows}


def grabbed_by_any_watch(mam_ids: Iterable[str]) -> set[str]:
    """MAM ids that any watch has already grabbed (cross-watch dedupe)."""
    ids = [str(m) for m in mam_ids if m]
    if not ids:
        return set()
    with _lock:
        rows = _get_conn().execute(
            f"SELECT DISTINCT mam_id FROM watch_seen WHERE grabbed = 1 AND mam_id IN ({', '.join('?' for _ in ids)})",
            ids,
        ).fetchall()
    return {row["mam_id"] for row in rows}


def mark_seen(watch_id: int, items: Iterable[tuple[str, str]], *, grabbed: bool = False) -> int:
    """Record ``(mam_id, title)`` pairs as seen. A grabbed flag is never downgraded."""
    rows = [(int(watch_id), str(mam_id), int(grabbed), title or "", utc_now_iso()) for mam_id, title in items if mam_id]
    if not rows:
        return 0
    with _lock:
        conn = _get_conn()
        conn.executemany(
            """
            INSERT INTO watch_seen (watch_id, mam_id, grabbed, title, seen_at) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(watch_id, mam_id) DO UPDATE SET grabbed = MAX(grabbed, excluded.grabbed)
            """,
            rows,
        )
        conn.commit()
    return len(rows)


# --------------------------------------------------------------------------- events
def log_event(watch_id: int, kind: str, *, mam_id: str | None = None, title: str | None = None,
              detail: str | None = None) -> None:
    if kind not in EVENT_KINDS:
        raise ValueError(f"unknown event kind: {kind}")
    with _lock:
        conn = _get_conn()
        conn.execute(
            "INSERT INTO watch_events (watch_id, ts, kind, mam_id, title, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (int(watch_id), utc_now_iso(), kind, mam_id, title, detail),
        )
        conn.execute(
            """
            DELETE FROM watch_events WHERE watch_id = ? AND id NOT IN (
                SELECT id FROM watch_events WHERE watch_id = ? ORDER BY ts DESC, id DESC LIMIT ?
            )
            """,
            (int(watch_id), int(watch_id), MAX_EVENTS_PER_WATCH),
        )
        conn.commit()


def events_for(watch_id: int, limit: int = 50) -> list[dict]:
    with _lock:
        rows = _get_conn().execute(
            "SELECT * FROM watch_events WHERE watch_id = ? ORDER BY ts DESC, id DESC LIMIT ?",
            (int(watch_id), max(1, min(int(limit), MAX_EVENTS_PER_WATCH))),
        ).fetchall()
    return [dict(row) for row in rows]


def grabs_since(hours: float = 24) -> int:
    """Grabs across all watches in the trailing window (for the daily cap)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).replace(microsecond=0).isoformat()
    with _lock:
        row = _get_conn().execute(
            "SELECT COUNT(*) AS n FROM watch_events WHERE kind = 'grabbed' AND ts >= ?", (cutoff,)
        ).fetchone()
    return int(row["n"] or 0)
