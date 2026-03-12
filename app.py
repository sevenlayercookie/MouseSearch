# app.py - Quart (async) version
from quart import Quart, request, render_template, Response, jsonify, send_file, g
import httpx
import json
import copy
import html
import argparse
import os
import posixpath
import time
import hashlib
import collections
import math
import shutil
import uuid
import sqlite3
import ipaddress
from difflib import SequenceMatcher

from datetime import datetime, timedelta
from dotenv import load_dotenv, dotenv_values
from httpx import Limits, Timeout, AsyncHTTPTransport
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from urllib.parse import unquote, urlparse

import re
from pathlib import Path

import logging # for hypercorn logging
import sys # for stderr logging

from static.language_dict import language_dict

import asyncio
try:
    from rapidfuzz import fuzz
except ImportError:
    fuzz = None
RAPIDFUZZ_AVAILABLE = fuzz is not None

from clients import get_torrent_client, get_client_display_name, get_available_clients
from hashing import calculate_torrent_hash_from_url, calculate_torrent_hash_from_bytes

# --- SCHEDULER AND STATE SETUP ---
app = Quart(__name__)

UPSTREAM_CLIENT: httpx.AsyncClient | None = None

torrent_client = None

# --- Monitoring & Caching Globals ---
monitoring_state = {} 
monitor_task = None
torrent_status_cache = {}
CACHE_TTL = 2.0
pending_mid_resolutions = {}  # Maps MID -> {"added_at": timestamp, "metadata": {...}}

# --- SSE Globals ---
connected_websockets = set() 

# --- RATE LIMITING HELPER ---
class LeakyBucket:
    """
    Enforces a rate limit of `limit` requests per `period` seconds.
    """
    def __init__(self, limit, period):
        self.limit = limit
        self.period = period
        self.tokens = limit
        self.last_update = time.monotonic()
        self.lock = asyncio.Lock()

    async def acquire(self):
        async with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            self.last_update = now
            
            # Refill tokens
            new_tokens = elapsed * (self.limit / self.period)
            self.tokens = min(self.limit, self.tokens + new_tokens)
            
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            
            # Calculate wait time if empty
            wait_time = (1 - self.tokens) * (self.period / self.limit)
            return wait_time

# 120 requests per 60 seconds (Shared limit)
mam_autosuggest_limiter = LeakyBucket(120, 60.0)
AUTOSUGGEST_RESPONSE_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60.0
AUTOSUGGEST_RESPONSE_CACHE_MAX_ENTRIES = 1000
autosuggest_cache_db_conn = None
autosuggest_cache_db_lock = asyncio.Lock()
AUTOSUGGEST_CACHE_DB_PATH = None

RESULT_DISPLAY_FIELDS = [
    "date_uploaded",
    "file_type",
    "file_size",
    "snatches",
    "seeders",
    "category",
    "language",
    "narrator",
    "series",
]
LANGUAGE_BY_ID = {str(value): name for name, value in language_dict.items()}
DEFAULT_SEARCH_FILTER_DEFAULTS = {
    "searchType": "all",
    "search_scope": "torrents",
    "hide_downloaded": False,
    "search_in_title": True,
    "search_in_author": True,
    "search_in_series": True,
    "search_in_narrator": False,
    "search_in_description": False,
    "search_in_tags": False,
    "search_in_filenames": False,
    "language_ids": [str(language_dict.get("English", 1))],
    "main_cat": [],
    "category_ids": [],
    "flags_mode": "0",
    "flag_ids": [],
    "start_date": "",
    "end_date": "",
    "min_size": "",
    "max_size": "",
    "size_unit": "1048576",
    "min_seeders": "",
    "max_seeders": "",
    "min_leechers": "",
    "max_leechers": "",
    "min_snatched": "",
    "max_snatched": "",
}

LEGACY_CONFIG_ALIASES = {
    "LOCAL_TORRENT_DOWNLOAD_PATH": ("TORRENT_DOWNLOAD_PATH",),
}

def normalize_result_display_fields(value, fallback):
    allowed = set(RESULT_DISPLAY_FIELDS)
    if isinstance(value, list):
        items = [str(item).strip() for item in value if str(item).strip()]
        return [item for item in items if item in allowed]
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",") if item.strip()]
        return [item for item in items if item in allowed] if items else fallback
    return fallback


def coerce_bool(val, default: bool) -> bool:
    # Already a bool? Keep it.
    if isinstance(val, bool):
        return val

    # None / empty string => use default (don’t silently flip off)
    if val is None:
        return default
    if isinstance(val, str) and val.strip() == "":
        return default

    # Int-like values
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if val == 1:
            return True
        if val == 0:
            return False
        return default

    # String values
    s = str(val).strip().lower()
    true_set = {"true", "1", "t", "yes", "y", "on"}
    false_set = {"false", "0", "f", "no", "n", "off"}

    if s in true_set:
        return True
    if s in false_set:
        return False

    # Unknown value => default
    return default


def normalize_string_list(value):
    if isinstance(value, list):
        items = [str(item).strip() for item in value]
    elif isinstance(value, str):
        stripped = value.strip()
        items = [stripped] if stripped else []
    else:
        items = []

    unique = []
    seen = set()
    for item in items:
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        unique.append(item)
    return unique


def normalize_info_hash(value) -> str:
    return str(value or "").strip().lower()


def normalize_monitoring_state_keys():
    normalized_state = {}
    for raw_hash, state in list(monitoring_state.items()):
        normalized_hash = normalize_info_hash(raw_hash)
        if not normalized_hash:
            continue
        existing = normalized_state.get(normalized_hash, {})
        merged = dict(existing)
        merged.update(state or {})
        normalized_state[normalized_hash] = merged

    monitoring_state.clear()
    monitoring_state.update(normalized_state)


def make_autosuggest_cache_key(raw_query, query_candidates, lang_ids, main_cats, selected_fields, suggestion_limit):
    payload = {
        "schema": 2,
        "q": normalize_spaces(raw_query).lower(),
        "candidates": [str(item) for item in (query_candidates or [])],
        "lang_ids": sorted({str(item) for item in (lang_ids or [])}),
        "main_cats": sorted({str(item) for item in (main_cats or [])}),
        "fields": [str(item) for item in (selected_fields or [])],
        "limit": int(suggestion_limit),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


async def get_cached_autosuggest_response(cache_key):
    if not await ensure_autosuggest_cache_db():
        return None

    now = time.time()
    async with autosuggest_cache_db_lock:
        conn = autosuggest_cache_db_conn
        if conn is None:
            return None

        row = conn.execute(
            "SELECT payload, expires_at FROM autosuggest_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if not row:
            return None

        payload_text, expires_at = row
        if float(expires_at) <= now:
            conn.execute("DELETE FROM autosuggest_cache WHERE cache_key = ?", (cache_key,))
            conn.commit()
            return None

        conn.execute(
            "UPDATE autosuggest_cache SET updated_at = ? WHERE cache_key = ?",
            (now, cache_key),
        )
        conn.commit()

    try:
        payload = json.loads(payload_text)
    except (TypeError, json.JSONDecodeError):
        async with autosuggest_cache_db_lock:
            conn = autosuggest_cache_db_conn
            if conn is not None:
                conn.execute("DELETE FROM autosuggest_cache WHERE cache_key = ?", (cache_key,))
                conn.commit()
        return None

    return payload if isinstance(payload, list) else None


async def set_cached_autosuggest_response(cache_key, payload):
    if not await ensure_autosuggest_cache_db():
        return

    async with autosuggest_cache_db_lock:
        conn = autosuggest_cache_db_conn
        if conn is None:
            return

        if not isinstance(payload, list) or len(payload) == 0:
            conn.execute("DELETE FROM autosuggest_cache WHERE cache_key = ?", (cache_key,))
            conn.commit()
            return

        now = time.time()
        expires_at = now + AUTOSUGGEST_RESPONSE_CACHE_TTL_SECONDS
        payload_text = json.dumps(payload, separators=(",", ":"))

        conn.execute(
            """
            INSERT INTO autosuggest_cache (cache_key, payload, expires_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                payload = excluded.payload,
                expires_at = excluded.expires_at,
                updated_at = excluded.updated_at
            """,
            (cache_key, payload_text, expires_at, now),
        )
        prune_autosuggest_cache_db_locked(conn, now)
        conn.commit()


def prune_autosuggest_cache_db_locked(conn, now_epoch):
    conn.execute("DELETE FROM autosuggest_cache WHERE expires_at <= ?", (now_epoch,))
    row = conn.execute("SELECT COUNT(*) FROM autosuggest_cache").fetchone()
    total = int(row[0]) if row else 0
    overflow = total - AUTOSUGGEST_RESPONSE_CACHE_MAX_ENTRIES
    if overflow > 0:
        conn.execute(
            """
            DELETE FROM autosuggest_cache
            WHERE cache_key IN (
                SELECT cache_key FROM autosuggest_cache
                ORDER BY updated_at ASC
                LIMIT ?
            )
            """,
            (overflow,),
        )


async def ensure_autosuggest_cache_db():
    global autosuggest_cache_db_conn
    async with autosuggest_cache_db_lock:
        if autosuggest_cache_db_conn is not None:
            return True

        db_path = AUTOSUGGEST_CACHE_DB_PATH
        if db_path is None:
            return False

        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS autosuggest_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_autosuggest_cache_expires_at ON autosuggest_cache(expires_at)"
            )
            prune_autosuggest_cache_db_locked(conn, time.time())
            conn.commit()
            autosuggest_cache_db_conn = conn
            return True
        except Exception as e:
            app.logger.error(f"Failed to initialize autosuggest sqlite cache: {e}")
            autosuggest_cache_db_conn = None
            return False


async def close_autosuggest_cache_db():
    global autosuggest_cache_db_conn
    async with autosuggest_cache_db_lock:
        if autosuggest_cache_db_conn is None:
            return
        autosuggest_cache_db_conn.close()
        autosuggest_cache_db_conn = None


def normalize_search_filter_defaults(value):
    defaults = copy.deepcopy(DEFAULT_SEARCH_FILTER_DEFAULTS)
    if not isinstance(value, dict):
        return defaults

    bool_fields = [
        "hide_downloaded",
        "search_in_title",
        "search_in_author",
        "search_in_series",
        "search_in_narrator",
        "search_in_description",
        "search_in_tags",
        "search_in_filenames",
    ]
    for field in bool_fields:
        defaults[field] = coerce_bool(value.get(field), defaults[field])

    for field in ["searchType", "search_scope"]:
        raw = value.get(field, defaults[field])
        text = str(raw).strip()
        defaults[field] = text if text else defaults[field]

    main_cats = normalize_string_list(value.get("main_cat", defaults["main_cat"]))
    if "all" in main_cats:
        main_cats = ["all"]
    defaults["main_cat"] = main_cats

    defaults["language_ids"] = normalize_string_list(value.get("language_ids", defaults["language_ids"]))
    defaults["category_ids"] = normalize_string_list(value.get("category_ids", defaults["category_ids"]))
    defaults["flag_ids"] = normalize_string_list(value.get("flag_ids", defaults["flag_ids"]))

    flags_mode = str(value.get("flags_mode", defaults["flags_mode"])).strip()
    defaults["flags_mode"] = flags_mode if flags_mode in {"0", "1"} else defaults["flags_mode"]

    text_fields = [
        "start_date",
        "end_date",
        "min_size",
        "max_size",
        "size_unit",
        "min_seeders",
        "max_seeders",
        "min_leechers",
        "max_leechers",
        "min_snatched",
        "max_snatched",
    ]
    for field in text_fields:
        raw = value.get(field, defaults[field])
        defaults[field] = str(raw).strip() if raw is not None else defaults[field]

    if not defaults["size_unit"]:
        defaults["size_unit"] = DEFAULT_SEARCH_FILTER_DEFAULTS["size_unit"]

    return defaults


AUTO_ORGANIZE_MEDIA_TYPES = [
    {"id": "13", "label": "Audiobooks"},
    {"id": "14", "label": "E-Books"},
    {"id": "15", "label": "Musicology"},
    {"id": "16", "label": "Radio"},
]
ALLOWED_AUTO_ORGANIZE_MAIN_CATS = {item["id"] for item in AUTO_ORGANIZE_MEDIA_TYPES}


def normalize_destination_paths(value, fallback_path):
    fallback_root = str(fallback_path or "").strip()
    if not fallback_root:
        fallback_root = "/downloads/organized"

    entries = []

    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                raw_path = item.get("path") or item.get("root_path") or item.get("destination")
                raw_default = item.get("default_main_cat") or item.get("default_for") or item.get("media_type") or ""
                raw_torrent_category = (
                    item.get("default_torrent_category")
                    or item.get("default_client_category")
                    or item.get("torrent_category")
                    or item.get("client_category")
                    or ""
                )
            elif isinstance(item, str):
                raw_path = item
                raw_default = ""
                raw_torrent_category = ""
            else:
                continue

            path = str(raw_path or "").strip()
            if not path:
                continue

            default_main_cat = str(raw_default or "").strip()
            if default_main_cat not in ALLOWED_AUTO_ORGANIZE_MAIN_CATS:
                default_main_cat = ""
            default_torrent_category = str(raw_torrent_category or "").strip()

            entries.append({
                "path": path,
                "default_main_cat": default_main_cat,
                "default_torrent_category": default_torrent_category,
            })
    elif isinstance(value, str):
        path = value.strip()
        if path:
            entries.append({"path": path, "default_main_cat": "", "default_torrent_category": ""})

    if not entries:
        entries = [{"path": fallback_root, "default_main_cat": "", "default_torrent_category": ""}]

    seen_defaults = set()
    for entry in entries:
        default_main_cat = entry.get("default_main_cat", "")
        if not default_main_cat:
            continue
        if default_main_cat in seen_defaults:
            entry["default_main_cat"] = ""
            continue
        seen_defaults.add(default_main_cat)

    return entries


def normalize_type_specific_torrent_categories(value, fallback_destination_paths=None):
    entries = []

    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue

            raw_default = item.get("default_main_cat") or item.get("default_for") or item.get("media_type") or ""
            raw_torrent_category = (
                item.get("default_torrent_category")
                or item.get("default_client_category")
                or item.get("torrent_category")
                or item.get("client_category")
                or ""
            )

            default_main_cat = str(raw_default or "").strip()
            if default_main_cat not in ALLOWED_AUTO_ORGANIZE_MAIN_CATS:
                continue

            default_torrent_category = str(raw_torrent_category or "").strip()
            if not default_torrent_category:
                continue

            entries.append({
                "default_main_cat": default_main_cat,
                "default_torrent_category": default_torrent_category,
            })

    if not entries and isinstance(fallback_destination_paths, list):
        for item in fallback_destination_paths:
            if not isinstance(item, dict):
                continue

            default_main_cat = str(item.get("default_main_cat") or "").strip()
            if default_main_cat not in ALLOWED_AUTO_ORGANIZE_MAIN_CATS:
                continue

            default_torrent_category = str(item.get("default_torrent_category") or "").strip()
            if not default_torrent_category:
                continue

            entries.append({
                "default_main_cat": default_main_cat,
                "default_torrent_category": default_torrent_category,
            })

    normalized = []
    seen_defaults = set()
    for entry in entries:
        default_main_cat = entry["default_main_cat"]
        if default_main_cat in seen_defaults:
            continue
        seen_defaults.add(default_main_cat)
        normalized.append(entry)

    return normalized


def apply_default_destination_path(default_path, destination_paths):
    default_root = str(default_path or "").strip() or FALLBACK_CONFIG["ORGANIZED_PATH"]
    normalized = normalize_destination_paths(destination_paths, default_root)

    extras = []
    for entry in normalized:
        path = str(entry.get("path") or "").strip()
        default_main_cat = str(entry.get("default_main_cat") or "").strip()
        has_type_specific_mapping = bool(default_main_cat)
        if not path:
            continue
        if path == default_root and not has_type_specific_mapping:
            continue
        extras.append({
            "path": path,
            "default_main_cat": default_main_cat,
            "default_torrent_category": "",
        })

    combined = [{"path": default_root, "default_main_cat": "", "default_torrent_category": ""}] + extras
    return default_root, combined


@app.before_serving
async def startup():
    # 1. Load the configuration FIRST
    await load_new_app_config()

    if await ensure_autosuggest_cache_db():
        app.logger.debug(f"Autosuggest sqlite cache ready at {AUTOSUGGEST_CACHE_DB_PATH}")
    else:
        app.logger.warning("Autosuggest sqlite cache unavailable; autosuggest cache disabled")

    if not RAPIDFUZZ_AVAILABLE:
        app.logger.warning(
            "rapidfuzz is not installed; autosuggest fuzzy scoring is using difflib fallback. "
            "Install requirements to enable rapidfuzz-based matching."
        )

    # 2. Use app.config (instead of initial_config) to check settings
    if app.config.get("ENABLE_FILESYSTEM_THUMBNAIL_CACHE", True):
        app.logger.debug("Cache cleanup task started")
        app.add_background_task(cleanup_cache_task)
        
    if app.config.get("AUTO_ORGANIZE_ON_SCHEDULE"):
        hours = int(app.config.get("AUTO_ORGANIZE_INTERVAL_HOURS", 1))
        misfire_grace_seconds = max(1, int(hours * 3600 * 0.8))
        scheduler.add_job(
            check_for_unorganized_torrents,
            'interval',
            hours=hours,
            id='organize_safety_net_job',
            replace_existing=True,
            misfire_grace_time=misfire_grace_seconds,
        )

    
    if (app.config.get("AUTO_BUY_UPLOAD_ON_RATIO")
            or app.config.get("AUTO_BUY_UPLOAD_ON_BUFFER")
            or app.config.get("AUTO_BUY_UPLOAD_ON_BONUS")):
        interval_hours = int(app.config.get("AUTO_BUY_UPLOAD_CHECK_INTERVAL_HOURS", 6))
        misfire_grace_seconds = max(1, int(interval_hours * 3600 * 0.8))
        scheduler.add_job(
            check_and_buy_upload,
            'interval',
            hours=interval_hours,
            id='upload_check_job',
            replace_existing=True,
            misfire_grace_time=misfire_grace_seconds,
        )
        scheduler.add_job(check_and_buy_upload, 'date', run_date=datetime.now() + timedelta(seconds=15), id='initial_upload_check_job')

    if app.config.get("ENABLE_DYNAMIC_IP_UPDATE"):
        interval_hours = int(app.config.get("DYNAMIC_IP_UPDATE_INTERVAL_HOURS", 3))
        misfire_grace_seconds = max(1, int(interval_hours * 3600 * 0.8))
        scheduler.add_job(
            check_and_update_ip,
            'interval',
            hours=interval_hours,
            id='ip_check_job',
            replace_existing=True,
            misfire_grace_time=misfire_grace_seconds,
        )
        scheduler.add_job(check_and_update_ip, 'date', run_date=datetime.now() + timedelta(seconds=5), id='initial_ip_check_job')
    
    if app.config.get("AUTO_BUY_VIP"):
        interval_hours = int(app.config.get("AUTO_BUY_VIP_INTERVAL_HOURS", 24))
        misfire_grace_seconds = max(1, int(interval_hours * 3600 * 0.8))
        scheduler.add_job(
            auto_buy_vip,
            'interval',
            hours=interval_hours,
            id='vip_buy_job',
            replace_existing=True,
            misfire_grace_time=misfire_grace_seconds,
        )
        scheduler.add_job(auto_buy_vip, 'date', run_date=datetime.now() + timedelta(seconds=10), id='initial_vip_buy_job')
        app.logger.info("AUTO_BUY_VIP started")
    
    if not scheduler.running:
        scheduler.start()
        app.logger.debug("AsyncIOScheduler started")

    global UPSTREAM_CLIENT
    transport = AsyncHTTPTransport(http2=True, retries=2)
    limits = Limits(max_connections=200, max_keepalive_connections=50, keepalive_expiry=120.0)
    timeout = Timeout(connect=5.0, read=15.0, write=15.0, pool=None)
    UPSTREAM_CLIENT = httpx.AsyncClient(transport=transport, limits=limits, timeout=timeout)
    app.logger.debug("Shared httpx AsyncClient initialized")
    
    # --- Initialize Active Monitoring on Startup ---
    metadata = load_database()
    pending = [h for h, m in metadata.items() if m.get('status') == 'pending']
    if pending:
        app.logger.info(f"Startup: Found {len(pending)} pending torrents. Starting active monitoring.")
        current_time = time.time()
        for h in pending:
            normalized_hash = normalize_info_hash(h)
            if normalized_hash:
                monitoring_state[normalized_hash] = {"added_at": current_time - 20}
        start_monitoring_loop()


@app.after_serving
async def shutdown():
    if scheduler.running:
        scheduler.shutdown()
        app.logger.info("AsyncIOScheduler shutdown")

    global UPSTREAM_CLIENT
    if UPSTREAM_CLIENT is not None:
        await UPSTREAM_CLIENT.aclose()
        UPSTREAM_CLIENT = None
        app.logger.info("Shared httpx AsyncClient closed")

    await close_autosuggest_cache_db()
    
    global monitor_task
    if monitor_task:
        monitor_task.cancel()


# --- LOGGING CONFIGURATION (NOISY LIBS SILENCED) ---
def parse_log_level(value, default=logging.DEBUG):
    """Parse a string/int log level with fallback."""
    if isinstance(value, int):
        return value
    if not value:
        return default
    level = getattr(logging, str(value).upper(), None)
    return level if isinstance(level, int) else default


def parse_bool_env(value, default=False):
    """Parse boolean environment values like true/false/1/0/on/off."""
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


APP_LOG_LEVEL = parse_log_level(os.getenv("APP_LOG_LEVEL", "INFO"), logging.INFO)
LOG_HTTP_REQUESTS = parse_bool_env(os.getenv("LOG_HTTP_REQUESTS"), False)
LOG_HTTP_REQUESTS_INCLUDE_STATIC = parse_bool_env(os.getenv("LOG_HTTP_REQUESTS_INCLUDE_STATIC"), False)
LOG_HTTP_REQUESTS_INCLUDE_EVENTS = parse_bool_env(os.getenv("LOG_HTTP_REQUESTS_INCLUDE_EVENTS"), False)
HTTP_LOG_REDACT_QUERY_KEYS = {"q", "query", "url", "torrent_url", "download_link"}
HTTP_LOG_MAX_QUERY_LENGTH = 240
HTTP_LOG_QUIET_PATHS = {"/events"}
HTTP_LOG_STATIC_PREFIXES = ("/static/",)


def _client_ip_from_headers():
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.headers.get("X-Real-IP") or request.remote_addr or "-"


def _sanitize_query_args():
    if not request.args:
        return ""
    parts = []
    for key in sorted(request.args.keys()):
        key_lower = key.lower()
        values = request.args.getlist(key)
        cleaned_values = []
        for raw in values:
            value = str(raw).replace("\n", " ").replace("\r", " ")
            if key_lower in HTTP_LOG_REDACT_QUERY_KEYS:
                cleaned_values.append(f"<redacted:{len(value)}>")
            else:
                cleaned_values.append(value if len(value) <= 80 else f"{value[:77]}...")
        if not cleaned_values:
            continue
        if len(cleaned_values) == 1:
            parts.append(f"{key}={cleaned_values[0]}")
        else:
            parts.append(f"{key}=[{','.join(cleaned_values)}]")
    joined = ", ".join(parts)
    if len(joined) > HTTP_LOG_MAX_QUERY_LENGTH:
        joined = f"{joined[:HTTP_LOG_MAX_QUERY_LENGTH]}..."
    return joined


def _should_skip_http_log(path):
    if path in HTTP_LOG_QUIET_PATHS and not LOG_HTTP_REQUESTS_INCLUDE_EVENTS:
        return True
    if path.startswith(HTTP_LOG_STATIC_PREFIXES) and not LOG_HTTP_REQUESTS_INCLUDE_STATIC:
        return True
    if path in {"/favicon.ico", "/robots.txt"} and not LOG_HTTP_REQUESTS_INCLUDE_STATIC:
        return True
    return False


@app.before_request
async def _track_request_start():
    g.request_started_at = time.monotonic()
    incoming_request_id = (request.headers.get("X-Request-ID") or "").strip()
    g.request_id = incoming_request_id[:64] if incoming_request_id else uuid.uuid4().hex[:12]


@app.after_request
async def _log_http_request(response):
    request_id = getattr(g, "request_id", uuid.uuid4().hex[:12])
    response.headers["X-Request-ID"] = request_id

    if not LOG_HTTP_REQUESTS:
        return response

    path = request.path or "/"
    if _should_skip_http_log(path):
        return response

    started = getattr(g, "request_started_at", None)
    duration_ms = (time.monotonic() - started) * 1000 if started else 0.0
    status_code = int(response.status_code)

    log_msg = (
        f"[HTTP] req={request_id} ip={_client_ip_from_headers()} "
        f"{request.method} {path} status={status_code} duration_ms={duration_ms:.1f}"
    )
    query_summary = _sanitize_query_args()
    if query_summary:
        log_msg += f" query={query_summary}"

    if status_code >= 500:
        app.logger.error(log_msg)
    elif status_code >= 400:
        app.logger.warning(log_msg)
    else:
        app.logger.info(log_msg)

    return response


# Configure root logger
logging.basicConfig(
    level=APP_LOG_LEVEL,
    format='[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    stream=sys.stderr
)

# Silence noisy libraries
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("hpack").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
logging.getLogger("tzlocal").setLevel(logging.WARNING)
logging.getLogger("hypercorn.access").setLevel(logging.INFO)

if __name__ != '__main__':
    logger = logging.getLogger('hypercorn.error')
    app.logger.handlers = logger.handlers
    app.logger.setLevel(APP_LOG_LEVEL)
else:
    app.logger.setLevel(APP_LOG_LEVEL)

scheduler = AsyncIOScheduler()

load_dotenv()

DEFAULT_RELATIVE_PATH_TEMPLATE = os.getenv("DEFAULT_RELATIVE_PATH_TEMPLATE", "{Author}/{Title}")

# --- VERSIONING HELPER ---
def get_app_version():
    """Reads the version from version.txt in the root directory."""
    try:
        version_file = Path("version.txt")
        if version_file.exists():
            with open(version_file, "r") as f:
                return f.read().strip()
    except Exception as e:
        app.logger.warning(f"Could not read version.txt: {e}")
    return "dev" # Default fallback

# Inject APP_VERSION into all templates
@app.context_processor
def inject_version():
    return dict(APP_VERSION=get_app_version())
    
# Define fallback values
FALLBACK_CONFIG = {
    "QUART_SECRET_KEY": os.urandom(24).hex(),
    "MAM_API_URL": "https://www.myanonamouse.net",
    "TORRENT_CLIENT_TYPE": "qbittorrent",
    "TORRENT_CLIENT_URL": "http://localhost:8080",
    "TORRENT_CLIENT_USERNAME": "admin",
    "TORRENT_CLIENT_PASSWORD": "",
    "TORRENT_CLIENT_CATEGORY": "",
    "MAM_ID": "",
    "DATA_PATH": "./data",
    "ORGANIZED_PATH": "/downloads/organized",
    "DESTINATION_PATHS": [
        {"path": "/downloads/organized", "default_main_cat": "", "default_torrent_category": ""}
    ],
    "TYPE_SPECIFIC_TORRENT_CATEGORIES": [],
    "LOCAL_TORRENT_DOWNLOAD_PATH": "/downloads/torrents",
    "REMOTE_TORRENT_DOWNLOAD_PATH": "",
    "REL_PATH_TEMPLATE": DEFAULT_RELATIVE_PATH_TEMPLATE,
    "AUTO_ORGANIZE_ON_ADD": False,
    "AUTO_ORGANIZE_ON_SCHEDULE": False,
    "AUTO_ORGANIZE_INTERVAL_HOURS": 1,
    "AUTO_ORGANIZE_USE_COPY": False,
    "HAPTICS_ENABLED": True,
    "ENABLE_DYNAMIC_IP_UPDATE": False,
    "DYNAMIC_IP_UPDATE_INTERVAL_HOURS": 3,
    "AUTO_BUY_VIP": False,
    "AUTO_BUY_VIP_INTERVAL_HOURS": 24,
    "AUTO_BUY_UPLOAD_ON_RATIO": False,
    "AUTO_BUY_UPLOAD_RATIO_THRESHOLD": 1.5,
    "AUTO_BUY_UPLOAD_RATIO_AMOUNT": 50,
    "AUTO_BUY_UPLOAD_ON_BUFFER": False,
    "AUTO_BUY_UPLOAD_BUFFER_THRESHOLD": 10,
    "AUTO_BUY_UPLOAD_BUFFER_AMOUNT": 50,
    "AUTO_BUY_UPLOAD_ON_BONUS": False,
    "AUTO_BUY_UPLOAD_BONUS_THRESHOLD": 5000,
    "AUTO_BUY_UPLOAD_BONUS_AMOUNT": 50,
    "AUTO_BUY_UPLOAD_CHECK_INTERVAL_HOURS": 6,
    "BLOCK_DOWNLOAD_ON_LOW_BUFFER": True,
    "AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD": False,
    "ENABLE_FILESYSTEM_THUMBNAIL_CACHE": True,
    "THUMBNAIL_CACHE_MAX_SIZE_MB": 500,
    "MAX_SEARCH_RESULTS": 50,
    "MAX_AUTOCOMPLETE_RESULTS": 20,
    "RESULTS_DISPLAY_FIELDS": ["narrator", "series", "file_size", "file_type", "seeders"],
    "SEARCH_FILTER_DEFAULTS": copy.deepcopy(DEFAULT_SEARCH_FILTER_DEFAULTS),
}

# Set up data directory and paths
DATA_PATH = Path(os.getenv("DATA_PATH", FALLBACK_CONFIG["DATA_PATH"])).resolve()
DATA_PATH.mkdir(parents=True, exist_ok=True)
AUTOSUGGEST_CACHE_DB_PATH = DATA_PATH / "autosuggest_cache.sqlite3"

UPLOAD_OPTIONS_FILE = Path("./static/upload_options.json")
UPLOAD_CREDIT_COST_PER_GB = 500
UPLOAD_CREDIT_MIN_GB = 50
UPLOAD_CREDIT_MAX_GB = 200
UPLOAD_CREDIT_CHUNK_SIZES = (100, 50)
VIP_COST_PER_WEEK = 1250
VIP_MAX_WEEKS = 12.85
VIP_MIN_WEEKS = 1

CONFIG_FILE = DATA_PATH / "config.json"
DATABASE_FILE = DATA_PATH / "database.json"
IP_STATE_FILE = DATA_PATH / "ip_state.json"
ENV_FILE = Path(".env")


# --- Setup:thumbnail cache ---
THUMB_CACHE_DIR = DATA_PATH / "cache/thumbnails"

# These will be set from config
ORGANIZED_PATH = None
LOCAL_TORRENT_DOWNLOAD_PATH = None
REMOTE_TORRENT_DOWNLOAD_PATH = None


def apply_legacy_config_aliases(target: dict, source):
    for canonical_key, aliases in LEGACY_CONFIG_ALIASES.items():
        canonical_value = source.get(canonical_key)
        if canonical_value not in (None, ""):
            target[canonical_key] = canonical_value
            continue

        for alias in aliases:
            legacy_value = source.get(alias)
            if legacy_value not in (None, ""):
                target[canonical_key] = legacy_value
                break


def get_local_torrent_download_path(config: dict) -> str:
    value = (
        config.get("LOCAL_TORRENT_DOWNLOAD_PATH")
        or config.get("TORRENT_DOWNLOAD_PATH")
        or FALLBACK_CONFIG["LOCAL_TORRENT_DOWNLOAD_PATH"]
    )
    return str(value).strip()


def get_remote_torrent_download_path(config: dict) -> str:
    return str(config.get("REMOTE_TORRENT_DOWNLOAD_PATH") or "").strip()


def _is_relative_to_remote_base(remote_path: str, remote_base: str) -> str | None:
    normalized_remote_path = posixpath.normpath(str(remote_path or "").strip())
    normalized_remote_base = posixpath.normpath(str(remote_base or "").strip())
    if not normalized_remote_path or not normalized_remote_base:
        return None

    rel_path = posixpath.relpath(normalized_remote_path, normalized_remote_base)
    if rel_path in {".", ""}:
        return ""
    if rel_path == ".." or rel_path.startswith("../"):
        return None
    return rel_path


def resolve_local_save_path(config: dict, remote_save_path: str | None) -> Path | None:
    local_base = get_local_torrent_download_path(config)
    remote_base = get_remote_torrent_download_path(config)
    raw_save_path = str(remote_save_path or "").strip()

    if raw_save_path:
        candidate = Path(raw_save_path)
        if local_base:
            local_base_path = Path(local_base).resolve()
            try:
                candidate_resolved = candidate.resolve()
            except Exception:
                candidate_resolved = candidate

            try:
                candidate_resolved.relative_to(local_base_path)
                return candidate_resolved
            except Exception:
                pass

        if remote_base and local_base:
            rel_path = _is_relative_to_remote_base(raw_save_path, remote_base)
            if rel_path is not None:
                mapped = Path(local_base)
                if rel_path:
                    mapped = mapped / Path(rel_path)
                return mapped.resolve()

        if candidate.exists():
            return candidate.resolve()

    if local_base:
        return Path(local_base).resolve()

    if raw_save_path:
        return Path(raw_save_path).resolve()

    return None


def resolve_local_content_path(config: dict, torrent_info: dict) -> Path | None:
    name = str((torrent_info or {}).get("name") or "").strip()
    if not name:
        return None

    save_path = resolve_local_save_path(config, (torrent_info or {}).get("save_path"))
    if save_path is not None:
        return save_path / Path(name)

    return Path(name)

def load_config():
    # 1. Start with Hardcoded Defaults (Lowest Priority)
    config = copy.deepcopy(FALLBACK_CONFIG)
    
    # 2. Update with Environment Variables (Medium Priority)
    # These act as fallbacks if the key is missing in config.json
    env_config = {key: os.getenv(key) for key in FALLBACK_CONFIG.keys() if os.getenv(key) is not None}
    apply_legacy_config_aliases(env_config, os.environ)
    config.update(env_config)

    # 3. Update with config.json (Highest Priority - The Source of Truth)
    # If a value exists here, it overwrites whatever was in .env or defaults
    json_config = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            try:
                json_config = json.load(f)
            except json.JSONDecodeError:
                pass # corrupted config, ignore

    json_overrides = dict(json_config)
    apply_legacy_config_aliases(json_overrides, json_config)
    config.update(json_overrides)

    # --- TYPE CASTING BLOCK (Safety) ---
    # Now that we have the final values, we force them into the correct types
    
    # Integers
    for key in [
        "AUTO_ORGANIZE_INTERVAL_HOURS", 
        "DYNAMIC_IP_UPDATE_INTERVAL_HOURS",
        "AUTO_BUY_VIP_INTERVAL_HOURS",
        "AUTO_BUY_UPLOAD_CHECK_INTERVAL_HOURS",
        "THUMBNAIL_CACHE_MAX_SIZE_MB",
        "MAX_SEARCH_RESULTS",
        "MAX_AUTOCOMPLETE_RESULTS",
    ]:
        try:
            config[key] = int(config[key])
        except (ValueError, TypeError):
            config[key] = FALLBACK_CONFIG[key]
    
    if config["MAX_SEARCH_RESULTS"] <= 0:
        config["MAX_SEARCH_RESULTS"] = FALLBACK_CONFIG["MAX_SEARCH_RESULTS"]
    if config["MAX_AUTOCOMPLETE_RESULTS"] <= 0:
        config["MAX_AUTOCOMPLETE_RESULTS"] = FALLBACK_CONFIG["MAX_AUTOCOMPLETE_RESULTS"]

    # Floats
    for key in [
        "AUTO_BUY_UPLOAD_RATIO_THRESHOLD",
        "AUTO_BUY_UPLOAD_RATIO_AMOUNT",
        "AUTO_BUY_UPLOAD_BUFFER_THRESHOLD",
        "AUTO_BUY_UPLOAD_BUFFER_AMOUNT",
        "AUTO_BUY_UPLOAD_BONUS_THRESHOLD",
        "AUTO_BUY_UPLOAD_BONUS_AMOUNT"
    ]:
        try:
            config[key] = float(config[key])
        except (ValueError, TypeError):
            config[key] = FALLBACK_CONFIG[key]

    # Booleans
    for key in [
        "AUTO_ORGANIZE_ON_ADD",
        "AUTO_ORGANIZE_ON_SCHEDULE",
        "AUTO_ORGANIZE_USE_COPY",
        "HAPTICS_ENABLED",
        "ENABLE_DYNAMIC_IP_UPDATE",
        "AUTO_BUY_VIP",
        "AUTO_BUY_UPLOAD_ON_RATIO",
        "AUTO_BUY_UPLOAD_ON_BUFFER",
        "AUTO_BUY_UPLOAD_ON_BONUS",
        "BLOCK_DOWNLOAD_ON_LOW_BUFFER",
        "AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD",
        "ENABLE_FILESYSTEM_THUMBNAIL_CACHE"
    ]:
        config[key] = coerce_bool(config.get(key), FALLBACK_CONFIG[key])
        val = config[key]
        if not isinstance(val, bool):
            # Check against common string representations of True
            config[key] = str(val).lower() in ('true', '1', 't', 'yes', 'on')

    config["RESULTS_DISPLAY_FIELDS"] = normalize_result_display_fields(
        config.get("RESULTS_DISPLAY_FIELDS"),
        FALLBACK_CONFIG["RESULTS_DISPLAY_FIELDS"]
    )
    config["SEARCH_FILTER_DEFAULTS"] = normalize_search_filter_defaults(
        config.get("SEARCH_FILTER_DEFAULTS")
    )

    raw_destination_paths = config.get("DESTINATION_PATHS")
    organized_path, destination_paths = apply_default_destination_path(
        config.get("ORGANIZED_PATH", FALLBACK_CONFIG["ORGANIZED_PATH"]),
        raw_destination_paths,
    )
    config["ORGANIZED_PATH"] = organized_path
    config["DESTINATION_PATHS"] = destination_paths
    config["TYPE_SPECIFIC_TORRENT_CATEGORIES"] = normalize_type_specific_torrent_categories(
        config.get("TYPE_SPECIFIC_TORRENT_CATEGORIES"),
        raw_destination_paths,
    )
    config["LOCAL_TORRENT_DOWNLOAD_PATH"] = get_local_torrent_download_path(config)
    config["REMOTE_TORRENT_DOWNLOAD_PATH"] = get_remote_torrent_download_path(config)
    config["TORRENT_DOWNLOAD_PATH"] = config["LOCAL_TORRENT_DOWNLOAD_PATH"]

    return config

def save_config(config):
    config_to_save = {key: config.get(key) for key in FALLBACK_CONFIG.keys()}
    with open(CONFIG_FILE, "w") as f:
        json.dump(config_to_save, f, indent=4)

def read_env_values():
    if not ENV_FILE.exists():
        return {}
    return dotenv_values(ENV_FILE)

def update_env_value(key: str, value: str):
    key = str(key)
    value = str(value)
    line = f"{key}={value}"

    if not ENV_FILE.exists():
        ENV_FILE.write_text(line + "\n")
        return

    lines = ENV_FILE.read_text().splitlines()
    updated = False
    new_lines = []
    for raw in lines:
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in raw:
            current_key = raw.split("=", 1)[0].strip()
            if current_key == key:
                new_lines.append(line)
                updated = True
                continue
        new_lines.append(raw)

    if not updated:
        if new_lines and new_lines[-1] != "":
            new_lines.append("")
        new_lines.append(line)

    ENV_FILE.write_text("\n".join(new_lines) + "\n")

def initialize_config():
    if not CONFIG_FILE.exists():
        initial_config = load_config()
        save_config(initial_config)
        print(f"Initialized {CONFIG_FILE} with default configuration.")
    else:
        # Check if QUART_SECRET_KEY is missing and needs to be generated
        existing_config = load_config()
        if not existing_config.get("QUART_SECRET_KEY") or existing_config.get("QUART_SECRET_KEY") == "":
            # Generate a new secret key and save it
            existing_config["QUART_SECRET_KEY"] = os.urandom(24).hex()
            save_config(existing_config)
            print(f"Generated and saved new QUART_SECRET_KEY to {CONFIG_FILE}.")

initialize_config()

def load_upload_options():
    if not UPLOAD_OPTIONS_FILE.exists():
        app.logger.warning("upload_options.json not found.")
        return {}
    try:
        with open(UPLOAD_OPTIONS_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        app.logger.error(f"Failed to load upload options: {e}")
        return {}

def build_upload_chunks(amount):
    try:
        val = float(amount)
    except (ValueError, TypeError):
        return None, None

    if val <= 0:
        return None, None

    units = round(val / UPLOAD_CREDIT_MIN_GB)
    if abs(val - (units * UPLOAD_CREDIT_MIN_GB)) > 1e-6:
        return None, None

    total = int(units) * UPLOAD_CREDIT_MIN_GB
    if total < UPLOAD_CREDIT_MIN_GB:
        return None, None
    if total > UPLOAD_CREDIT_MAX_GB:
        return None, None

    remaining = total
    chunks = []
    for chunk in UPLOAD_CREDIT_CHUNK_SIZES:
        count = remaining // chunk
        if count:
            chunks.extend([chunk] * int(count))
            remaining -= chunk * int(count)

    if remaining != 0:
        return None, None

    return total, chunks

def calculate_vip_topup_weeks(user_data):
    if not user_data:
        return 0.0

    seedbonus = float(user_data.get('seedbonus', 0) or 0)
    weeks_affordable = seedbonus / VIP_COST_PER_WEEK

    current_weeks = 0.0
    vip_until = user_data.get('vip_until')
    if vip_until:
        try:
            vip_dt = datetime.fromisoformat(str(vip_until).strip().replace(' ', 'T'))
            now = datetime.utcnow()
            if vip_dt > now:
                current_weeks = (vip_dt - now).total_seconds() / (60 * 60 * 24 * 7)
        except Exception:
            pass

    weeks_to_cap = max(0.0, VIP_MAX_WEEKS - current_weeks)
    return min(weeks_affordable, weeks_to_cap)
    
async def load_new_app_config():
    new_config = load_config()

    raw_destination_paths = new_config.get("DESTINATION_PATHS")
    organized_path, destination_paths = apply_default_destination_path(
        new_config.get("ORGANIZED_PATH", FALLBACK_CONFIG["ORGANIZED_PATH"]),
        raw_destination_paths,
    )
    new_config["ORGANIZED_PATH"] = organized_path
    new_config["DESTINATION_PATHS"] = destination_paths
    new_config["TYPE_SPECIFIC_TORRENT_CATEGORIES"] = normalize_type_specific_torrent_categories(
        new_config.get("TYPE_SPECIFIC_TORRENT_CATEGORIES"),
        raw_destination_paths,
    )
    new_config["LOCAL_TORRENT_DOWNLOAD_PATH"] = get_local_torrent_download_path(new_config)
    new_config["REMOTE_TORRENT_DOWNLOAD_PATH"] = get_remote_torrent_download_path(new_config)
    new_config["TORRENT_DOWNLOAD_PATH"] = new_config["LOCAL_TORRENT_DOWNLOAD_PATH"]

    app.secret_key = new_config["QUART_SECRET_KEY"]
    app.config.update(new_config)
    
    # Load upload options
    app.config["UPLOAD_OPTIONS"] = load_upload_options()
    
    # Update path globals
    global ORGANIZED_PATH, LOCAL_TORRENT_DOWNLOAD_PATH, REMOTE_TORRENT_DOWNLOAD_PATH
    ORGANIZED_PATH = Path(new_config.get("ORGANIZED_PATH", FALLBACK_CONFIG["ORGANIZED_PATH"])).resolve()
    LOCAL_TORRENT_DOWNLOAD_PATH = Path(
        new_config.get("LOCAL_TORRENT_DOWNLOAD_PATH", FALLBACK_CONFIG["LOCAL_TORRENT_DOWNLOAD_PATH"])
    ).resolve()
    REMOTE_TORRENT_DOWNLOAD_PATH = new_config.get("REMOTE_TORRENT_DOWNLOAD_PATH") or None
    
    global mam_session_cookies
    mam_session_cookies = {"mam_id": app.config.get("MAM_ID")}
    
    # --- CRITICAL FIX HERE ---
    global torrent_client 
    try:
        torrent_client = get_torrent_client(app.config)
        app.logger.info(f"Initialized torrent client: {app.config.get('TORRENT_CLIENT_TYPE', 'qbittorrent')}")
    except Exception as e:
        app.logger.error(f"Failed to initialize torrent client: {e}")
        torrent_client = None

# --- ACTIVE MONITORING & CACHING LOGIC ---

def start_monitoring_loop():
    global monitor_task
    if monitor_task is None or monitor_task.done():
        monitor_task = asyncio.create_task(monitor_downloads_loop())
        app.logger.info("Active download monitoring loop started.")

async def monitor_downloads_loop():
    app.logger.info("Entered monitoring loop.")
    client_session_active = False
    
    while True:
        normalize_monitoring_state_keys()

        # First, check and process pending MID resolutions
        if pending_mid_resolutions and torrent_client:
            try:
                all_torrents = await torrent_client.get_torrents_with_metadata()
                mids_to_remove = []
                
                for mid, pending_data in pending_mid_resolutions.items():
                    # Look for this MID in the torrents list
                    for torrent in all_torrents:
                        comment = torrent.get('comment', '')
                        mid_match = re.search(r'MID=(\d+)', comment)
                        
                        if mid_match and mid_match.group(1) == mid:
                            # Found the torrent! Extract hash and move to monitoring_state
                            torrent_hash = normalize_info_hash(torrent.get('hash', ''))
                            if torrent_hash:
                                app.logger.info(f"Resolved MID {mid} to hash {torrent_hash}")
                                
                                # Save metadata with hash
                                metadata = load_database()
                                metadata[torrent_hash] = pending_data["metadata"]
                                save_database(metadata)
                                
                                # Add to monitoring state
                                monitoring_state[torrent_hash] = {
                                    "added_at": pending_data["added_at"]
                                }
                                
                                mids_to_remove.append(mid)
                                break
                    
                    # Check timeout (e.g., 60 seconds)
                    if time.time() - pending_data["added_at"] > 60:
                        app.logger.warning(f"MID {mid} resolution timed out after 60s")
                        mids_to_remove.append(mid)
                
                # Clean up resolved/timed-out MIDs
                for mid in mids_to_remove:
                    del pending_mid_resolutions[mid]
                    
            except Exception as e:
                app.logger.warning(f"[MONITOR] Failed to resolve pending MIDs: {e}")
        
        if not monitoring_state:
            if client_session_active:
                app.logger.debug("[MONITOR] Queue empty. Going idle.")
            client_session_active = False 
            await asyncio.sleep(5)
            continue

        try:
            if not torrent_client:
                app.logger.warning("Monitor loop: Client not ready.")
                await asyncio.sleep(5)
                continue

            # OPTIMIZED LOGIN
            if not client_session_active:
                try:
                    await torrent_client.login()
                    client_session_active = True
                    app.logger.debug("[MONITOR] Session established with torrent client.")
                except Exception as e:
                    app.logger.error(f"[MONITOR] Login failed: {e}")
                    await asyncio.sleep(5)
                    continue

            active_hashes = [normalize_info_hash(h) for h in monitoring_state.keys() if normalize_info_hash(h)]
            torrents_info = {}
            
            # FETCH DATA
            try:
                if hasattr(torrent_client, 'get_torrent_info_batch'):
                    batch_res = await torrent_client.get_torrent_info_batch(active_hashes)
                    if 'torrents' in batch_res:
                        torrents_info = batch_res['torrents']
                else:
                    for h in active_hashes:
                        info = await torrent_client.get_torrent_info(h)
                        if info: torrents_info[h] = info
                
                if torrents_info:
                    status_summary = []
                    for h, info in torrents_info.items():
                        p = info.get('progress', 0) * 100
                        eta = info.get('eta', 8640000)
                        eta_str = f"{eta}s" if eta < 8640000 else "Unknown"
                        status_summary.append(f"{h[:6]}..: {p:.1f}% (ETA: {eta_str})")
                    
                    app.logger.debug(f"[MONITOR] Polled {len(torrents_info)} item(s): {', '.join(status_summary)}")
                    
                    # Broadcast torrent progress updates via SSE
                    await broadcast_payload({
                        "event": "torrent-progress",
                        "torrents": torrents_info
                    })
                    
                    # Broadcast client health status
                    await broadcast_payload({
                        "event": "client-status",
                        "status": "connected",
                        "display_name": get_client_display_name(app.config.get('TORRENT_CLIENT_TYPE', 'qbittorrent'))
                    })

            except Exception as e:
                app.logger.warning(f"[MONITOR] Fetch failed (session expired?): {e}")
                client_session_active = False
                # Broadcast client disconnected status
                await broadcast_payload({
                    "event": "client-status",
                    "status": "disconnected"
                })
                await asyncio.sleep(1)
                continue

            finished_hashes = []
            current_time = time.time()
            
            # Logic Flags
            force_high_freq = False
            valid_etas_for_sleep = []

            for h, info in torrents_info.items():
                # UPDATE CACHE
                torrent_status_cache[h] = {
                    "data": info,
                    "timestamp": current_time
                }

                # --- HISTORY & STABILITY LOGIC ---
                # 1. Lazy Init History in monitoring_state
                # This ensures we don't crash if the key is missing
                state_entry = monitoring_state.get(h)
                if not state_entry: continue 
                eta_history = state_entry.setdefault('eta_history', [])

                tracker_error = str(info.get('tracker_error') or '').strip()
                if tracker_error:
                    last_tracker_error = str(state_entry.get('last_tracker_error') or '').strip()
                    if tracker_error != last_tracker_error:
                        state_entry['last_tracker_error'] = tracker_error
                        torrent_name = str(info.get('name') or h[:8])
                        await broadcast_toast(f"Torrent client (Transmission) tracker error for '{torrent_name}': {tracker_error}", "warning")
                elif state_entry.get('last_tracker_error'):
                    state_entry['last_tracker_error'] = ''

                state = info.get('state', 'unknown')
                progress = info.get('progress', 0)
                current_eta = info.get('eta', 8640000)
                
                # Check completion
                is_complete = state in ['uploading', 'stalledUP', 'forcedUP', 'pausedUP', 'checkingUP']
                if progress >= 1 and state not in ['error', 'missingFiles']:
                    is_complete = True

                if is_complete:
                    finished_hashes.append(h)
                    continue # Skip frequency logic for finished items

                # 2. Update Rolling History (Max 5 items)
                eta_history.append(current_eta)
                if len(eta_history) > 5:
                    eta_history.pop(0)

                # 3. Check "Initial Phase" (First 15s)
                added_at = state_entry.get('added_at', 0)
                if current_time - added_at < 15:
                    force_high_freq = True
                    continue # Must poll fast, ignore stability
                
                # 4. Check Stability (Rolling 5, min >= 80% of max)
                is_stable = False
                if len(eta_history) == 5:
                    min_eta = min(eta_history)
                    max_eta = max(eta_history)
                    # If max is 0, we are effectively finished, treat as stable
                    if max_eta == 0 or min_eta >= (0.8 * max_eta):
                        is_stable = True
                
                if not is_stable:
                    force_high_freq = True
                else:
                    # Stable: Allow this ETA to influence the sleep calculation
                    valid_etas_for_sleep.append(current_eta)

            # --- END LOOP OVER ITEMS ---

            for h in finished_hashes:
                app.logger.info(f"[MONITOR] Torrent {h} finished.")

                if h in torrents_info:
                    final_status = {h: torrents_info[h]}
                    await broadcast_payload({
                        "event": "torrent-progress",
                        "torrents": final_status
                    })

                if app.config.get("AUTO_ORGANIZE_ON_ADD"):
                    try:
                        success, msg = await _perform_organization(h)
                        if success:
                            app.logger.info(f"[MONITOR] Auto-organize succeeded for {h}: {msg}")
                        else:
                            app.logger.warning(f"[MONITOR] Auto-organize failed for {h}: {msg}")
                    except Exception as e:
                        app.logger.error(f"[MONITOR] Exception during auto-organize for {h}: {e}", exc_info=True)
                if h in monitoring_state:
                    del monitoring_state[h]
                
                # Push updated MAM stats when a torrent finishes
                await push_mam_stats()

            for h in active_hashes:
                if h not in torrents_info and h not in finished_hashes:
                    added_at = monitoring_state.get(h, {}).get('added_at', 0)
                    if current_time - added_at > 10:
                        app.logger.warning(f"[MONITOR] Torrent {h} disappeared. Stopping monitor.")
                        del monitoring_state[h]

            if not monitoring_state:
                app.logger.info("[MONITOR] All tracked downloads finished.")
                await asyncio.sleep(2) 
                continue

            # --- SLEEP CALCULATION ---
            sleep_reason = ""
            if force_high_freq:
                sleep_time = 1
                sleep_reason = "High Freq (Initial/Unstable)"
            elif valid_etas_for_sleep:
                lowest_eta = min(valid_etas_for_sleep)
                # ETA / 2 logic
                sleep_time = max(2, int(lowest_eta / 2))
                # Cap at 3 seconds for responsive SSE updates to frontend
                sleep_time = min(sleep_time, 3)
                sleep_reason = f"Stable Backoff (min ETA: {lowest_eta}s)"
            else:
                # Fallback if we have active downloads but none fell into valid buckets
                # (e.g. all < 5 history points but > 15s old? Treat as unstable)
                sleep_time = 1
                sleep_reason = "Fallback (Insufficient Data)"
            
            app.logger.debug(f"[MONITOR] Sleeping {sleep_time}s [{sleep_reason}]")
            await asyncio.sleep(sleep_time)

        except Exception as e:
            app.logger.error(f"[MONITOR] Error in loop: {e}")
            client_session_active = False
            await asyncio.sleep(5)


# --- IP STATE MANAGEMENT ---

def load_ip_state():
    if os.path.exists(IP_STATE_FILE):
        try:
            with open(IP_STATE_FILE, "r") as f:
                return json.load(f).get("last_ip")
        except (json.JSONDecodeError, FileNotFoundError):
            pass
    return None

def save_ip_state(ip):
    with open(IP_STATE_FILE, "w") as f:
        json.dump({"last_ip": ip}, f, indent=4)

async def force_update_ip():
    async with app.app_context():
        app.logger.info("Forcing manual IP update for dynamic seedbox.")
        if not app.config.get("MAM_ID"): return
        api_cookies = {"mam_id": app.config.get("MAM_ID")}
        try:
            update_url = "https://t.myanonamouse.net/json/dynamicSeedbox.php"
            async with httpx.AsyncClient() as client:
                update_response = await client.get(update_url, cookies=api_cookies, timeout=15)
                update_response.raise_for_status()
                update_data = update_response.json()
                if new_ip := update_data.get("ip"):
                    save_ip_state(new_ip)
        except Exception as e:
            app.logger.error(f"Error calling dynamic seedbox update: {e}")

async def check_and_update_ip():
    async with app.app_context():
        if not app.config.get("MAM_ID"): return
        api_cookies = {"mam_id": app.config.get("MAM_ID")}
        try:
            ip_check_url = f"{app.config.get('MAM_API_URL')}/json/jsonIp.php"
            async with httpx.AsyncClient() as client:
                response = await client.get(ip_check_url, cookies=api_cookies, timeout=10)
                response.raise_for_status()
                current_ip = response.json().get("ip")
                if not current_ip: return
        except Exception:
            return
            
        last_ip = load_ip_state()
        if current_ip != last_ip:
            await force_update_ip()


# --- VIP AUTO-BUY SCHEDULER ---
async def auto_buy_vip():
    """Automatically purchase VIP credit to keep it topped up."""
    async with app.app_context():
        if not app.config.get("MAM_ID"):
            app.logger.warning("VIP auto-buy scheduled but MAM_ID not configured")
            return
        
        if not await login_mam():
            app.logger.warning("VIP auto-buy failed: Could not log into MAM")
            return

        user_data = await fetch_mam_json_load()
        if not user_data:
            app.logger.warning("[AUTO-VIP] Could not fetch user data")
            return
        max_weeks = calculate_vip_topup_weeks(user_data)
        if max_weeks < VIP_MIN_WEEKS:
            app.logger.info(f"[AUTO-VIP] Skipping top-up: max purchase {max_weeks:.2f} weeks (< {VIP_MIN_WEEKS})")
            return
        
        try:
            epoch_ms = int(time.time() * 1000)
            api_url = f"{app.config.get('MAM_API_URL')}/json/bonusBuy.php/"
            params = {
                'spendtype': 'VIP',
                'duration': 'max',
                '_': epoch_ms
            }
            
            async with httpx.AsyncClient() as client:
                response = await client.get(api_url, params=params, cookies=mam_session_cookies, timeout=10)
                update_cookies(response)
                response.raise_for_status()
                result = response.json()
                
                if result.get('success'):
                    app.logger.info(f"[AUTO-VIP] Purchase successful - {result.get('amount')} weeks added, Remaining bonus: {result.get('seedbonus')}")
                    await broadcast_payload({
                        'event': 'vip_purchase',
                        'success': True,
                        'amount': result.get('amount'),
                        'seedbonus': result.get('seedbonus')
                    })
                else:
                    app.logger.warning(f"[AUTO-VIP] Purchase failed: {result}")
        except Exception as e:
            app.logger.error(f"[AUTO-VIP] Error during scheduled VIP purchase: {e}")



# --- UPLOAD CREDIT AUTO-BUY SCHEDULER ---
async def check_and_buy_upload():
    """Check ratio, buffer, and bonus thresholds, auto-purchase upload credit if needed."""
    async with app.app_context():
        if not app.config.get("MAM_ID"):
            return
        
        if not await login_mam():
            app.logger.warning("[AUTO-UPLOAD] Could not log into MAM")
            return
        
        # Get current user stats
        stats = await get_user_stats()
        if not stats:
            app.logger.warning("[AUTO-UPLOAD] Could not fetch user stats")
            return
        
        ratio_check_enabled = app.config.get("AUTO_BUY_UPLOAD_ON_RATIO", False)
        buffer_check_enabled = app.config.get("AUTO_BUY_UPLOAD_ON_BUFFER", False)
        bonus_check_enabled = app.config.get("AUTO_BUY_UPLOAD_ON_BONUS", False)
        
        purchased = False
        current_seedbonus = stats.get('seedbonus')

        async def purchase_upload(amount, reason):
            _, chunks = build_upload_chunks(amount)
            if not chunks:
                app.logger.warning(f"[AUTO-UPLOAD-{reason.upper()}] Invalid amount: {amount} GB (multiples of {UPLOAD_CREDIT_MIN_GB} only)")
                return False, None

            total_purchased = 0
            final_seedbonus = None
            api_url = f"{app.config.get('MAM_API_URL')}/json/bonusBuy.php/"

            async with httpx.AsyncClient() as client:
                for chunk in chunks:
                    try:
                        if len(chunks) > 1 and chunk != chunks[0]:
                            await asyncio.sleep(0.5)

                        epoch_ms = int(time.time() * 1000)
                        params = {'spendtype': 'upload', 'amount': chunk, '_': epoch_ms}
                        response = await client.get(api_url, params=params, cookies=mam_session_cookies, timeout=10)
                        update_cookies(response)
                        response.raise_for_status()
                        result = response.json()

                        if result.get('success'):
                            try:
                                amt_added = result.get('amount')
                                val = float(amt_added) if str(amt_added).lower() != 'max' else 0
                                total_purchased += val
                            except Exception:
                                pass

                            final_seedbonus = result.get('seedbonus')
                        else:
                            app.logger.warning(f"[AUTO-UPLOAD-{reason.upper()}] Purchase failed: {result}")
                            return False, None
                    except Exception as e:
                        app.logger.error(f"[AUTO-UPLOAD-{reason.upper()}] Error: {e}")
                        return False, None

            if total_purchased <= 0:
                app.logger.warning(f"[AUTO-UPLOAD-{reason.upper()}] Purchase failed: no upload credit added")
                return False, None

            app.logger.info(f"[AUTO-UPLOAD-{reason.upper()}] Purchase successful - {total_purchased} GB added")
            await broadcast_payload({
                'event': 'upload_purchase',
                'success': True,
                'amount': total_purchased,
                'reason': reason,
                'seedbonus': final_seedbonus
            })
            return True, final_seedbonus
        
        # Check ratio threshold
        if ratio_check_enabled:
            ratio_threshold = float(app.config.get("AUTO_BUY_UPLOAD_RATIO_THRESHOLD", 1.5))
            if stats['ratio'] < ratio_threshold:
                amount = float(app.config.get("AUTO_BUY_UPLOAD_RATIO_AMOUNT", 50))
                app.logger.info(f"[AUTO-UPLOAD] Ratio {stats['ratio']} below threshold {ratio_threshold}, purchasing {amount} GB")
                
                success, seedbonus = await purchase_upload(amount, "ratio")
                if success:
                    purchased = True
                    if seedbonus is not None:
                        current_seedbonus = seedbonus
        
        # Check buffer threshold (only if we didn't already purchase)
        if buffer_check_enabled and not purchased:
            buffer_threshold = float(app.config.get("AUTO_BUY_UPLOAD_BUFFER_THRESHOLD", 10))
            if stats['buffer_gb'] < buffer_threshold:
                amount = float(app.config.get("AUTO_BUY_UPLOAD_BUFFER_AMOUNT", 50))
                app.logger.info(f"[AUTO-UPLOAD] Buffer {stats['buffer_gb']:.2f} GB below threshold {buffer_threshold} GB, purchasing {amount} GB")
                
                success, seedbonus = await purchase_upload(amount, "buffer")
                if success and seedbonus is not None:
                    current_seedbonus = seedbonus

        if bonus_check_enabled:
            bonus_threshold = float(app.config.get("AUTO_BUY_UPLOAD_BONUS_THRESHOLD", 5000))
            amount = float(app.config.get("AUTO_BUY_UPLOAD_BONUS_AMOUNT", 50))
            seedbonus = current_seedbonus
            if seedbonus is None:
                refreshed = await get_user_stats()
                if not refreshed:
                    app.logger.warning("[AUTO-UPLOAD-BONUS] Could not refresh user stats before bonus check")
                    return
                seedbonus = refreshed.get('seedbonus')

            while seedbonus is not None and seedbonus >= bonus_threshold:
                app.logger.info(f"[AUTO-UPLOAD] Bonus points {seedbonus} >= threshold {bonus_threshold}, purchasing {amount} GB")
                success, new_seedbonus = await purchase_upload(amount, "bonus")
                if not success:
                    break
                if new_seedbonus is None:
                    refreshed = await get_user_stats()
                    if not refreshed:
                        app.logger.warning("[AUTO-UPLOAD-BONUS] Could not refresh user stats after purchase")
                        break
                    new_seedbonus = refreshed.get('seedbonus')
                if new_seedbonus is None:
                    break
                if new_seedbonus >= seedbonus:
                    app.logger.warning("[AUTO-UPLOAD-BONUS] Bonus points did not decrease after purchase; stopping loop")
                    break
                seedbonus = new_seedbonus


# --- SESSION AND API HELPERS ---
def update_cookies(response):
    global mam_session_cookies
    if "set-cookie" in response.headers:
        cookies = dict(response.cookies)
        mam_session_cookies.update(cookies)

async def login_mam():
    """Checks if the MAM session is valid by attempting to load user data."""
    data = await fetch_mam_json_load()
    return data is not None

async def push_mam_stats():
    """Fetch MAM user stats and broadcast them via SSE."""
    user_data = await fetch_mam_json_load()
    
    if not user_data:
        app.logger.debug("[MAM-STATS] Not logged in or fetch failed, skipping stats push")
        return

    # Format seedbonus for display
    if seedbonus := user_data.get("seedbonus"):
        user_data["seedbonus_formatted"] = f"{seedbonus:,}"
    
    # Broadcast MAM stats via SSE
    await broadcast_payload({
        "event": "mam-stats",
        "data": user_data
    })
    app.logger.debug("[MAM-STATS] Successfully pushed MAM stats via SSE")


def normalize_spaces(text: str) -> str:
    if text is None:
        return ""
    return re.sub(r"\s+", " ", str(text).strip())


def build_wildcard_clause(text: str) -> str:
    words = [w.strip("*").strip() for w in normalize_spaces(text).split() if w.strip("*").strip()]
    meaningful_words = [w for w in words if len(re.sub(r"\W+", "", w, flags=re.UNICODE)) >= 2]
    words_for_query = meaningful_words if meaningful_words else words
    wildcard_words = [f"*{w}*" for w in words_for_query]
    return " ".join(wildcard_words)


def build_author_initials_variant(query: str) -> str | None:
    """
    Normalize likely author-initial patterns to MAM style:
    "jk rowling" -> "j k rowling", "j.k. rowling" -> "j k rowling", "rr martin" -> "r r martin".
    Returns None when no likely initials pattern is found or query seems advanced/operator-heavy.
    """
    if query is None:
        return None
    query = str(query)
    if not query.strip():
        return None
    if re.search(r'[|()"*]', query):
        return None

    raw_tokens = [token for token in normalize_spaces(query).split(" ") if token]
    if not raw_tokens:
        return None

    has_non_initial_word = any(len(re.sub(r"[^A-Za-z]", "", token)) >= 3 for token in raw_tokens)
    if not has_non_initial_word:
        return None

    normalized_tokens = []
    changed = False

    for raw in raw_tokens:
        token = raw.strip()
        if not token:
            continue

        stripped = token.strip(".,;:!?")
        if not stripped:
            continue

        # Dotted initials chunk like "j.k." or "j.r.r."
        dotted_letters = re.findall(r"[A-Za-z]", stripped)
        is_dotted_initials = "." in stripped and len(dotted_letters) >= 2 and re.fullmatch(r"[A-Za-z.]+", stripped)
        if is_dotted_initials:
            normalized_tokens.extend(dotted_letters)
            changed = True
            continue

        letters_only = re.sub(r"[^A-Za-z]", "", stripped)
        is_initial_cluster = (
            len(letters_only) == 2
            and letters_only.isalpha()
            and not re.search(r"[AEIOUaeiou]", letters_only)
        )
        if is_initial_cluster:
            normalized_tokens.extend(list(letters_only))
            changed = True
            continue

        normalized_tokens.append(stripped)

    if not changed:
        return None

    variant = normalize_spaces(" ".join(normalized_tokens))
    if not variant:
        return None
    if variant.lower() == normalize_spaces(query).lower():
        return None
    return variant

# --- QUART ROUTES ---
@app.route('/mam/autosuggest', methods=['GET'])
async def mam_autosuggest():
    def autosuggest_response(payload, cache_status="miss"):
        response = jsonify(payload)
        response.headers["X-Autosuggest-Cache"] = cache_status
        return response

    # 1. Capture and clean input
    raw_query = request.args.get('q', '').strip()
    
    # Basic length check on the raw input
    if len(raw_query) < 3:
        return autosuggest_response([])

    # 2. Prepare MAM Request
    if not mam_session_cookies.get("mam_id"):
        return autosuggest_response([])

    url = f"{app.config['MAM_API_URL']}/tor/js/loadSearchJSONbasic.php"
    
    def get_nonempty_list(name):
        return [v for v in request.args.getlist(name) if v]

    lang_ids = get_nonempty_list("language_ids") or get_nonempty_list("language_ids[]")
    if not lang_ids:
        lang_value = request.args.get("language", "English")
        if lang_value.isdigit():
            lang_ids = [lang_value]
        else:
            lang_ids = [str(language_dict.get(lang_value, 1))]

    search_field_names = [
        "search_in_title",
        "search_in_author",
        "search_in_series",
        "search_in_narrator",
    ]
    has_search_param = any(request.args.get(name) is not None for name in search_field_names)
    default_search_fields = {
        "search_in_title": True,
        "search_in_author": True,
        "search_in_series": True,
        "search_in_narrator": False,
    }

    def checkbox_state(name):
        val = request.args.get(name)
        if val is None:
            return default_search_fields.get(name, False) if not has_search_param else False
        return val in ("true", "on", "1", "yes")

    def bool_arg(name, default=False):
        val = request.args.get(name)
        if val is None:
            return default
        return str(val).strip().lower() in {"true", "on", "1", "yes"}

    title_on = checkbox_state("search_in_title")
    author_on = checkbox_state("search_in_author")
    series_on = checkbox_state("search_in_series")
    narrator_on = checkbox_state("search_in_narrator")
    cache_only = bool_arg("cache_only", False)
    if author_on and not title_on:
        title_on = True

    base_wildcard_clause = build_wildcard_clause(raw_query)
    author_variant = build_author_initials_variant(raw_query) if author_on else None
    variant_wildcard_clause = build_wildcard_clause(author_variant) if author_variant else ""
    quoted_variant = author_variant.replace('"', '').strip() if author_variant else ""

    # Query fallback order for autosuggest (loadSearchJSONbasic.php):
    # 1) normalized author-friendly variant for initials (best recall)
    # 2) original wildcard query
    # 3) exact normalized phrase
    query_candidates = []
    if variant_wildcard_clause:
        query_candidates.append(variant_wildcard_clause)
    if base_wildcard_clause:
        query_candidates.append(base_wildcard_clause)
    if quoted_variant:
        query_candidates.append(f"\"{quoted_variant}\"")
    query_candidates = list(dict.fromkeys([clause for clause in query_candidates if clause]))

    if not query_candidates:
        return autosuggest_response([])

    suggestion_limit = app.config.get("MAX_AUTOCOMPLETE_RESULTS", FALLBACK_CONFIG["MAX_AUTOCOMPLETE_RESULTS"])
    try:
        suggestion_limit = int(suggestion_limit)
    except (TypeError, ValueError):
        suggestion_limit = FALLBACK_CONFIG["MAX_AUTOCOMPLETE_RESULTS"]
    if suggestion_limit <= 0:
        suggestion_limit = FALLBACK_CONFIG["MAX_AUTOCOMPLETE_RESULTS"]

    # Over-fetch to better fill the final deduped list.
    fetch_perpage = min(max(suggestion_limit * 3, suggestion_limit), 200)

    # Construct parameters to match the main search filters
    params = {
        "tor[text]": query_candidates[0],
        "tor[sortType]": "seeders",
        "perpage": fetch_perpage,
        "thumbnail": "true",
        
        # Dynamic Filters from URL params
        "tor[browse_lang][]": lang_ids,
        "tor[srchIn][title]": "on" if title_on else "off",
        "tor[srchIn][author]": "on" if author_on else "off",
        "tor[srchIn][narrator]": "on" if narrator_on else "off",
        "tor[srchIn][series]": "on" if series_on else "off",
        "tor[searchType]": "all"
    }

    # Apply Category Filter
    main_cats = [m for m in request.args.getlist("main_cat") if m]
    if not main_cats:
        main_cats = [m for m in request.args.getlist("media_type") if m]
    effective_main_cats = []
    if main_cats and "all" not in main_cats:
        effective_main_cats = list(dict.fromkeys(main_cats))
        params["tor[main_cat][]"] = effective_main_cats

    def fuzzy_score(query_text, candidate_text):
        query_norm = normalize_spaces(query_text)
        candidate_norm = normalize_spaces(candidate_text)
        if not query_norm or not candidate_norm:
            return 0.0
        if fuzz is not None:
            return float(fuzz.WRatio(query_norm, candidate_norm))
        return float(SequenceMatcher(None, query_norm.lower(), candidate_norm.lower()).ratio() * 100.0)

    selected_primary_fields = [
        name for name, enabled in (
            ("title", title_on),
            ("author", author_on),
            ("series", series_on),
            ("narrator", narrator_on),
        ) if enabled
    ]
    if not selected_primary_fields:
        selected_primary_fields = ["title"]
    field_priority = {"title": 0, "series": 1, "author": 2, "narrator": 3}
    seen_by_primary_type = {"title": set(), "author": set(), "series": set(), "narrator": set()}

    cache_key = make_autosuggest_cache_key(
        raw_query=raw_query,
        query_candidates=query_candidates,
        lang_ids=lang_ids,
        main_cats=effective_main_cats,
        selected_fields=selected_primary_fields,
        suggestion_limit=suggestion_limit,
    )
    cached_payload = await get_cached_autosuggest_response(cache_key)
    if cached_payload is not None:
        return autosuggest_response(cached_payload, "hit")
    if cache_only:
        return autosuggest_response([])

    # 3. Enforce Rate Limit before calling upstream
    wait_or_success = await mam_autosuggest_limiter.acquire()
    if wait_or_success is not True:
        await asyncio.sleep(wait_or_success)

    def normalize_dedupe_text(value):
        return normalize_spaces(value).lower()

    try:
        async with httpx.AsyncClient() as client:
            raw_results = []
            for query_text in query_candidates:
                params["tor[text]"] = query_text
                resp = await client.get(url, params=params, cookies=mam_session_cookies, timeout=5.0)
                update_cookies(resp)
                if resp.status_code != 200:
                    continue
                data = resp.json()
                raw_results = data.get('data', [])
                if raw_results:
                    break

            if not raw_results:
                return autosuggest_response([])
            phrase_candidates = []

            for row_index, row in enumerate(raw_results):
                # -- Parse Author --
                author_str = "Unknown"
                try:
                    if row.get('author_info'):
                        auth_data = json.loads(row['author_info'])
                        author_str = ", ".join(str(v) for v in auth_data.values())
                except:
                    pass

                # -- Parse Narrator --
                narrator_str = ""
                try:
                    if row.get('narrator_info'):
                        narr_data = json.loads(row['narrator_info'])
                        narrator_str = ", ".join(str(v) for v in narr_data.values())
                except:
                    pass

                # -- Parse Series --
                series_str = ""
                try:
                    if row.get('series_info'):
                        ser_data = json.loads(row['series_info'])
                        if ser_data:
                            first_series = next(iter(ser_data.values()))
                            name = first_series[0]
                            series_str = str(name)
                except:
                    pass

                title_str = str(row.get('title', 'Unknown'))
                author_display = normalize_spaces(author_str)
                if author_display.lower() == "unknown":
                    author_display = ""
                candidate_texts = {
                    "title": title_str,
                    "author": author_str,
                    "series": series_str,
                    "narrator": narrator_str,
                }
                for field in selected_primary_fields:
                    primary_text = normalize_spaces(candidate_texts.get(field, ""))
                    dedupe_key = normalize_dedupe_text(primary_text)
                    if not dedupe_key:
                        continue
                    if dedupe_key in seen_by_primary_type[field]:
                        continue

                    seen_by_primary_type[field].add(dedupe_key)
                    try:
                        seeders = int(row.get("seeders", 0) or 0)
                    except (TypeError, ValueError):
                        seeders = 0

                    phrase_candidates.append({
                        "primary_type": field,
                        "primary_text": primary_text,
                        "author_text": author_display,
                        "seeders": seeders,
                        "score": fuzzy_score(raw_query, primary_text),
                        "row_index": row_index,
                        "field_priority": field_priority[field],
                    })

            phrase_candidates.sort(
                key=lambda item: (-item["score"], item["field_priority"], -item["seeders"], item["row_index"])
            )
            suggestions = [
                {
                    "primary_type": item["primary_type"],
                    "primary_text": item["primary_text"],
                    "author_text": item.get("author_text", ""),
                    "seeders": item["seeders"],
                    "match_score": round(item["score"], 2),
                }
                for item in phrase_candidates[:suggestion_limit]
            ]

            await set_cached_autosuggest_response(cache_key, suggestions)
            return autosuggest_response(suggestions)

    except Exception as e:
        app.logger.error(f"MAM Autosuggest Error: {e}")
        return autosuggest_response([])
    
    
@app.route('/mam/status', methods=['GET'])
async def mam_status(): 
    return jsonify({'status': 'connected' if await login_mam() else 'not connected'})

@app.route('/mam/user_data', methods=['GET'])
async def mam_user_data():
    user_data = await fetch_mam_json_load()
    
    if not user_data:
        return jsonify({'error': 'Not logged into MAM or failed to fetch data'}), 401
        
    if seedbonus := user_data.get("seedbonus"):
        user_data["seedbonus_formatted"] = f"{seedbonus:,}"
        
    return jsonify(user_data)

@app.route('/mam/buy_vip', methods=['POST'])
async def mam_buy_vip():
    """Buy VIP credit using bonus points. Accepts 'max' or specific weeks."""
    if not await login_mam():
        return jsonify({'success': False, 'error': 'Not logged into MAM'}), 401

    try:
        # Get JSON data to determine duration
        data = await request.get_json() or {}
        duration = data.get('duration', 'max') # Default to max if not specified
        if str(duration).lower() == 'max':
            user_data = await fetch_mam_json_load()
            max_weeks = calculate_vip_topup_weeks(user_data)
            if max_weeks < VIP_MIN_WEEKS:
                return jsonify({
                    'success': False,
                    'error': f"Minimum VIP purchase is {VIP_MIN_WEEKS} week."
                }), 400
        else:
            try:
                duration_val = float(duration)
            except (TypeError, ValueError):
                return jsonify({'success': False, 'error': 'Invalid duration format'}), 400
            if duration_val < VIP_MIN_WEEKS:
                return jsonify({
                    'success': False,
                    'error': f"Minimum VIP purchase is {VIP_MIN_WEEKS} week."
                }), 400

        # Get current epoch time in milliseconds for the request
        epoch_ms = int(time.time() * 1000)
        api_url = f"{app.config.get('MAM_API_URL')}/json/bonusBuy.php/"
        params = {
            'spendtype': 'VIP',
            'duration': duration,
            '_': epoch_ms
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(api_url, params=params, cookies=mam_session_cookies, timeout=10)
            update_cookies(response)
            response.raise_for_status()
            result = response.json()

            # Log the result
            if result.get('success'):
                app.logger.info(f"VIP purchase successful - Duration: {duration}, Amount added: {result.get('amount')} weeks, Remaining bonus: {result.get('seedbonus')}")
            else:
                app.logger.warning(f"VIP purchase failed: {result}")

            return jsonify(result)
    except Exception as e:
        app.logger.error(f"Error buying VIP credit: {e}")
        return jsonify({'success': False, 'error': 'Failed to purchase VIP'}), 503

@app.route('/mam/buy_upload', methods=['POST'])
async def mam_buy_upload():
    """
    Buy upload credit using 50/100 GB chunks.
    Accepts 'max' (computed from bonus points) or a specific multiple of 50 GB.
    """
    if not await login_mam():
        return jsonify({'success': False, 'error': 'Not logged into MAM'}), 401
    
    data = await request.get_json() or {}
    raw_amount = data.get('amount')

    # 1. Handle 'max' special case
    if str(raw_amount).lower() == 'max':
        stats = await get_user_stats()
        if not stats:
            return jsonify({'success': False, 'error': 'Could not fetch user stats'}), 503
        seedbonus = stats.get('seedbonus')
        if seedbonus is None:
            return jsonify({'success': False, 'error': 'Could not read bonus points'}), 503

        affordable_gb = math.floor(seedbonus / UPLOAD_CREDIT_COST_PER_GB)
        affordable_gb -= affordable_gb % UPLOAD_CREDIT_MIN_GB
        affordable_gb = min(affordable_gb, UPLOAD_CREDIT_MAX_GB)
        if affordable_gb < UPLOAD_CREDIT_MIN_GB:
            return jsonify({
                'success': False,
                'error': f'Insufficient bonus points to purchase {UPLOAD_CREDIT_MIN_GB} GB.'
            }), 400

        total, chunks = build_upload_chunks(affordable_gb)
        if not chunks:
            return jsonify({'success': False, 'error': 'Failed to calculate max affordable amount'}), 400

        app.logger.info(f"Processing 'max' upload purchase for {total} GB using chunks: {chunks}")

    # 2. Handle numeric amounts
    else:
        total, chunks = build_upload_chunks(raw_amount)
        if not chunks:
            return jsonify({
                'success': False,
                'error': f'Invalid amount: {raw_amount} GB. Valid amounts are multiples of {UPLOAD_CREDIT_MIN_GB} GB, up to {UPLOAD_CREDIT_MAX_GB} GB.'
            }), 400

        app.logger.info(f"Processing purchase for {total} GB using chunks: {chunks}")

    # 3. Execute the requests
    total_purchased = 0
    final_seedbonus = 0
    errors = []
    api_url = f"{app.config.get('MAM_API_URL')}/json/bonusBuy.php/"

    async with httpx.AsyncClient() as client:
        for chunk in chunks:
            try:
                # Rate limit safety sleep between multi-chunk requests
                if len(chunks) > 1 and chunk != chunks[0]:
                    await asyncio.sleep(0.5)

                epoch_ms = int(time.time() * 1000)
                params = {
                    'spendtype': 'upload', 
                    'amount': chunk, 
                    '_': epoch_ms
                }
                
                response = await client.get(api_url, params=params, cookies=mam_session_cookies, timeout=10)
                update_cookies(response)
                response.raise_for_status()
                result = response.json()

                if result.get('success'):
                    amt_added = result.get('amount')
                    # Handle 'max' return or numeric return
                    try:
                        val = float(amt_added) if str(amt_added).lower() != 'max' else 0
                        total_purchased += val
                    except: 
                        pass
                        
                    final_seedbonus = result.get('seedbonus')
                    app.logger.info(f"[BUY-UPLOAD] Chunk {chunk} success.")
                else:
                    msg = result.get('error') or result.get('message') or 'Unknown error'
                    app.logger.warning(f"[BUY-UPLOAD] Chunk {chunk} failed: {msg}")
                    errors.append(f"Failed on {chunk}: {msg}")
                    break # Stop on first failure
                    
            except Exception as e:
                app.logger.error(f"[BUY-UPLOAD] Exception on chunk {chunk}: {e}")
                errors.append(f"Error on {chunk}: {str(e)}")
                break

    # 4. Return result
    success = len(errors) == 0
    
    if total_purchased > 0:
        await push_mam_stats()
        
        msg = f"Purchased {total_purchased} GB successfully."
        
        if errors:
            msg += f" (Stopped early: {', '.join(errors)})"
            
        return jsonify({
            'success': success,
            'amount': total_purchased,
            'seedbonus': final_seedbonus,
            'message': msg
        })
    else:
        return jsonify({
            'success': False, 
            'error': '; '.join(errors) if errors else "Purchase failed."
        }), 400


@app.route('/mam/buy_personal_fl', methods=['POST'])
async def mam_buy_personal_fl():
    """Spend a personal freeleech token (wedge) on a specific torrent."""
    if not await login_mam():
        return jsonify({'success': False, 'error': 'Not logged into MAM'}), 401

    try:
        data = await request.get_json() or {}
        torrentid = data.get('torrentid') or data.get('torrent_id') or data.get('id')
        if torrentid is None:
            return jsonify({'success': False, 'error': 'Missing torrentid'}), 400

        try:
            torrentid = int(torrentid)
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': 'Invalid torrentid'}), 400

        result = await purchase_personal_fl_wedge(torrentid)
        return jsonify(result)
    except Exception as e:
        app.logger.error(f"Error buying personal freeleech: {e}")
        return jsonify({'success': False, 'error': 'Failed to spend freeleech token'}), 503


async def purchase_personal_fl_wedge(torrentid: int) -> dict:
    """Attempt to spend a personal freeleech wedge for a torrent id."""
    epoch_ms = int(time.time() * 1000)

    api_url = f"{app.config.get('MAM_API_URL')}/json/bonusBuy.php/{epoch_ms}"
    params = {
        'spendtype': 'personalFL',
        'torrentid': int(torrentid),
        'timestamp': epoch_ms,
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(api_url, params=params, cookies=mam_session_cookies, timeout=10)
        update_cookies(response)
        response.raise_for_status()
        result = response.json()

    if result.get('success'):
        await push_mam_stats()
    else:
        app.logger.warning(f"[BUY-PERSONAL-FL] Purchase failed: {result}")

    return result
        

# Helper function to clean the specific MAM JSON format
def parse_mam_metadata(json_str, is_series=False):
    if not json_str:
        return ""
    try:
        data = json.loads(json_str)
        if not data:
            return ""
        
        items = []
        # Series format: {"id": ["Series Name", "Book Number", Total]}
        if is_series:
            for val in data.values():
                if isinstance(val, list) and len(val) >= 2:
                    # Formats as "Artemis Fowl #05"
                    items.append(f"{val[0]} #{val[1]}")
        
        # Author/Narrator format: {"id": "Name"}
        else:
            for val in data.values():
                items.append(str(val))
                
        # Join multiple (e.g. multiple authors) and unescape HTML
        return html.unescape(", ".join(items))
    except (json.JSONDecodeError, TypeError):
        # Fallback if it's not JSON, just return unescaped string
        return html.unescape(str(json_str))

async def fetch_mam_json_load():
    """
    Unified helper to fetch data from jsonLoad.php.
    Handles connection, cookies, and basic error logging.
    Returns the JSON dict on success, or None on failure.
    """
    url = app.config.get("MAM_API_URL")
    # Basic pre-check
    if not url or not mam_session_cookies.get("mam_id"): 
        return None

    try:
        api_url = f"{url}/jsonLoad.php"
        async with httpx.AsyncClient() as client:
            response = await client.get(api_url, cookies=mam_session_cookies, timeout=10)
            
            # Centralized cookie update
            update_cookies(response)
            
            response.raise_for_status()
            return response.json()
            
    except Exception as e:
        # Log the specific error here so calling functions don't have to
        app.logger.warning(f"[MAM-API] jsonLoad.php request failed: {e}")
        return None
    
async def get_user_stats():
    """Helper to fetch current user stats (ratio, uploaded, downloaded, seedbonus)."""
    data = await fetch_mam_json_load()
    
    if not data:
        return None
        
    try:
        # --- ROBUST NUMBER PARSER ---
        def safe_float(val):
            """Safely converts strings to float, handling commas, Infinity, and NaN."""
            if val is None: return 0.0
            if isinstance(val, (int, float)): return float(val)
            
            # Clean string: remove commas, whitespace
            s = str(val).strip().replace(',', '')
            
            # Handle Infinity / NaN
            if '∞' in s or 'inf' in s.lower():
                return float('inf')
            if 'nan' in s.lower() or '---' in s:
                return 0.0
                
            try:
                return float(s)
            except (ValueError, TypeError):
                app.logger.warning(f"Could not parse stat '{val}', defaulting to 0.0")
                return 0.0

        # Parse uploaded and downloaded (format: "1,234.45 GiB")
        def parse_size(size_str):
            if not size_str: return 0.0
            parts = size_str.split()
            if len(parts) != 2: return 0.0
            
            # Use safe_float here to handle commas in "1,234.56"
            value = safe_float(parts[0])
            unit = parts[1].upper()
            
            if 'TIB' in unit or 'TB' in unit: return value * 1024
            elif 'GIB' in unit or 'GB' in unit: return value
            elif 'MIB' in unit or 'MB' in unit: return value / 1024
            elif 'KIB' in unit or 'KB' in unit: return value / (1024 * 1024)
            return value
        
        uploaded_gb = parse_size(data.get('uploaded', '0 GiB'))
        downloaded_gb = parse_size(data.get('downloaded', '0 GiB'))
        
        # Now safe to use safe_float on these fields too
        ratio = safe_float(data.get('ratio', 0))
        seedbonus = safe_float(data.get('seedbonus', 0))
        
        return {
            'uploaded_gb': uploaded_gb,
            'downloaded_gb': downloaded_gb,
            'buffer_gb': uploaded_gb - downloaded_gb,
            'ratio': ratio,
            'seedbonus': seedbonus
        }
    except Exception as e:
        app.logger.error(f"Error parsing user stats: {e}")
        return None

async def fetch_torrent_file_from_mam(torrent_url: str) -> tuple[bytes | None, str | None]:
    """
    Downloads a .torrent file with MAM cookies so the app can upload raw bytes
    to qBittorrent instead of delegating URL fetch/authentication to qB.
    """
    if not torrent_url:
        return None, None

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
            response = await client.get(torrent_url, cookies=mam_session_cookies)
            update_cookies(response)
            response.raise_for_status()

        torrent_bytes = response.content
        if not torrent_bytes:
            return None, None

        filename = "download.torrent"
        content_disposition = response.headers.get("Content-Disposition", "")
        filename_match = re.search(
            r"filename\*?=(?:UTF-8''|\"|)?([^\";]+)",
            content_disposition,
            flags=re.IGNORECASE
        )
        if filename_match:
            filename = unquote(filename_match.group(1)).strip()
        else:
            basename = os.path.basename(urlparse(str(response.url)).path)
            if basename:
                filename = basename

        if not filename.lower().endswith(".torrent"):
            filename = f"{filename}.torrent"

        return torrent_bytes, filename
    except Exception as e:
        app.logger.error(f"Failed to download torrent file from MAM URL '{torrent_url}': {e}")
        return None, None
    
# --- GENERIC TORRENT CLIENT ROUTES ---
@app.route('/client/status', methods=['GET'])
async def client_status():
    if not torrent_client: return jsonify({"status": "error", "message": "Client not initialized"}), 500
    # Only login if needed (handled by client usually, but we force login in other places)
    try:
        return jsonify(await torrent_client.get_status())
    except:
        await torrent_client.login()
        return jsonify(await torrent_client.get_status())

@app.route('/client/categories', methods=['GET'])
async def client_categories():
    if not torrent_client: return jsonify({'error': 'Not connected'}), 401
    # Try fetch, if fail login
    try:
        categories = await torrent_client.get_categories()
    except:
        await torrent_client.login()
        categories = await torrent_client.get_categories()
    return jsonify(categories) if categories else (jsonify({'error': 'Failed'}), 500)

@app.route('/client/add', methods=['POST'])
async def client_add_torrent():
    """
    Handles the addition of a new torrent to the torrent client, with support for buffer checks, custom download paths, and auto-organization.
    Workflow:
    - Ensures the torrent client is initialized and logs in.
    - Parses incoming JSON data for torrent details, including optional custom_relative_path.
    - Checks if the user's buffer is sufficient to download the torrent; if not, returns a response with recommended upload credit.
    - If a MID (metadata ID) is present and auto-organization is enabled, adds the torrent immediately and stores metadata for later hash resolution.
    - If no MID or auto-organization is disabled, calculates the torrent hash and stores metadata for auto-organization.
    - Adds the torrent to the client and, if successful, starts monitoring for completion if auto-organization is enabled.
    Args:
        None (expects JSON data in the request body with keys such as 'torrent_url', 'author', 'title', 'id', 'category', 'size', 'series_info', 'main_cat', 'download_link', and optionally 'custom_relative_path').
    Returns:
        Flask Response: JSON response indicating success, error, or insufficient buffer, with appropriate HTTP status codes.
    """

    if not torrent_client:
        return jsonify({'error': 'Client not initialized'}), 500
    
    await torrent_client.login()
    incoming_data = await request.get_json()
    
    # --- NEW: Extract custom path ---
    custom_relative_path = incoming_data.get('custom_relative_path')
    custom_destination_path = incoming_data.get('custom_destination_path')
    # --------------------------------
    
    torrent_url = incoming_data.get('torrent_url') or incoming_data.get('url')
    author = incoming_data.get('author', 'Unknown')
    title = incoming_data.get('title', 'Unknown')
    id = incoming_data.get('id', '0')
    category = incoming_data.get('category', app.config.get("TORRENT_CLIENT_CATEGORY", ""))
    torrent_size_str = incoming_data.get('size', '0 GiB')  # e.g., "1.5 GiB"
    is_public_freeleech = False
    try:
        is_public_freeleech = int(incoming_data.get('free', 0) or 0) == 1
    except (ValueError, TypeError):
        is_public_freeleech = False

    if app.config.get("AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD", False):
        if is_public_freeleech:
            app.logger.info("[DOWNLOAD] Auto Freeleech wedge purchase skipped: torrent is already public freeleech.")
        else:
            torrent_id_for_fl = None
            try:
                if id not in (None, '', '0', 0):
                    torrent_id_for_fl = int(id)
            except (ValueError, TypeError):
                torrent_id_for_fl = None

            if torrent_id_for_fl is not None:
                try:
                    if await login_mam():
                        fl_result = await purchase_personal_fl_wedge(torrent_id_for_fl)
                        if fl_result.get('success'):
                            app.logger.info(f"[DOWNLOAD] Auto-purchased Freeleech wedge for torrent {torrent_id_for_fl}")
                        else:
                            app.logger.warning(
                                f"[DOWNLOAD] Auto Freeleech wedge purchase failed for torrent {torrent_id_for_fl}; continuing download. Result={fl_result}"
                            )
                    else:
                        app.logger.warning(
                            f"[DOWNLOAD] Auto Freeleech wedge purchase skipped for torrent {torrent_id_for_fl}; not logged into MAM. Continuing download."
                        )
                except Exception as e:
                    app.logger.warning(
                        f"[DOWNLOAD] Auto Freeleech wedge purchase errored for torrent {torrent_id_for_fl}; continuing download. Error={e}"
                    )
    
    # Check if download should be blocked due to low buffer
    if app.config.get("BLOCK_DOWNLOAD_ON_LOW_BUFFER", True) and await login_mam():
        stats = await get_user_stats()
        if stats:
            # Parse torrent size
            def parse_size(size_str):
                if not size_str:
                    return 0.0
                parts = size_str.split()
                if len(parts) != 2:
                    return 0.0
                try:
                    value = float(parts[0])
                except:
                    return 0.0
                unit = parts[1].upper()
                # Convert to GB
                if 'TIB' in unit or 'TB' in unit:
                    return value * 1024
                elif 'GIB' in unit or 'GB' in unit:
                    return value
                elif 'MIB' in unit or 'MB' in unit:
                    return value / 1024
                elif 'KIB' in unit or 'KB' in unit:
                    return value / (1024 * 1024)
                return value
            
            torrent_size_gb = parse_size(torrent_size_str)
            buffer_gb = stats['buffer_gb']
            
            if torrent_size_gb > buffer_gb:
                # Calculate how much upload credit needed
                needed_gb = torrent_size_gb - buffer_gb
                cost_per_gb = UPLOAD_CREDIT_COST_PER_GB  # bonus points
                
                # Round up to the nearest 50 GB, with a 50 GB minimum
                recommended_amount = max(
                    UPLOAD_CREDIT_MIN_GB,
                    math.ceil(needed_gb / UPLOAD_CREDIT_MIN_GB) * UPLOAD_CREDIT_MIN_GB
                )
                recommended_amount = min(recommended_amount, UPLOAD_CREDIT_MAX_GB)
                
                return jsonify({
                    'status': 'insufficient_buffer',
                    'buffer_gb': round(buffer_gb, 2),
                    'torrent_size_gb': round(torrent_size_gb, 2),
                    'needed_gb': round(needed_gb, 2),
                    'recommended_amount': recommended_amount,
                    'recommended_cost': int(recommended_amount * cost_per_gb),
                    'seedbonus': stats['seedbonus'],
                    'message': f'Insufficient buffer: {round(buffer_gb, 2)} GB available, {round(torrent_size_gb, 2)} GB needed'
                }), 400
    
    auto_organize_warning = None
    hash_val = None
    client_type = app.config.get("TORRENT_CLIENT_TYPE", "qbittorrent").lower()
    client_add_kwargs = {}

    supports_binary_add = {"qbittorrent", "transmission"}
    if client_type in supports_binary_add and torrent_url and torrent_url.lower().startswith(("http://", "https://")):
        torrent_file_data, torrent_filename = await fetch_torrent_file_from_mam(torrent_url)
        if torrent_file_data is None:
            return jsonify({'error': f'Failed to download torrent file from MAM before sending to {client_type}'}), 400
        client_add_kwargs = {
            "torrent_data": torrent_file_data,
            "torrent_filename": torrent_filename
        }
        hash_val = calculate_torrent_hash_from_bytes(torrent_file_data)
    
    # Check if MID is present - if so, skip hash calculation and resolve hash via MID polling.
    if id and id != '0':
        app.logger.info(f"MID {id} detected - adding torrent without hash calculation")
        
        # Add torrent immediately
        result = await torrent_client.add_torrent(torrent_url, category, mid=id, **client_add_kwargs)
        
        if result['status'] == 'success':
            # Extract additional metadata from incoming_data
            series_info = parse_series_info(incoming_data.get('series_info', ''))
            main_cat = incoming_data.get('main_cat', '')
            download_link = incoming_data.get('download_link', '')
            resolved_hash = normalize_info_hash(result.get('hash') or hash_val or '')

            metadata_payload = {
                "mid": id,
                "author": author,
                "title": title,
                "added_on": datetime.now().isoformat(),
                "status": "pending",
                "retry_count": 0,
                "series_info": series_info,
                "category": get_category_name(main_cat),
                "download_link": download_link,
                "custom_relative_path": custom_relative_path,
                "custom_destination_path": custom_destination_path,
            }

            if resolved_hash:
                if app.config.get("AUTO_ORGANIZE_ON_ADD"):
                    metadata = load_database()
                    metadata[resolved_hash] = metadata_payload
                    save_database(metadata)
                    app.logger.info(f"Saved metadata for torrent hash: {resolved_hash}")

                monitoring_state[normalize_info_hash(resolved_hash)] = {
                    "added_at": time.time()
                }
                start_monitoring_loop()

                response_data = {'message': result['message'], 'hash': resolved_hash}
                if auto_organize_warning:
                    response_data['warning'] = auto_organize_warning
                return jsonify(response_data)
            
            # Store in pending_mid_resolutions for later hash resolution
            pending_mid_resolutions[id] = {
                "added_at": time.time(),
                "metadata": metadata_payload
            }
            app.logger.info(f"Added MID {id} to pending_mid_resolutions for hash resolution")
            start_monitoring_loop()
            
            return jsonify({'message': result['message']})
        else:
            return jsonify({'error': result.get('message', 'Unknown error')}), 400
    
    # Fallback: No MID or auto-organize disabled - use old hash-based approach
    if not hash_val:
        app.logger.warning(f"WARNING: running hash calculation for torrent URL without MID: {torrent_url}")
        hash_val = await calculate_torrent_hash_from_url(torrent_url)
    
    if app.config.get("AUTO_ORGANIZE_ON_ADD"):
        if not hash_val:
            auto_organize_warning = "Unable to calculate hash - auto-organization will not work."
        else:
            # Extract additional metadata from incoming_data
            series_info = parse_series_info(incoming_data.get('series_info', ''))
            main_cat = incoming_data.get('main_cat', '')
            download_link = incoming_data.get('download_link', '')
            
            metadata = load_database()
            normalized_hash = normalize_info_hash(hash_val)
            metadata[normalized_hash] = {
                "mid": id, "author": author, "title": title,
                "added_on": datetime.now().isoformat(),
                "status": "pending", "retry_count": 0,
                "series_info": series_info,
                "category": get_category_name(main_cat),
                "download_link": download_link,
                "custom_relative_path": custom_relative_path,
                "custom_destination_path": custom_destination_path,
            }
            save_database(metadata)
            app.logger.info(f"Saved metadata for torrent hash: {normalized_hash}")
    
    result = await torrent_client.add_torrent(torrent_url, category, **client_add_kwargs)
    
    if result['status'] == 'success':
        resolved_hash = normalize_info_hash(result.get('hash') or hash_val or '')
        # Start progress monitoring regardless of auto-organize setting.
        if resolved_hash:
            monitoring_state[normalize_info_hash(resolved_hash)] = {
                "added_at": time.time()
            }
            start_monitoring_loop()
            app.logger.info(f"Registered {resolved_hash} for active monitoring.")

        response_data = {'message': result['message']}
        if resolved_hash:
            response_data['hash'] = resolved_hash
        if auto_organize_warning: response_data['warning'] = auto_organize_warning
        return jsonify(response_data)
    else:
        return jsonify({'error': result.get('message', 'Unknown error')}), 400

@app.route('/client/resolve_mid', methods=['POST'])
async def client_resolve_mid():
    """Resolve a MID (MyAnonamouse ID) to a torrent hash by querying the client."""
    if not torrent_client:
        return jsonify({'error': 'Client not initialized'}), 500
    
    data = await request.get_json()
    mid = data.get('mid')
    
    if not mid:
        return jsonify({'error': 'MID required'}), 400
    
    try:
        # Fetch all torrents with metadata from the client
        all_torrents = await torrent_client.get_torrents_with_metadata()
        
        # Search for the MID in torrent comments
        for torrent in all_torrents:
            comment = torrent.get('comment', '')
            if comment:
                mid_match = re.search(r'MID=(\d+)', comment)
                if mid_match and mid_match.group(1) == str(mid):
                    torrent_hash = torrent.get('hash', '')
                    if torrent_hash:
                        app.logger.debug(f"Resolved MID {mid} to hash {torrent_hash}")
                        return jsonify({'hash': torrent_hash, 'mid': mid})
        
        # Fallback: search local metadata DB for MID->hash mapping (hash-first add flow)
        metadata = load_database()
        for hash_key, entry in metadata.items():
            if str(entry.get('mid', '')) == str(mid):
                return jsonify({'hash': str(hash_key).strip().lower(), 'mid': str(mid)})

        # MID not found in client nor local DB
        return jsonify({'error': 'MID not found in client'}), 404
        
    except Exception as e:
        app.logger.error(f"Error resolving MID {mid}: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/client/info/<hash_val>', methods=['GET'])
async def client_torrent_info(hash_val):
    if hash_val in torrent_status_cache:
        entry = torrent_status_cache[hash_val]
        if time.time() - entry['timestamp'] < CACHE_TTL:
            return jsonify(entry['data'])

    if not torrent_client: return jsonify({'error': 'Client not initialized'}), 500
    
    # Optimistic fetch, fallback to login
    try:
        info = await torrent_client.get_torrent_info(hash_val)
    except:
        await torrent_client.login()
        info = await torrent_client.get_torrent_info(hash_val)

    if info:
        torrent_status_cache[hash_val] = {"data": info, "timestamp": time.time()}
        return jsonify(info)
    return jsonify({'error': 'Not found'}), 404

@app.route('/client/info/batch', methods=['POST'])
async def client_torrent_info_batch():
    data = await request.get_json()
    hash_list = data.get('hashes', [])
    if not hash_list: return jsonify({'torrents': []})
    
    cached_response = {}
    hashes_to_fetch = []
    current_time = time.time()
    
    for h in hash_list:
        if h in torrent_status_cache and (current_time - torrent_status_cache[h]['timestamp'] < CACHE_TTL):
            cached_response[h] = torrent_status_cache[h]['data']
        else:
            hashes_to_fetch.append(h)
    
    if not hashes_to_fetch:
        return jsonify({'torrents': cached_response})

    if not torrent_client: return jsonify({'error': 'Client not initialized'}), 500
    
    try:
        fetched_results = {}
        if hasattr(torrent_client, 'get_torrent_info_batch'):
            result = await torrent_client.get_torrent_info_batch(hashes_to_fetch)
            fetched_results = result.get('torrents', {})
        else:
            for hash_val in hashes_to_fetch:
                info = await torrent_client.get_torrent_info(hash_val)
                if info: fetched_results[hash_val] = info
        
        for h, info in fetched_results.items():
            torrent_status_cache[h] = {"data": info, "timestamp": current_time}
            cached_response[h] = info
            
        return jsonify({'torrents': cached_response})
    except Exception as e:
        # Retry once with login
        try:
            await torrent_client.login()
            if hasattr(torrent_client, 'get_torrent_info_batch'):
                result = await torrent_client.get_torrent_info_batch(hashes_to_fetch)
                fetched_results = result.get('torrents', {})
            else:
                for hash_val in hashes_to_fetch:
                    info = await torrent_client.get_torrent_info(hash_val)
                    if info: fetched_results[hash_val] = info
            return jsonify({'torrents': fetched_results})
        except Exception as e2:
            return jsonify({'error': str(e2)}), 503
    
def load_database():
    if not os.path.exists(DATABASE_FILE): return {}
    try:
        with open(DATABASE_FILE, "r") as f: return json.load(f)
    except: return {}

def save_database(data):
    with open(DATABASE_FILE, "w") as f: json.dump(data, f, indent=4)

def sanitize_filename(name: str) -> str:
    sanitized = re.sub(r'[<>:"/\\|?*]', '', name)
    return sanitized.strip('. ') if sanitized else "Untitled"

def get_category_name(category_num):
    """Convert MAM category number to text name."""
    category_map = {
        13: "audiobooks",
        14: "ebooks",
        15: "musicology",
        16: "radio"
    }
    try:
        return category_map.get(int(category_num), "unknown")
    except (ValueError, TypeError):
        return "unknown"

def parse_series_info(series_info_str):
    """Parse series_info from JSON string to object. Returns {} if empty or invalid."""
    if not series_info_str:
        return {}
    try:
        return json.loads(series_info_str)
    except (json.JSONDecodeError, TypeError):
        return {}

async def broadcast_payload(payload: dict):
    """Broadcast a generic payload to all connected SSE clients."""
    payload_json = json.dumps(payload)
    disconnected = set()
    # Fix for "Set changed size during iteration" error
    for queue in list(connected_websockets):
        try:
            await queue.put(payload_json)
        except Exception:
            # Remove immediately, safe because we are iterating a list copy
            connected_websockets.discard(queue)

async def broadcast_toast(message: str, category: str = "primary"):
    """Broadcast a toast notification to all connected SSE clients."""
    await broadcast_payload({"event": "toast", "message": message, "type": category})
    
@app.route('/calculate_hash', methods=['POST'])
async def get_torrent_hash():
    data = await request.get_json()
    url = data.get('url')
    if not url: return jsonify({'error': 'URL required'}), 400
    app.logger.warning(f"WARNING: running hash calculation for torrent URL: {url}")
    hash_val = await calculate_torrent_hash_from_url(url)
    return jsonify({'hash': hash_val}) if hash_val else (jsonify({'error': 'Failed'}), 500)

# --- SEARCH ROUTES & HELPERS ---
def parse_author_info(info):
    try: return ", ".join(json.loads(info).values())
    except: return "Unknown"

def format_date(date_string):
    try: return datetime.strptime(date_string, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
    except: return "Unknown"

def rank_results(results):
    if not results: return []
    max_seeders = max(r.get('seeders', 0) for r in results) if results else 1
    for r in results:
        r["author_info"] = parse_author_info(r.get("author_info", ""))
        r["narrator_info"] = parse_author_info(r.get("narrator_info", ""))
        try:
            series_json = json.loads(r.get("series_info", ""))
            series_name, book_number = next(iter(series_json.values()))
            r["series_display"] = f"{series_name}, Book {book_number}" if book_number else series_name
        except:
            r["series_display"] = ""
        r["added"] = format_date(r.get("added", "Unknown"))
        filetype_score = {'m4b': 50, 'mp3': 30}.get(r.get('filetype'), 10)
        seeders_score = (r.get('seeders', 0) / max_seeders * 30) if max_seeders > 0 else 0
        r['score'] = round(filetype_score + seeders_score, 1)
    return sorted(results, key=lambda x: x['score'], reverse=True)

@app.route('/mam/search', methods=['GET'])
async def mam_search():
    if not await login_mam(): 
        return await render_template(
            "partials/results.html",
            error_message="Login failed",
            RESULTS_DISPLAY_FIELDS=app.config.get(
                "RESULTS_DISPLAY_FIELDS",
                FALLBACK_CONFIG["RESULTS_DISPLAY_FIELDS"]
            ),
        )
    query = request.args.get("query", "").strip()
    search_started_at = time.monotonic()

    # Used by templates to decide whether VIP Freeleech applies (fl_vip).
    is_vip_active = False
    try:
        user_data = await fetch_mam_json_load()
        vip_until = (user_data or {}).get('vip_until')
        if vip_until:
            vip_dt = datetime.fromisoformat(str(vip_until).strip().replace(' ', 'T'))
            is_vip_active = vip_dt > datetime.utcnow()
    except Exception:
        is_vip_active = False

    def get_nonempty_list(name):
        return [v for v in request.args.getlist(name) if v]

    search_field_names = [
        "search_in_title",
        "search_in_author",
        "search_in_series",
        "search_in_narrator",
        "search_in_description",
        "search_in_tags",
        "search_in_filenames",
    ]
    has_search_param = any(request.args.get(name) is not None for name in search_field_names)
    default_search_fields = {
        "search_in_title": True,
        "search_in_author": True,
        "search_in_series": True,
        "search_in_narrator": False,
        "search_in_description": False,
        "search_in_tags": False,
        "search_in_filenames": False,
    }

    def checkbox_state(name):
        val = request.args.get(name)
        if val is None:
            return default_search_fields.get(name, False) if not has_search_param else False
        return val in ("true", "on", "1", "yes")

    title_on = checkbox_state("search_in_title")
    author_on = checkbox_state("search_in_author")
    series_on = checkbox_state("search_in_series")
    narrator_on = checkbox_state("search_in_narrator")
    description_on = checkbox_state("search_in_description")
    tags_on = checkbox_state("search_in_tags")
    filenames_on = checkbox_state("search_in_filenames")
    hide_downloaded = checkbox_state("hide_downloaded")
    if author_on and not title_on:
        title_on = True

    lang_ids = get_nonempty_list("language_ids") or get_nonempty_list("language_ids[]")
    if not lang_ids:
        lang_value = request.args.get("language", "English")
        if lang_value.isdigit():
            lang_ids = [lang_value]
        else:
            lang_ids = [str(language_dict.get(lang_value, 1))]

    params = {
        "tor[sortType]": "default",
        "perpage": app.config.get("MAX_SEARCH_RESULTS", FALLBACK_CONFIG["MAX_SEARCH_RESULTS"]),
        "thumbnail": "true",
        "dlLink": "true",
        "tor[browse_lang][]": lang_ids,
        "tor[srchIn][title]": "on" if title_on else "off",
        "tor[srchIn][author]": "on" if author_on else "off",
        "tor[srchIn][narrator]": "on" if narrator_on else "off",
        "tor[srchIn][series]": "on" if series_on else "off",
        "tor[srchIn][description]": "on" if description_on else "off",
        "tor[srchIn][tags]": "on" if tags_on else "off",
        "tor[srchIn][filenames]": "on" if filenames_on else "off",
        "tor[searchType]": request.args.get("searchType", "all"),
        "isbn": "true", "description": "true", "mediaInfo": "true"
    }
    if query:
        search_text = query
        if author_on:
            author_variant = build_author_initials_variant(query)
            if author_variant:
                quoted_variant = author_variant.replace('"', '').strip()
                if quoted_variant:
                    search_text = f"({query} | \"{quoted_variant}\")"
        params["tor[text]"] = search_text
    main_cats = [m for m in request.args.getlist("main_cat") if m]
    if not main_cats:
        main_cats = [m for m in request.args.getlist("media_type") if m]
    if main_cats and "all" not in main_cats:
        params["tor[main_cat][]"] = list(dict.fromkeys(main_cats))

    if search_scope := request.args.get("search_scope"):
        params["tor[searchIn]"] = search_scope

    if category_ids := get_nonempty_list("category_ids") or get_nonempty_list("category_ids[]"):
        params["tor[cat][]"] = category_ids

    if flag_ids := get_nonempty_list("flag_ids") or get_nonempty_list("flag_ids[]"):
        params["tor[browseFlags][]"] = flag_ids
        params["tor[browseFlagsHideVsShow]"] = request.args.get("flags_mode", "0")

    if start_date := request.args.get("start_date"):
        params["tor[startDate]"] = start_date
    if end_date := request.args.get("end_date"):
        params["tor[endDate]"] = end_date

    min_size = request.args.get("min_size")
    max_size = request.args.get("max_size")
    if min_size:
        params["tor[minSize]"] = min_size
    if max_size:
        params["tor[maxSize]"] = max_size
    if (min_size or max_size) and (size_unit := request.args.get("size_unit")):
        params["tor[unit]"] = size_unit

    stat_mappings = {
        "min_seeders": "tor[minSeeders]",
        "max_seeders": "tor[maxSeeders]",
        "min_leechers": "tor[minLeechers]",
        "max_leechers": "tor[maxLeechers]",
        "min_snatched": "tor[minSnatched]",
        "max_snatched": "tor[maxSnatched]"
    }
    for arg_name, tor_name in stat_mappings.items():
        if value := request.args.get(arg_name):
            params[tor_name] = value

    headers = {"Cookie": "; ".join([f"{k}={v}" for k, v in mam_session_cookies.items()])}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{app.config['MAM_API_URL']}/tor/js/loadSearchJSONbasic.php", params=params, headers=headers)
            update_cookies(response)
            response.raise_for_status()
            json_data = response.json()
            results = json_data.get("data", [])
            
            # --- STEP 1: Rank Results FIRST ---
            # We must rank BEFORE cleaning because rank_results expects raw JSON strings
            ranked = rank_results(results)
            
            base_dl_url = f"{app.config['MAM_API_URL']}/tor/download.php/"
            
            # --- STEP 2: Clean Data for Display ---
            # Now we decode HTML entities and fix formatting on the sorted list
            for item in ranked:
                # 1. Handle Download Links
                if dl_hash := item.get('dl'): 
                    item['download_link'] = base_dl_url + dl_hash
                else: 
                    item['download_link'] = '' 

                # 2. Handle Thumbnails
                if not item.get('thumbnail'):
                    if item.get('id'):
                        item['thumbnail'] = f"https://cdn.myanonamouse.net/t/p/small/{item['id']}.webp"
                    else:
                        cat = item.get('category', '')
                        item['thumbnail'] = f"https://static.myanonamouse.net/pic/cats/3/{cat}.png"

                # 3. Decode Metadata (Author, Narrator, Series)
                # Note: rank_results may have already partially parsed these into strings.
                # parse_mam_metadata handles both JSON strings AND plain strings safely.
                item['author_info'] = parse_mam_metadata(item.get('author_info', ''))
                item['narrator_info'] = parse_mam_metadata(item.get('narrator_info', ''))
                
                # Overwrite series_display with our cleaner, HTML-decoded version
                item['series_display'] = parse_mam_metadata(item.get('series_info', ''), is_series=True)

                language_id = str(item.get("language", "")).strip()
                language_name = LANGUAGE_BY_ID.get(language_id)
                if not language_name:
                    language_name = item.get("lang_code") or item.get("language") or "Unknown"
                item["language_name"] = language_name

            # ... Rest of your function ...
            client_status_data = await torrent_client.get_status() if torrent_client else {"status": "error"}
            client_connected = client_status_data.get("status") == "success"
            categories = await torrent_client.get_categories() if client_connected else {}
            
            mid_to_hash = {}
            if client_connected and torrent_client:
                try:
                    all_torrents = await torrent_client.get_torrents_with_metadata()
                    for torrent in all_torrents:
                        comment = torrent.get('comment', '')
                        if comment:
                            mid_match = re.search(r'MID=(\d+)', comment)
                            if mid_match:
                                mid = mid_match.group(1)
                                torrent_hash = torrent.get('hash', '')
                                if torrent_hash:
                                    mid_to_hash[mid] = torrent_hash
                except Exception as e:
                    app.logger.warning(f"Failed to fetch torrents with metadata: {e}")
            
            for item in ranked:
                item_id = str(item.get('id', ''))
                if item_id in mid_to_hash:
                    item['my_snatched'] = 1
            
            metadata = load_database()
            for item in ranked:
                if item.get('my_snatched') == 1:
                    item_id = str(item.get('id', ''))
                    torrent_hash = mid_to_hash.get(item_id)
                    if torrent_hash and torrent_hash not in metadata:
                        metadata[torrent_hash] = {
                            "mid": item_id,
                            "author": item.get('author_info', ''), 
                            "title": item.get('title', ''),
                            "added_on": datetime.now().isoformat(),
                            "status": "unknown",
                            "retry_count": 0,
                            "series_info": item.get('series_display', ''), 
                            "category": get_category_name(item.get('main_cat', '')),
                            "download_link": item.get('download_link', '')
                        }
            
            if any(item.get('my_snatched') == 1 for item in ranked):
                save_database(metadata)

            display_results = ranked
            if hide_downloaded:
                display_results = [
                    item for item in ranked
                    if str(item.get('my_snatched', 0)) != "1"
                ]

            search_duration_ms = (time.monotonic() - search_started_at) * 1000
            app.logger.info(
                f"[SEARCH] results={len(display_results)} query_len={len(query)} "
                f"scope={params.get('tor[searchIn]', 'torrents')} duration_ms={search_duration_ms:.1f}"
            )
            
            return await render_template(
                "partials/results.html",
                results=display_results,
                CLIENT_STATUS="CONNECTED" if client_connected else "NOT CONNECTED",
                categories=categories,
                TORRENT_CLIENT_CATEGORY=app.config.get("TORRENT_CLIENT_CATEGORY", ""),
                DESTINATION_PATHS=app.config.get("DESTINATION_PATHS", FALLBACK_CONFIG["DESTINATION_PATHS"]),
                TYPE_SPECIFIC_TORRENT_CATEGORIES=app.config.get(
                    "TYPE_SPECIFIC_TORRENT_CATEGORIES",
                    FALLBACK_CONFIG["TYPE_SPECIFIC_TORRENT_CATEGORIES"],
                ),
                IS_VIP_ACTIVE=is_vip_active,
                RESULTS_DISPLAY_FIELDS=app.config.get(
                    "RESULTS_DISPLAY_FIELDS",
                    FALLBACK_CONFIG["RESULTS_DISPLAY_FIELDS"]
                ),
            )
    except Exception as e:
        app.logger.error(f"[SEARCH] Failed query_len={len(query)}: {e}", exc_info=True)
        return await render_template(
            "partials/results.html",
            error_message=f"Error: {e}",
            DESTINATION_PATHS=app.config.get("DESTINATION_PATHS", FALLBACK_CONFIG["DESTINATION_PATHS"]),
            TORRENT_CLIENT_CATEGORY=app.config.get("TORRENT_CLIENT_CATEGORY", ""),
            TYPE_SPECIFIC_TORRENT_CATEGORIES=app.config.get(
                "TYPE_SPECIFIC_TORRENT_CATEGORIES",
                FALLBACK_CONFIG["TYPE_SPECIFIC_TORRENT_CATEGORIES"],
            ),
            RESULTS_DISPLAY_FIELDS=app.config.get(
                "RESULTS_DISPLAY_FIELDS",
                FALLBACK_CONFIG["RESULTS_DISPLAY_FIELDS"]
            ),
        )

@app.route("/")
async def index():
    # Determine display name dynamically from the class
    c_type = app.config.get("TORRENT_CLIENT_TYPE", "qbittorrent")
    display_name = get_client_display_name(c_type)
    
    # NEW: Get list of all registered clients
    available_clients = get_available_clients()

    # Fetch categories for the modal
    categories = {}
    if torrent_client:
        try:
            status = await torrent_client.get_status()
            if status.get("status") == "success":
                categories = await torrent_client.get_categories()
        except Exception:
            pass

    language_choices = sorted(language_dict.items(), key=lambda item: item[0].lower())

    return await render_template(
        "index.html",
        CLIENT_DISPLAY_NAME=display_name,
        AVAILABLE_CLIENTS=available_clients,  # Pass the list here
        categories=categories,
        AUTO_ORGANIZE_MEDIA_TYPES=AUTO_ORGANIZE_MEDIA_TYPES,
        LANGUAGE_CHOICES=language_choices,
        LANGUAGE_MAP=language_dict,
        DEFAULT_LANGUAGE_ID=language_dict.get("English", 1),
        **app.config
    )
    

async def cleanup_cache_task():
    """Deletes files in the cache directory older than 30 days and enforces size limit."""
    max_age = 30 * 24 * 60 * 60  # 30 days in seconds
    
    while True:
        try:
            now = time.time()
            cutoff = now - max_age
            
            if os.path.exists(THUMB_CACHE_DIR):
                # Get all files with their stats
                file_stats = []
                for filename in os.listdir(THUMB_CACHE_DIR):
                    filepath = os.path.join(THUMB_CACHE_DIR, filename)
                    if os.path.isfile(filepath):
                        stat = os.stat(filepath)
                        file_stats.append({
                            'path': filepath,
                            'mtime': stat.st_mtime,
                            'size': stat.st_size
                        })
                
                # 1. Delete files older than 30 days
                files_deleted_age = 0
                for file_info in file_stats[:]:
                    if file_info['mtime'] < cutoff:
                        try:
                            os.remove(file_info['path'])
                            file_stats.remove(file_info)
                            files_deleted_age += 1
                        except Exception as e:
                            app.logger.warning(f"Failed to delete old cache file {file_info['path']}: {e}")
                
                if files_deleted_age > 0:
                    app.logger.info(f"[CACHE-CLEANUP] Deleted {files_deleted_age} files older than 30 days")
                
                # 2. Enforce size limit by deleting oldest files first
                # --- FIX START ---
                try:
                    limit_mb = int(app.config.get("THUMBNAIL_CACHE_MAX_SIZE_MB", 500))
                except ValueError:
                    limit_mb = 500 # Fallback if config is malformed
                
                max_size_bytes = limit_mb * 1024 * 1024
                # --- FIX END ---

                total_size = sum(f['size'] for f in file_stats)
                
                if total_size > max_size_bytes:
                    # Sort by modification time (oldest first)
                    file_stats.sort(key=lambda x: x['mtime'])
                    
                    files_deleted_size = 0
                    while total_size > max_size_bytes and file_stats:
                        oldest = file_stats.pop(0)
                        try:
                            os.remove(oldest['path'])
                            total_size -= oldest['size']
                            files_deleted_size += 1
                        except Exception as e:
                            app.logger.warning(f"Failed to delete cache file for size limit {oldest['path']}: {e}")
                    
                    if files_deleted_size > 0:
                        app.logger.info(f"[CACHE-CLEANUP] Deleted {files_deleted_size} oldest files to enforce {limit_mb}MB size limit (freed {(sum(f['size'] for f in file_stats[:files_deleted_size]) / 1024 / 1024):.2f} MB)")
                            
        except Exception as e:
            app.logger.error(f"Error during cache cleanup: {e}")
        
        # Sleep for 24 hours before checking again
        await asyncio.sleep(86400)

@app.route('/system/public_ip')
async def get_public_ip():
    """
    Fetches the backend's public IP address.
    """

    def extract_ip(raw_text):
        try:
            payload = json.loads(raw_text)
        except json.JSONDecodeError:
            payload = None

        candidates = []
        if isinstance(payload, dict):
            candidates.extend(payload.get(key) for key in ("clientIp", "ip", "origin"))
        candidates.append(raw_text)

        for candidate in candidates:
            if not candidate:
                continue
            for token in re.split(r"[\s,]+", str(candidate).strip()):
                token = token.strip().strip("[]")
                if not token:
                    continue
                try:
                    return str(ipaddress.ip_address(token))
                except ValueError:
                    continue

        return None

    resolvers = [
        ("icanhazip.com", "https://icanhazip.com"),
        ("api.ipify.org", "https://api.ipify.org"),
        ("ifconfig.me", "https://ifconfig.me/ip"),
    ]

    try:
        # We use httpx instead of os.system('curl') because it is async,
        # non-blocking, and works reliably in serverless environments.
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for resolver_name, resolver_url in resolvers:
                try:
                    response = await client.get(
                        resolver_url,
                        headers={"Accept": "application/json, text/plain;q=0.9, */*;q=0.8"},
                        timeout=5.0,
                    )
                    response.raise_for_status()

                    resolved_ip = extract_ip(response.text)
                    if resolved_ip:
                        return jsonify({'ip': resolved_ip})

                    app.logger.warning(
                        f"Public IP resolver {resolver_name} returned an unusable response"
                    )
                except Exception as resolver_error:
                    app.logger.warning(
                        f"Public IP resolver {resolver_name} failed: {resolver_error}"
                    )
    except Exception as e:
        app.logger.error(f"Failed to fetch public IP: {e}")

    app.logger.error("Failed to fetch public IP from all configured resolvers")
    return jsonify({'error': 'Could not fetch IP'}), 500
    
FETCH_SEMAPHORE = asyncio.Semaphore(200)

@app.route("/proxy_thumbnail")
async def proxy_thumbnail():
    url = request.args.get("url")
    if not url or UPSTREAM_CLIENT is None: return "Error", 400
    
    cache_enabled = app.config.get("ENABLE_FILESYSTEM_THUMBNAIL_CACHE", True)
    
    # --- Cache Read ---
    # We cache based on the REQUESTED url (the one with the '0' timestamp).
    # The content stored will be the final image.
    cache_key = hashlib.md5(url.encode()).hexdigest()
    cache_path = os.path.join(THUMB_CACHE_DIR, cache_key)
    
    if cache_enabled:
        os.makedirs(THUMB_CACHE_DIR, exist_ok=True)
        if os.path.exists(cache_path):
            if time.time() - os.path.getmtime(cache_path) < 2592000:
                response = await send_file(cache_path)
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                response.headers["X-mousesearch-Cache-Status"] = "HIT"
                return response
            
    # --- Upstream Fetch with Manual Redirect Handling ---
    fwd_headers = {h: request.headers.get(h) for h in ("If-None-Match", "If-Modified-Since", "Range") if request.headers.get(h)}
    
    async with FETCH_SEMAPHORE:
        # We allow up to 3 redirects manually to ensure we attach cookies every time
        redirect_count = 0
        current_url = url
        
        while redirect_count < 3:
            req = UPSTREAM_CLIENT.build_request("GET", current_url, headers=fwd_headers, cookies=mam_session_cookies)
            
            # Disable auto-follow so we can inspect the headers ourselves
            r = await UPSTREAM_CLIENT.send(req, stream=True, follow_redirects=False)
            
            if r.status_code in (301, 302, 303, 307, 308):
                await r.aclose() # Close the stream for the redirect response
                redirect_loc = r.headers.get('Location')
                if not redirect_loc:
                    break # Should not happen on valid redirect
                
                # Handle relative redirects if necessary (though MAM usually sends absolute)
                if redirect_loc.startswith('/'):
                    from urllib.parse import urljoin
                    current_url = urljoin(current_url, redirect_loc)
                else:
                    current_url = redirect_loc
                    
                redirect_count += 1
                continue # Loop again with new URL and FRESH cookies
            else:
                # We found the final destination (200 OK or 404, etc)
                break

        # --- Process Final Response (Standard Logic) ---
        passthrough = {h: r.headers.get(h) for h in ("Content-Type", "Content-Length", "Cache-Control", "ETag", "Last-Modified", "Accept-Ranges", "Content-Range") if r.headers.get(h)}
        passthrough.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        
        if r.status_code == 304:
            await r.aclose()
            return Response(status=304, headers=passthrough)
            
        async def body():
            temp_path = cache_path + ".tmp"
            should_cache = cache_enabled and r.status_code == 200
            try:
                file_handle = open(temp_path, 'wb') if should_cache else None
                
                async for chunk in r.aiter_bytes(): 
                    if file_handle: file_handle.write(chunk)
                    yield chunk
                
                if file_handle:
                    file_handle.close()
                    os.rename(temp_path, cache_path)
            except Exception:
                if should_cache and os.path.exists(temp_path): 
                    os.remove(temp_path)
                raise
            finally: 
                await r.aclose()

        response = Response(body(), status=r.status_code, headers=passthrough)
        response.headers["X-mousesearch-Cache-Status"] = "MISS" if cache_enabled else "DISABLED"
        return response

@app.route("/api/settings", methods=["GET", "POST"])
async def api_settings():
    if request.method == "GET":
        env_values = read_env_values()
        default_template = env_values.get("DEFAULT_RELATIVE_PATH_TEMPLATE") or FALLBACK_CONFIG["REL_PATH_TEMPLATE"]
        return jsonify({
            "DEFAULT_RELATIVE_PATH_TEMPLATE": default_template,
            "REL_PATH_TEMPLATE": app.config.get("REL_PATH_TEMPLATE", FALLBACK_CONFIG["REL_PATH_TEMPLATE"])
        })

    payload = await request.get_json(silent=True) or {}
    template_value = payload.get("DEFAULT_RELATIVE_PATH_TEMPLATE") or payload.get("REL_PATH_TEMPLATE")
    if template_value is None:
        return jsonify({"status": "error", "message": "Missing DEFAULT_RELATIVE_PATH_TEMPLATE"}), 400

    update_env_value("DEFAULT_RELATIVE_PATH_TEMPLATE", template_value)
    os.environ["DEFAULT_RELATIVE_PATH_TEMPLATE"] = str(template_value)

    config_to_update = app.config.copy()
    config_to_update["REL_PATH_TEMPLATE"] = template_value
    save_config(config_to_update)
    await load_new_app_config()

    return jsonify({
        "status": "success",
        "DEFAULT_RELATIVE_PATH_TEMPLATE": template_value
    })

@app.route("/update_settings", methods=["POST"])
async def update_settings():
    form = await request.form
    config_to_update = app.config.copy()
    boolean_fields = {"AUTO_ORGANIZE_ON_ADD", "AUTO_ORGANIZE_ON_SCHEDULE", "AUTO_ORGANIZE_USE_COPY", "HAPTICS_ENABLED", "ENABLE_DYNAMIC_IP_UPDATE", "AUTO_BUY_VIP", "AUTO_BUY_UPLOAD_ON_RATIO", "AUTO_BUY_UPLOAD_ON_BUFFER", "AUTO_BUY_UPLOAD_ON_BONUS", "BLOCK_DOWNLOAD_ON_LOW_BUFFER", "AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD"}
    for key in FALLBACK_CONFIG.keys():
        if key in boolean_fields: config_to_update[key] = key in form
        elif key in form: config_to_update[key] = form[key]

    raw_dest_paths = form.getlist("extra_dest_paths[]")
    raw_dest_defaults = form.getlist("extra_dest_defaults[]")
    destination_rows = []
    for i, raw_path in enumerate(raw_dest_paths):
        path = str(raw_path or "").strip()
        if not path:
            continue
        default_main_cat = str(raw_dest_defaults[i] if i < len(raw_dest_defaults) else "").strip()
        destination_rows.append({
            "path": path,
            "default_main_cat": default_main_cat,
            "default_torrent_category": "",
        })

    default_path = str(config_to_update.get("ORGANIZED_PATH", FALLBACK_CONFIG["ORGANIZED_PATH"]) or "").strip() or FALLBACK_CONFIG["ORGANIZED_PATH"]
    normalized_extras = normalize_destination_paths(destination_rows, default_path)
    normalized_destinations = [{"path": default_path, "default_main_cat": "", "default_torrent_category": ""}] + [
        row for row in normalized_extras
        if row.get("path") != default_path
        or row.get("default_main_cat")
    ]
    config_to_update["DESTINATION_PATHS"] = normalized_destinations
    config_to_update["ORGANIZED_PATH"] = normalized_destinations[0]["path"]

    raw_type_category_defaults = form.getlist("type_category_defaults[]")
    raw_type_category_values = form.getlist("type_category_values[]")
    type_category_rows = []
    for i, raw_default in enumerate(raw_type_category_defaults):
        default_main_cat = str(raw_default or "").strip()
        default_torrent_category = str(raw_type_category_values[i] if i < len(raw_type_category_values) else "").strip()
        if not default_main_cat or not default_torrent_category:
            continue
        type_category_rows.append({
            "default_main_cat": default_main_cat,
            "default_torrent_category": default_torrent_category,
        })

    config_to_update["TYPE_SPECIFIC_TORRENT_CATEGORIES"] = normalize_type_specific_torrent_categories(
        type_category_rows,
        normalized_destinations,
    )

    if form.get("TORRENT_CLIENT_PASSWORD"): config_to_update["TORRENT_CLIENT_PASSWORD"] = form.get("TORRENT_CLIENT_PASSWORD")
    save_config(config_to_update)
    await load_new_app_config()
    if app.config.get("ENABLE_DYNAMIC_IP_UPDATE"):
        scheduler.add_job(id='manual_ip_update_job', func=force_update_ip, trigger='date', run_date=datetime.now() + timedelta(seconds=2))
    
    # Update VIP auto-buy scheduler based on new settings
    if app.config.get("AUTO_BUY_VIP"):
        interval_hours = int(app.config.get("AUTO_BUY_VIP_INTERVAL_HOURS", 24))
        misfire_grace_seconds = max(1, int(interval_hours * 3600 * 0.8))
        scheduler.add_job(
            auto_buy_vip,
            'interval',
            hours=interval_hours,
            id='vip_buy_job',
            replace_existing=True,
            misfire_grace_time=misfire_grace_seconds,
        )
    else:
        # Remove the job if disabled
        try:
            scheduler.remove_job('vip_buy_job')
        except:
            pass
    
    # Update upload credit auto-buy scheduler based on new settings
    if (app.config.get("AUTO_BUY_UPLOAD_ON_RATIO")
            or app.config.get("AUTO_BUY_UPLOAD_ON_BUFFER")
            or app.config.get("AUTO_BUY_UPLOAD_ON_BONUS")):
        interval_hours = int(app.config.get("AUTO_BUY_UPLOAD_CHECK_INTERVAL_HOURS", 6))
        misfire_grace_seconds = max(1, int(interval_hours * 3600 * 0.8))
        scheduler.add_job(
            check_and_buy_upload,
            'interval',
            hours=interval_hours,
            id='upload_check_job',
            replace_existing=True,
            misfire_grace_time=misfire_grace_seconds,
        )
    else:
        # Remove the job if disabled
        try:
            scheduler.remove_job('upload_check_job')
        except:
            pass
    
    # Get the new display name from the source of truth
    new_type = config_to_update.get("TORRENT_CLIENT_TYPE")
    display_name = get_client_display_name(new_type)

    return jsonify({
        "status": "success", 
        "message": "Settings updated!",
        "client_display_name": display_name 
    })

@app.route("/update_result_display_fields", methods=["POST"])
async def update_result_display_fields():
    payload = await request.get_json(silent=True) or {}
    fields = payload.get("fields")
    if fields is None:
        return jsonify({"status": "error", "message": "Missing fields."}), 400

    normalized = normalize_result_display_fields(fields, [])
    config_to_update = app.config.copy()
    config_to_update["RESULTS_DISPLAY_FIELDS"] = normalized
    save_config(config_to_update)
    app.config["RESULTS_DISPLAY_FIELDS"] = normalized
    return jsonify({"status": "success", "fields": normalized})


@app.route("/update_default_search_filters", methods=["POST"])
async def update_default_search_filters():
    payload = await request.get_json(silent=True) or {}
    filters = payload.get("filters")
    if filters is None:
        return jsonify({"status": "error", "message": "Missing filters."}), 400

    normalized = normalize_search_filter_defaults(filters)
    config_to_update = app.config.copy()
    config_to_update["SEARCH_FILTER_DEFAULTS"] = normalized
    save_config(config_to_update)
    app.config["SEARCH_FILTER_DEFAULTS"] = normalized

    return jsonify({
        "status": "success",
        "message": "Default filters saved.",
        "filters": normalized
    })


# --- ORGANIZE LOGIC ---

async def _perform_organization(hash_val: str) -> tuple[bool, str]:
    """
    Performs the file organization for a given torrent hash.

    Note:
        If the torrent metadata contains a 'custom_relative_path', it will be used as the destination path
        (relative to ORGANIZED_PATH), taking precedence over the default Author/Title folder generation.
        If 'custom_relative_path' is not set, the destination will default to ORGANIZED_PATH/Author/Title.
    """
    metadata = load_database()
    if hash_val not in metadata: return False, f"No metadata for hash {hash_val}."
    status = metadata[hash_val].get('status', 'pending')
    if status == 'organized': return True, f"Already organized: {hash_val}."
    if status == 'unknown': return True, f"Torrent {hash_val} is marked as unknown - skipping organization."
    if metadata[hash_val].get('retry_count', 0) >= 3: return True, "Max retries exceeded."
    
    if not torrent_client: return False, "Client not initialized."
    # Try to rely on session, fall back to explicit login
    try:
        info = await torrent_client.get_torrent_info(hash_val)
    except Exception as e:
        app.logger.warning(f"[ORGANIZE] Initial client fetch failed for {hash_val}: {e}. Attempting login.")
        await torrent_client.login()
        try:
            info = await torrent_client.get_torrent_info(hash_val)
        except Exception as e:
            app.logger.error(f"[ORGANIZE] Client fetch error for {hash_val}: {e}")
            return False, f"Client fetch error: {e}"

    if not info: return False, f"Torrent {hash_val} not found in client."
    
    content_path = resolve_local_content_path(app.config, info)
    if content_path is None:
        return False, f"Unable to resolve source path for torrent {hash_val}."
    torrent_meta = metadata[hash_val]

    destination_root = str(torrent_meta.get('custom_destination_path') or ORGANIZED_PATH or "").strip()
    if not destination_root:
        destination_root = str(FALLBACK_CONFIG["ORGANIZED_PATH"])
    organized_path = Path(destination_root)
    
    # --- CHANGED LOGIC START ---
    if torrent_meta.get('custom_relative_path'):
        # Use user-defined path (strip leading slashes to ensure it stays relative)
        rel_path = torrent_meta['custom_relative_path'].strip('/\\')
        dest_path = organized_path / rel_path
    else:
        # Use default logic
        dest_path = organized_path / sanitize_filename(torrent_meta['author']) / sanitize_filename(torrent_meta['title'])
    # --- CHANGED LOGIC END ---
    
    # Wait up to 10s for the filesystem to settle (fix for "Move on Completion" race condition)
    for _ in range(5):
        if content_path.exists():
            break
        await asyncio.sleep(2)
    
    if not content_path.exists(): 
        app.logger.debug(f"[ORGANIZE] Source path missing: {content_path}")
        await broadcast_toast(f"Auto-organization failed for '{torrent_meta.get('title', 'Unknown')}': Source path missing", "danger")
        return False, f"Source missing: {content_path}"
    
    try: dest_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        app.logger.error(f"[ORGANIZE] Failed to create destination path {dest_path}: {e}")
        return False, f"Dest create failed: {e}"
    
    files_linked, files_exist = 0, 0
    
    if content_path.is_dir():
        source_files = content_path.rglob('*')
        base_path = content_path
    else:
        source_files = [content_path]
        base_path = content_path.parent  # Use parent so relative_to keeps the filename
    
    for source_file in source_files:
        if source_file.is_file():
            # NO FILTERING: Link/copy everything found in the torrent
            rel_path = source_file.relative_to(base_path)
            dest_file = dest_path / rel_path
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            if dest_file.exists(): 
                files_exist += 1
                app.logger.debug(f"[ORGANIZE] Exists: {dest_file}")
            else:
                try:
                    if app.config.get("AUTO_ORGANIZE_USE_COPY", False):
                        # Run copy in a separate thread to prevent blocking
                        await asyncio.to_thread(shutil.copy2, source_file, dest_file)
                        files_linked += 1
                        app.logger.debug(f"[ORGANIZE] Copied: {source_file} -> {dest_file}")
                    else:
                        os.link(source_file, dest_file)
                        files_linked += 1
                        app.logger.debug(f"[ORGANIZE] Linked: {source_file} -> {dest_file}")
                except Exception as e:
                    operation = "Copy" if app.config.get("AUTO_ORGANIZE_USE_COPY", False) else "Link"
                    app.logger.error(f"[ORGANIZE] {operation} error {source_file}: {e}")

    total = files_linked + files_exist
    if total == 0:
        metadata[hash_val]['retry_count'] += 1
        save_database(metadata)
        await broadcast_toast(f"Auto-organization failed for '{torrent_meta.get('title', 'Unknown')}': No files linked", "warning")
        return False, "No files found."
    
    metadata[hash_val]['status'] = 'organized'
    save_database(metadata)
    
    # User-friendly success message
    title = torrent_meta.get('title', 'Unknown')
    author = torrent_meta.get('author', 'Unknown Author')
    await broadcast_toast(f"Successfully auto-organized '{title}' by {author}", "success")
    
    # Return detailed message with both user-friendly text and technical details
    details = (
        f"Successfully auto-organized '{title}' by {author}. "
        f"Files: {files_linked} linked, {files_exist} already existed. "
        f"Source: {content_path}, Destination: {dest_path}"
    )
    app.logger.info(f"[ORGANIZE] {details}")
    return True, details

@app.route('/events')
async def events():
    """Server-Sent Events endpoint with heartbeat to prevent timeouts."""
    queue = asyncio.Queue()
    connected_websockets.add(queue)
    connected_at = time.monotonic()
    client_ip = _client_ip_from_headers()
    app.logger.debug(f"[SSE] Client connected ip={client_ip} active_clients={len(connected_websockets)}")

    async def event_stream():
        try:
            while True:
                # Wait for new data, but timeout every 15 seconds to send a heartbeat
                yield ": connected\n\n"
                try:
                    # Wait for a real message
                    data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {data}\n\n"
                except asyncio.TimeoutError:
                    # No message received in 15s; send a comment (heartbeat)
                    # Comments start with ':' and are ignored by the browser EventSource
                    yield ": keep-alive\n\n"
        finally:
            connected_websockets.discard(queue)
            connection_duration = time.monotonic() - connected_at
            app.logger.debug(
                f"[SSE] Client disconnected ip={client_ip} "
                f"duration_s={connection_duration:.1f} active_clients={len(connected_websockets)}"
            )

    return Response(event_stream(), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no' # Helpful for Nginx/proxies
    })

@app.route('/organize', methods=['POST'])
@app.route('/organize/<hash_val>', methods=['POST'])
async def organize_torrent_webhook(hash_val=None):
    async with app.app_context():
        if hash_val:
            try:
                success, msg = await _perform_organization(hash_val)
                return jsonify({'status': 'success' if success else 'error', 'message': msg}), 200 if success else 500
            except Exception as e:
                app.logger.error(f"[ORGANIZE] Exception during organization of {hash_val}: {e}", exc_info=True)
                return jsonify({'status': 'error', 'message': f'Internal error: {str(e)}'}), 500
        else:
            metadata = load_database()
            pending = [h for h, m in metadata.items() if m.get('status') == 'pending']
            results = {'succeeded': 0, 'failed': 0, 'errors': []}
            for h in pending:
                try:
                    s, m = await _perform_organization(h)
                    if s: results['succeeded'] += 1
                    else:
                        results['failed'] += 1
                        results['errors'].append({'hash': h[:8], 'message': m})
                except Exception as e:
                    results['failed'] += 1
                    error_msg = f"Exception: {str(e)}"
                    results['errors'].append({'hash': h[:8], 'message': error_msg})
                    app.logger.error(f"[ORGANIZE] Exception during organization of {h}: {e}", exc_info=True)
            
            # Determine overall status
            if results['failed'] > 0 and results['succeeded'] == 0:
                status_code = 500
                overall_status = 'error'
            elif results['failed'] > 0:
                status_code = 207  # Multi-Status (partial success)
                overall_status = 'partial'
            else:
                status_code = 200
                overall_status = 'success'
            
            return jsonify({'status': overall_status, 'results': results}), status_code

async def check_for_unorganized_torrents():
    """Safety net job."""
    async with app.app_context():
        app.logger.info("Running safety net organization job.")
        metadata = load_database()
        pending = [h for h, m in metadata.items() if m.get('status') == 'pending']
        for h in pending:
            try:
                success, msg = await _perform_organization(h)
                if not success:
                    app.logger.warning(f"[SAFETY NET] Organization failed for {h}: {msg}")
            except Exception as e:
                app.logger.error(f"[SAFETY NET] Exception during organization of {h}: {e}", exc_info=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=None, type=int)
    args = parser.parse_args()
    
    # Priority: CLI arg > PORT env var > hardcoded default (5000)
    port = args.port or int(os.getenv("PORT", 5000))
    
    app.run(host=args.host, port=port, debug=True, use_reloader=False)
