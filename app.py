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
from typing import Any

from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv, dotenv_values
from httpx import Limits, Timeout, AsyncHTTPTransport
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from urllib.parse import parse_qsl, unquote, urlparse

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
from hardcover.client import HardcoverAPIError, HardcoverClient
from hardcover.resolver import HardcoverBatchRunner, HardcoverEnrichmentConfig, HardcoverResolver

# --- SCHEDULER AND STATE SETUP ---
app = Quart(__name__)

UPSTREAM_CLIENT: httpx.AsyncClient | None = None
MAM_PROXY_CLIENT: httpx.AsyncClient | None = None
HARDCOVER_CLIENT: HardcoverClient | None = None
HARDCOVER_USER_BOOK_PRELOAD_ACTIVE = False

torrent_client = None
mam_session_cookies = {}
mam_session_cookie_lock = asyncio.Lock()
MAM_PROXY_FALLBACK_STATUS_CODES = {407, 502, 503, 504}
mam_proxy_route_state = {
    "route": "direct",
    "message": "No MAM proxy configured. MAM requests are going direct.",
    "last_error": "",
}

# --- Monitoring & Caching Globals ---
monitoring_state = {} 
monitor_task = None
torrent_status_cache = {}
CACHE_TTL = 2.0
pending_mid_resolutions = {}  # Maps MID -> {"added_at": timestamp, "metadata": {...}}

# --- SSE Globals ---
connected_websockets = set() 
hardcover_enrichment_batches = {}
HARDCOVER_ENRICHMENT_BATCH_TTL_SECONDS = 10 * 60
hardcover_series_response_cache = {}
HARDCOVER_SERIES_CACHE_TTL_SECONDS = 6 * 60 * 60

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
RESULTS_SORT_MODES = {
    "quality_desc",
    "date_uploaded_desc",
    "date_uploaded_asc",
    "author_asc",
    "author_desc",
    "title_asc",
    "title_desc",
    "seeders_desc",
    "seeders_asc",
    "snatches_desc",
    "snatches_asc",
    "size_desc",
    "size_asc",
}
DEFAULT_RESULTS_SORT_MODE = "quality_desc"
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


def normalize_results_sort_mode(value):
    normalized = str(value or "").strip()
    if normalized in RESULTS_SORT_MODES:
        return normalized
    return DEFAULT_RESULTS_SORT_MODE


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


def parse_size_to_gb(size_value, default=0.0):
    """Parse a tracker-style size string into GiB-equivalent GB."""
    if size_value is None:
        return default

    if isinstance(size_value, (int, float)) and not isinstance(size_value, bool):
        return float(size_value)

    text = str(size_value).strip().replace(",", "")
    if not text:
        return default

    match = re.fullmatch(r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))\s*([KMGT]?i?B|[KMGT]?B)?", text, re.IGNORECASE)
    if not match:
        return default

    try:
        value = float(match.group(1))
    except (ValueError, TypeError):
        return default

    unit = (match.group(2) or "GB").upper()
    if unit in {"TIB", "TB"}:
        return value * 1024
    if unit in {"GIB", "GB"}:
        return value
    if unit in {"MIB", "MB"}:
        return value / 1024
    if unit in {"KIB", "KB"}:
        return value / (1024 * 1024)
    if unit == "B":
        return value / (1024 * 1024 * 1024)
    return value


def coerce_legacy_gb_to_mb(value, default=0.0):
    gb_value = parse_size_to_gb(value, default=None)
    if gb_value is None:
        return default
    return gb_value * 1024


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
        interval_seconds = int(
            app.config.get(
                "DYNAMIC_IP_CHECK_INTERVAL_SECONDS",
                FALLBACK_CONFIG["DYNAMIC_IP_CHECK_INTERVAL_SECONDS"],
            )
        )
        misfire_grace_seconds = max(1, int(interval_seconds * 0.8))
        scheduler.add_job(
            check_and_update_ip,
            'interval',
            seconds=interval_seconds,
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
    UPSTREAM_CLIENT = build_shared_async_client()
    await rebuild_mam_proxy_client()
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

    global HARDCOVER_CLIENT
    if HARDCOVER_CLIENT is not None:
        await HARDCOVER_CLIENT.aclose()
        HARDCOVER_CLIENT = None

    global UPSTREAM_CLIENT
    if UPSTREAM_CLIENT is not None:
        await UPSTREAM_CLIENT.aclose()
        UPSTREAM_CLIENT = None
        app.logger.info("Shared httpx AsyncClient closed")

    global MAM_PROXY_CLIENT
    if MAM_PROXY_CLIENT is not None:
        await MAM_PROXY_CLIENT.aclose()
        MAM_PROXY_CLIENT = None
        app.logger.info("Shared MAM proxy AsyncClient closed")

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
    "QBITTORRENT_VERIFY_WEBUI_CERTIFICATE": True,
    "RTORRENT_DIGEST_AUTH": False,
    "MAM_ID": "",
    "MAM_PROXY_ENABLED": False,
    "MAM_PROXY_URL": "",
    "MAM_PROXY_ONLY": True,
    "MAM_PROXY_FALLBACK_DIRECT": True,
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
    "DYNAMIC_IP_CHECK_INTERVAL_SECONDS": 300,
    "DYNAMIC_IP_STALE_RESPONSE_SECONDS": 86400,
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
    "AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_ENABLED": False,
    "AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_MB": 0,
    "ENABLE_FILESYSTEM_THUMBNAIL_CACHE": True,
    "THUMBNAIL_CACHE_MAX_SIZE_MB": 500,
    "MAX_SEARCH_RESULTS": 50,
    "MAX_AUTOCOMPLETE_RESULTS": 20,
    "HARDCOVER_ENRICHMENT_ENABLED": True,
    "HARDCOVER_API_TOKEN": "",
    "HARDCOVER_API_URL": "https://api.hardcover.app/v1/graphql",
    "HARDCOVER_USER_AGENT": "MouseSearch Hardcover Enrichment",
    "HARDCOVER_RATE_LIMIT": 60,
    "HARDCOVER_MATCH_THRESHOLD": 78.0,
    "HARDCOVER_CONCURRENCY": 6,
    "HARDCOVER_SEARCH_PER_PAGE": 5,
    "RESULTS_DISPLAY_FIELDS": ["narrator", "series", "file_size", "file_type", "seeders"],
    "RESULTS_SORT_MODE": DEFAULT_RESULTS_SORT_MODE,
    "SEARCH_FILTER_DEFAULTS": copy.deepcopy(DEFAULT_SEARCH_FILTER_DEFAULTS),
}
ENV_ONLY_CONFIG_KEYS = {"QBITTORRENT_VERIFY_WEBUI_CERTIFICATE"}

# Set up data directory and paths
DATA_PATH = Path(os.getenv("DATA_PATH", FALLBACK_CONFIG["DATA_PATH"])).resolve()
DATA_PATH.mkdir(parents=True, exist_ok=True)
AUTOSUGGEST_CACHE_DB_PATH = DATA_PATH / "autosuggest_cache.sqlite3"

UPLOAD_CREDIT_COST_PER_GB = 500
UPLOAD_CREDIT_MIN_GB = 50
UPLOAD_MAX_AFFORDABLE_LITERAL = "Max Affordable "
VIP_COST_PER_WEEK = 1250
VIP_MAX_WEEKS = 12.85
VIP_MIN_WEEKS = 1
VALID_VIP_DURATIONS = {"4", "8", "12", "max"}

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


def build_shared_async_client(*, proxy: str | None = None, timeout: Timeout | None = None) -> httpx.AsyncClient:
    transport = AsyncHTTPTransport(http2=True, retries=2)
    limits = Limits(max_connections=200, max_keepalive_connections=50, keepalive_expiry=120.0)
    resolved_timeout = timeout or Timeout(connect=5.0, read=15.0, write=15.0, pool=None)
    client_kwargs = {
        "transport": transport,
        "limits": limits,
        "timeout": resolved_timeout,
        "follow_redirects": True,
    }
    if proxy:
        client_kwargs["proxy"] = proxy
    return httpx.AsyncClient(**client_kwargs)


async def rebuild_mam_proxy_client():
    global MAM_PROXY_CLIENT

    if MAM_PROXY_CLIENT is not None:
        await MAM_PROXY_CLIENT.aclose()
        MAM_PROXY_CLIENT = None

    proxy_url = current_mam_proxy_url()
    if not proxy_url:
        reset_mam_proxy_route_state()
        return

    try:
        MAM_PROXY_CLIENT = build_shared_async_client(proxy=proxy_url)
        reset_mam_proxy_route_state()
        if proxy_url.lower().startswith("socks5://"):
            app.logger.warning(
                "[MAM-PROXY] Proxy URL uses socks5://. Prefer socks5h:// for remote DNS resolution."
            )
    except Exception as exc:
        MAM_PROXY_CLIENT = None
        update_mam_proxy_route_state(
            "error",
            "Proxy is configured for MyAnonamouse traffic, but the proxy client could not be initialized.",
            last_error=str(exc),
        )
        app.logger.error("[MAM-PROXY] Failed to initialize proxy client: %s", exc)


async def request_with_optional_proxy(
    method: str,
    url: str,
    *,
    track_proxy_status: bool = False,
    force_proxy: bool = False,
    force_direct: bool = False,
    allow_fallback: bool | None = None,
    **kwargs,
) -> httpx.Response:
    proxy_url = current_mam_proxy_url()
    proxy_only = coerce_bool(app.config.get("MAM_PROXY_ONLY"), FALLBACK_CONFIG["MAM_PROXY_ONLY"])
    fallback_direct = coerce_bool(
        app.config.get("MAM_PROXY_FALLBACK_DIRECT"),
        FALLBACK_CONFIG["MAM_PROXY_FALLBACK_DIRECT"],
    ) if allow_fallback is None else bool(allow_fallback)
    should_use_proxy = (not force_direct) and bool(proxy_url) and (force_proxy or track_proxy_status or not proxy_only)

    if force_proxy and not proxy_url:
        raise RuntimeError("Proxy route requested, but no proxy URL is configured.")

    async def perform_direct_request() -> httpx.Response:
        if UPSTREAM_CLIENT is not None:
            return await UPSTREAM_CLIENT.request(method, url, **kwargs)
        async with build_shared_async_client() as client:
            return await client.request(method, url, **kwargs)

    if not should_use_proxy:
        response = await perform_direct_request()
        if track_proxy_status:
            update_mam_proxy_route_state(
                "direct",
                "No MAM proxy configured. MAM requests are going direct.",
            )
        return response

    proxy_error: Exception | None = None
    proxy_response: httpx.Response | None = None

    if MAM_PROXY_CLIENT is None:
        proxy_error = RuntimeError("Configured proxy client is not available.")
    else:
        try:
            proxy_response = await MAM_PROXY_CLIENT.request(method, url, **kwargs)
            if proxy_response.status_code in MAM_PROXY_FALLBACK_STATUS_CODES:
                status_code = int(proxy_response.status_code)
                await proxy_response.aclose()
                proxy_response = None
                proxy_error = RuntimeError(f"Proxy request returned HTTP {status_code}.")
            else:
                if track_proxy_status:
                    update_mam_proxy_route_state(
                        "proxy",
                        "MAM requests are using the configured proxy.",
                    )
                return proxy_response
        except httpx.RequestError as exc:
            proxy_error = exc
        except Exception as exc:
            proxy_error = exc

    error_text = str(proxy_error or "Unknown proxy error")
    if fallback_direct:
        try:
            response = await perform_direct_request()
            if track_proxy_status:
                update_mam_proxy_route_state(
                    "direct_fallback",
                    "Proxy is configured for MyAnonamouse traffic, but it is not reachable. "
                    "Requests are currently falling back to direct connection.",
                    last_error=error_text,
                )
            app.logger.warning("[MAM-PROXY] Proxy request failed; falling back direct: %s", error_text)
            return response
        except Exception as direct_exc:
            combined_error = f"{error_text} Direct fallback also failed: {direct_exc}"
            if track_proxy_status:
                update_mam_proxy_route_state(
                    "error",
                    "Proxy is configured for MyAnonamouse traffic, but it is not reachable and direct fallback also failed.",
                    last_error=combined_error,
                )
            raise direct_exc

    if track_proxy_status:
        update_mam_proxy_route_state(
            "error",
            "Proxy is configured for MyAnonamouse traffic, but it is not reachable. Direct fallback is disabled.",
            last_error=error_text,
        )
    raise proxy_error or RuntimeError("Proxy request failed.")


async def request_mam(method: str, url: str, **kwargs) -> httpx.Response:
    return await request_with_optional_proxy(method, url, track_proxy_status=True, **kwargs)


async def send_mam_stream(
    method: str,
    url: str,
    *,
    track_proxy_status: bool = True,
    follow_redirects: bool = False,
    **kwargs,
) -> httpx.Response:
    proxy_url = current_mam_proxy_url()
    fallback_direct = coerce_bool(
        app.config.get("MAM_PROXY_FALLBACK_DIRECT"),
        FALLBACK_CONFIG["MAM_PROXY_FALLBACK_DIRECT"],
    )
    should_use_proxy = bool(proxy_url)

    async def perform_direct_send() -> httpx.Response:
        if UPSTREAM_CLIENT is None:
            raise RuntimeError("Shared upstream client is not initialized.")
        req = UPSTREAM_CLIENT.build_request(method, url, **kwargs)
        return await UPSTREAM_CLIENT.send(req, stream=True, follow_redirects=follow_redirects)

    if not should_use_proxy:
        response = await perform_direct_send()
        if track_proxy_status:
            update_mam_proxy_route_state(
                "direct",
                "No MAM proxy configured. MAM requests are going direct.",
            )
        return response

    proxy_error: Exception | None = None
    proxy_response: httpx.Response | None = None

    if MAM_PROXY_CLIENT is None:
        proxy_error = RuntimeError("Configured proxy client is not available.")
    else:
        try:
            req = MAM_PROXY_CLIENT.build_request(method, url, **kwargs)
            proxy_response = await MAM_PROXY_CLIENT.send(req, stream=True, follow_redirects=follow_redirects)
            if proxy_response.status_code in MAM_PROXY_FALLBACK_STATUS_CODES:
                status_code = int(proxy_response.status_code)
                await proxy_response.aclose()
                proxy_response = None
                proxy_error = RuntimeError(f"Proxy request returned HTTP {status_code}.")
            else:
                if track_proxy_status:
                    update_mam_proxy_route_state(
                        "proxy",
                        "MAM requests are using the configured proxy.",
                    )
                return proxy_response
        except httpx.RequestError as exc:
            proxy_error = exc
        except Exception as exc:
            proxy_error = exc

    error_text = str(proxy_error or "Unknown proxy error")
    if fallback_direct:
        response = await perform_direct_send()
        if track_proxy_status:
            update_mam_proxy_route_state(
                "direct_fallback",
                "Proxy is configured for MyAnonamouse traffic, but it is not reachable. "
                "Requests are currently falling back to direct connection.",
                last_error=error_text,
            )
        app.logger.warning("[MAM-PROXY] Proxy streaming request failed; falling back direct: %s", error_text)
        return response

    if track_proxy_status:
        update_mam_proxy_route_state(
            "error",
            "Proxy is configured for MyAnonamouse traffic, but it is not reachable. Direct fallback is disabled.",
            last_error=error_text,
        )
    raise proxy_error or RuntimeError("Proxy request failed.")


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


def normalize_mam_cookie_value(value) -> str:
    raw = str(value or "").strip()
    for part in raw.split(";"):
        name, sep, val = part.strip().partition("=")
        if sep and name.strip() == "mam_id":
            return val.strip()
    return raw


async def sync_mam_session_cookie_from_response(response: httpx.Response | None) -> bool:
    global mam_session_cookies

    if response is None:
        return False

    response_cookies = dict(response.cookies)
    if not response_cookies:
        return False

    async with mam_session_cookie_lock:
        current_cookie = normalize_mam_cookie_value(mam_session_cookies.get("mam_id"))
        mam_session_cookies.update(response_cookies)

        rotated_cookie = normalize_mam_cookie_value(response_cookies.get("mam_id"))
        if not rotated_cookie:
            return False

        changed = rotated_cookie != current_cookie
        mam_session_cookies["mam_id"] = rotated_cookie

        if not changed:
            return changed

        display_cookie = format_cookie_for_display(rotated_cookie)
        if app.config.get("MAM_ID") != rotated_cookie:
            app.config["MAM_ID"] = rotated_cookie
            save_config(app.config)
            app.logger.info("[MAM-COOKIE] Persisted rotated session cookie from API response: %s", display_cookie)
        else:
            app.logger.info("[MAM-COOKIE] Refreshed in-memory session cookie from API response: %s", display_cookie)
        return True


def normalize_proxy_url(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"http://{raw}"
    return raw


def sanitize_proxy_url(url: str | None) -> str:
    raw_url = str(url or "").strip()
    if not raw_url:
        return ""

    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return "<invalid-proxy-url>"

    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    if parsed.username or parsed.password:
        netloc = f"<redacted>@{hostname}{port}" if hostname else "<redacted>"
    else:
        netloc = parsed.netloc or hostname

    return parsed._replace(netloc=netloc, query="", fragment="").geturl() or raw_url


def current_mam_proxy_url() -> str:
    if not coerce_bool(app.config.get("MAM_PROXY_ENABLED"), FALLBACK_CONFIG["MAM_PROXY_ENABLED"]):
        return ""
    return normalize_proxy_url(app.config.get("MAM_PROXY_URL"))


def mam_proxy_is_configured() -> bool:
    return bool(current_mam_proxy_url())


def mam_proxy_is_enabled() -> bool:
    return coerce_bool(app.config.get("MAM_PROXY_ENABLED"), FALLBACK_CONFIG["MAM_PROXY_ENABLED"])


def reset_mam_proxy_route_state():
    if not mam_proxy_is_enabled():
        mam_proxy_route_state.update({
            "route": "direct",
            "message": "MAM proxy is disabled. MAM requests are going direct.",
            "last_error": "",
        })
    elif mam_proxy_is_configured():
        mam_proxy_route_state.update({
            "route": "configured",
            "message": "Proxy configured. Route status will update after the next MAM request.",
            "last_error": "",
        })
    else:
        mam_proxy_route_state.update({
            "route": "direct",
            "message": "No MAM proxy configured. MAM requests are going direct.",
            "last_error": "",
        })


def update_mam_proxy_route_state(route: str, message: str, *, last_error: str = ""):
    mam_proxy_route_state.update({
        "route": route,
        "message": str(message or "").strip(),
        "last_error": str(last_error or "").strip(),
    })


def build_mam_proxy_status_payload() -> dict[str, Any]:
    enabled = mam_proxy_is_enabled()
    raw_proxy_url = normalize_proxy_url(app.config.get("MAM_PROXY_URL"))
    proxy_url = current_mam_proxy_url()
    proxy_only = coerce_bool(app.config.get("MAM_PROXY_ONLY"), FALLBACK_CONFIG["MAM_PROXY_ONLY"])
    fallback_direct = coerce_bool(
        app.config.get("MAM_PROXY_FALLBACK_DIRECT"),
        FALLBACK_CONFIG["MAM_PROXY_FALLBACK_DIRECT"],
    )
    route = str(mam_proxy_route_state.get("route") or "direct")
    last_error = str(mam_proxy_route_state.get("last_error") or "").strip()
    message = str(mam_proxy_route_state.get("message") or "").strip()

    if not enabled:
        route = "direct"
        level = "secondary"
        message = "MAM proxy is disabled. MAM requests are going direct."
    elif not proxy_url:
        route = "direct"
        level = "info"
        message = "MAM proxy is enabled, but no proxy URL is configured. MAM requests are going direct."
    elif route == "proxy":
        level = "success"
        message = message or "MAM requests are using the configured proxy."
    elif route == "direct_fallback":
        level = "warning"
        message = message or (
            "Proxy is configured for MyAnonamouse traffic, but it is not reachable. "
            "Requests are currently falling back to direct connection."
        )
    elif route == "error":
        level = "danger"
        message = message or (
            "Proxy is configured for MyAnonamouse traffic, but it is not reachable. "
            "Direct fallback is disabled."
        )
    elif route == "configured":
        level = "secondary"
        message = message or "Proxy configured. Route status will update after the next MAM request."
    else:
        level = "secondary"
        message = message or "MAM route status is unknown."

    return {
        "enabled": enabled,
        "configured": bool(proxy_url),
        "proxy_url_display": sanitize_proxy_url(raw_proxy_url) or "Not configured",
        "proxy_only": proxy_only,
        "fallback_direct": fallback_direct,
        "route": route,
        "status_level": level,
        "message": message,
        "last_error": last_error,
    }


def load_config():
    # 1. Start with Hardcoded Defaults (Lowest Priority)
    config = copy.deepcopy(FALLBACK_CONFIG)
    
    # 2. Update with Environment Variables (Medium Priority)
    # These act as fallbacks if the key is missing in config.json
    env_config = {key: os.getenv(key) for key in FALLBACK_CONFIG.keys() if os.getenv(key) is not None}
    if (
        "AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_MB" not in env_config
        and os.getenv("AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_GB") is not None
    ):
        env_config["AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_MB"] = coerce_legacy_gb_to_mb(
            os.getenv("AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_GB")
        )
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
    for key in ENV_ONLY_CONFIG_KEYS:
        json_overrides.pop(key, None)
    if (
        "AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_MB" not in json_overrides
        and "AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_GB" in json_overrides
    ):
        json_overrides["AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_MB"] = coerce_legacy_gb_to_mb(
            json_overrides.get("AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_GB")
        )
    apply_legacy_config_aliases(json_overrides, json_config)
    config.update(json_overrides)

    # --- TYPE CASTING BLOCK (Safety) ---
    # Now that we have the final values, we force them into the correct types
    
    # Integers
    for key in [
        "AUTO_ORGANIZE_INTERVAL_HOURS", 
        "DYNAMIC_IP_CHECK_INTERVAL_SECONDS",
        "DYNAMIC_IP_STALE_RESPONSE_SECONDS",
        "DYNAMIC_IP_UPDATE_INTERVAL_HOURS",
        "AUTO_BUY_VIP_INTERVAL_HOURS",
        "AUTO_BUY_UPLOAD_CHECK_INTERVAL_HOURS",
        "THUMBNAIL_CACHE_MAX_SIZE_MB",
        "MAX_SEARCH_RESULTS",
        "MAX_AUTOCOMPLETE_RESULTS",
        "HARDCOVER_RATE_LIMIT",
        "HARDCOVER_CONCURRENCY",
        "HARDCOVER_SEARCH_PER_PAGE",
    ]:
        try:
            config[key] = int(config[key])
        except (ValueError, TypeError):
            config[key] = FALLBACK_CONFIG[key]

    has_dynamic_check_interval = (
        "DYNAMIC_IP_CHECK_INTERVAL_SECONDS" in env_config
        or "DYNAMIC_IP_CHECK_INTERVAL_SECONDS" in json_overrides
    )
    has_legacy_dynamic_interval = (
        "DYNAMIC_IP_UPDATE_INTERVAL_HOURS" in env_config
        or "DYNAMIC_IP_UPDATE_INTERVAL_HOURS" in json_overrides
    )
    if not has_dynamic_check_interval and has_legacy_dynamic_interval:
        config["DYNAMIC_IP_CHECK_INTERVAL_SECONDS"] = max(
            1,
            int(config["DYNAMIC_IP_UPDATE_INTERVAL_HOURS"]) * 3600,
        )
    
    if config["MAX_SEARCH_RESULTS"] <= 0:
        config["MAX_SEARCH_RESULTS"] = FALLBACK_CONFIG["MAX_SEARCH_RESULTS"]
    if config["MAX_AUTOCOMPLETE_RESULTS"] <= 0:
        config["MAX_AUTOCOMPLETE_RESULTS"] = FALLBACK_CONFIG["MAX_AUTOCOMPLETE_RESULTS"]
    if config["DYNAMIC_IP_CHECK_INTERVAL_SECONDS"] <= 0:
        config["DYNAMIC_IP_CHECK_INTERVAL_SECONDS"] = FALLBACK_CONFIG["DYNAMIC_IP_CHECK_INTERVAL_SECONDS"]
    if config["DYNAMIC_IP_STALE_RESPONSE_SECONDS"] <= 0:
        config["DYNAMIC_IP_STALE_RESPONSE_SECONDS"] = FALLBACK_CONFIG["DYNAMIC_IP_STALE_RESPONSE_SECONDS"]
    if config["HARDCOVER_RATE_LIMIT"] <= 0:
        config["HARDCOVER_RATE_LIMIT"] = FALLBACK_CONFIG["HARDCOVER_RATE_LIMIT"]
    if config["HARDCOVER_CONCURRENCY"] <= 0:
        config["HARDCOVER_CONCURRENCY"] = FALLBACK_CONFIG["HARDCOVER_CONCURRENCY"]
    if config["HARDCOVER_SEARCH_PER_PAGE"] <= 0:
        config["HARDCOVER_SEARCH_PER_PAGE"] = FALLBACK_CONFIG["HARDCOVER_SEARCH_PER_PAGE"]

    # Floats
    for key in [
        "AUTO_BUY_UPLOAD_RATIO_THRESHOLD",
        "AUTO_BUY_UPLOAD_RATIO_AMOUNT",
        "AUTO_BUY_UPLOAD_BUFFER_THRESHOLD",
        "AUTO_BUY_UPLOAD_BUFFER_AMOUNT",
        "AUTO_BUY_UPLOAD_BONUS_THRESHOLD",
        "AUTO_BUY_UPLOAD_BONUS_AMOUNT",
        "AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_MB",
        "HARDCOVER_MATCH_THRESHOLD"
    ]:
        try:
            config[key] = float(config[key])
        except (ValueError, TypeError):
            config[key] = FALLBACK_CONFIG[key]

    if (
        not math.isfinite(config["AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_MB"])
        or config["AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_MB"] < 0
    ):
        config["AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_MB"] = FALLBACK_CONFIG["AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_MB"]

    for key in [
        "AUTO_BUY_UPLOAD_RATIO_AMOUNT",
        "AUTO_BUY_UPLOAD_BUFFER_AMOUNT",
        "AUTO_BUY_UPLOAD_BONUS_AMOUNT",
    ]:
        normalized_amount = normalize_upload_credit_amount(config.get(key))
        if normalized_amount is None:
            normalized_amount = FALLBACK_CONFIG[key]
        config[key] = normalized_amount

    # Booleans
    for key in [
        "AUTO_ORGANIZE_ON_ADD",
        "AUTO_ORGANIZE_ON_SCHEDULE",
        "AUTO_ORGANIZE_USE_COPY",
        "HAPTICS_ENABLED",
        "ENABLE_DYNAMIC_IP_UPDATE",
        "MAM_PROXY_ENABLED",
        "MAM_PROXY_ONLY",
        "MAM_PROXY_FALLBACK_DIRECT",
        "AUTO_BUY_VIP",
        "AUTO_BUY_UPLOAD_ON_RATIO",
        "AUTO_BUY_UPLOAD_ON_BUFFER",
        "AUTO_BUY_UPLOAD_ON_BONUS",
        "BLOCK_DOWNLOAD_ON_LOW_BUFFER",
        "AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD",
        "AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_ENABLED",
        "ENABLE_FILESYSTEM_THUMBNAIL_CACHE",
        "RTORRENT_DIGEST_AUTH",
        "HARDCOVER_ENRICHMENT_ENABLED",
        "QBITTORRENT_VERIFY_WEBUI_CERTIFICATE",
    ]:
        config[key] = coerce_bool(config.get(key), FALLBACK_CONFIG[key])
        val = config[key]
        if not isinstance(val, bool):
            # Check against common string representations of True
            config[key] = str(val).lower() in ('true', '1', 't', 'yes', 'on')

    config["MAM_PROXY_URL"] = normalize_proxy_url(config.get("MAM_PROXY_URL"))

    config["RESULTS_DISPLAY_FIELDS"] = normalize_result_display_fields(
        config.get("RESULTS_DISPLAY_FIELDS"),
        FALLBACK_CONFIG["RESULTS_DISPLAY_FIELDS"]
    )
    config["RESULTS_SORT_MODE"] = normalize_results_sort_mode(config.get("RESULTS_SORT_MODE"))
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
    config_to_save = {
        key: config.get(key)
        for key in FALLBACK_CONFIG.keys()
        if key not in ENV_ONLY_CONFIG_KEYS
    }
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

def parse_int_like(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            return None
        return int(value)

    raw = str(value or "").strip()
    if not raw or not re.fullmatch(r"[+-]?\d+", raw):
        return None

    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def normalize_upload_credit_amount(value) -> int | None:
    parsed = parse_int_like(value)
    if parsed is None or parsed < UPLOAD_CREDIT_MIN_GB:
        return None
    return parsed


def coerce_bonus_store_amount(value, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_bonus_store_error(result) -> str:
    if isinstance(result, dict):
        return normalize_spaces(
            result.get("error")
            or result.get("message")
            or json.dumps(result, ensure_ascii=True)
        )
    return normalize_spaces(result)


async def request_bonus_store(params: dict[str, Any]) -> dict:
    epoch_ms = int(time.time() * 1000)
    api_url = f"{app.config.get('MAM_API_URL')}/json/bonusBuy.php/"
    request_params = dict(params)
    request_params["_"] = epoch_ms
    response = await request_mam(
        "GET",
        api_url,
        params=request_params,
        cookies=mam_session_cookies,
        timeout=10,
    )
    response.raise_for_status()
    return response.json()

initialize_config()

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


class SafeFormatDict(dict):
    def __missing__(self, key):
        return ""


def _parse_auto_task_webhook_params(raw_value):
    raw = str(raw_value or "").strip()
    if not raw:
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = dict(parse_qsl(raw, keep_blank_values=True))

    if isinstance(parsed, dict):
        return parsed

    app.logger.warning("[AUTO-WEBHOOK] AUTO_TASK_WEBHOOK_PARAMS must be a JSON object or query string; ignoring value")
    return None


def _parse_auto_task_webhook_body(raw_value):
    raw = str(raw_value or "").strip()
    if not raw:
        return None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw_value


def _parse_auto_task_webhook_events(raw_value):
    raw = str(raw_value or "").strip()
    if not raw:
        return None

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = [item.strip() for item in raw.split(",")]

    if isinstance(parsed, str):
        parsed = [parsed]

    if isinstance(parsed, list):
        events = {str(item).strip() for item in parsed if str(item).strip()}
        return events or None

    app.logger.warning("[AUTO-WEBHOOK] AUTO_TASK_WEBHOOK_EVENTS must be a JSON array or comma-separated list; ignoring value")
    return None


def _render_auto_task_webhook_template(value, context):
    if isinstance(value, str):
        return value.format_map(SafeFormatDict(context))
    if isinstance(value, list):
        return [_render_auto_task_webhook_template(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _render_auto_task_webhook_template(val, context) for key, val in value.items()}
    return value


def _filter_none_values(payload):
    if isinstance(payload, dict):
        return {key: value for key, value in payload.items() if value is not None}
    return payload


def _normalize_webhook_query_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _build_auto_task_summary(context):
    label_map = {
        "task": "Task",
        "reason": "Reason",
        "amount": "Amount",
        "purchase_size": "Purchase Size",
        "purchase_count": "Purchase Count",
        "threshold": "Threshold",
        "current_ratio": "Current Ratio",
        "current_buffer_gb": "Current Buffer GB",
        "starting_seedbonus": "Starting Seedbonus",
        "seedbonus": "Seedbonus",
        "previous_ip": "Previous IP",
        "detected_ip": "Detected IP",
        "updated_ip": "Updated IP",
        "title": "Title",
        "author": "Author",
        "hash": "Hash",
        "mid": "MID",
        "pending_count": "Pending",
        "organized_count": "Organized",
        "failed_count": "Failed",
        "message": "Message",
        "error": "Error",
    }
    ordered_keys = [
        "task",
        "reason",
        "amount",
        "purchase_size",
        "purchase_count",
        "threshold",
        "current_ratio",
        "current_buffer_gb",
        "starting_seedbonus",
        "seedbonus",
        "previous_ip",
        "detected_ip",
        "updated_ip",
        "title",
        "author",
        "hash",
        "mid",
        "pending_count",
        "organized_count",
        "failed_count",
        "message",
        "error",
    ]

    parts = []
    for key in ordered_keys:
        value = context.get(key)
        if value in (None, ""):
            continue
        parts.append(f"{label_map[key]}={value}")
    return ". ".join(parts)


def _get_torrent_metadata_summary(hash_val):
    torrent_meta = load_database().get(hash_val, {})
    return {
        "hash": hash_val,
        "title": torrent_meta.get("title"),
        "author": torrent_meta.get("author"),
        "mid": torrent_meta.get("mid"),
    }


async def send_auto_task_webhook_notification(event, success, **details):
    webhook_url = str(os.getenv("AUTO_TASK_WEBHOOK_URL") or "").strip()
    if not webhook_url:
        return

    enabled_events = _parse_auto_task_webhook_events(os.getenv("AUTO_TASK_WEBHOOK_EVENTS"))
    if enabled_events is not None and event not in enabled_events:
        return

    method = str(os.getenv("AUTO_TASK_WEBHOOK_METHOD", "POST") or "POST").strip().upper()
    if method not in {"GET", "POST"}:
        app.logger.warning(f"[AUTO-WEBHOOK] Unsupported AUTO_TASK_WEBHOOK_METHOD '{method}', falling back to POST")
        method = "POST"

    query_template = _parse_auto_task_webhook_params(os.getenv("AUTO_TASK_WEBHOOK_PARAMS"))
    body_template = _parse_auto_task_webhook_body(os.getenv("AUTO_TASK_WEBHOOK_BODY"))
    status = "success" if success else "failure"
    context = _filter_none_values({
        "event": event,
        "task": details.get("task"),
        "status": status,
        "success": success,
        "timestamp": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        **details,
    })
    context["summary"] = _build_auto_task_summary(context)

    request_kwargs = {
        "params": None,
        "json": None,
        "content": None,
    }

    if query_template is None:
        if method == "GET":
            request_kwargs["params"] = {
                key: _normalize_webhook_query_value(value)
                for key, value in context.items()
            }
    else:
        rendered_params = _render_auto_task_webhook_template(query_template, context)
        request_kwargs["params"] = {
            str(key): _normalize_webhook_query_value(value)
            for key, value in rendered_params.items()
            if value is not None
        }

    if method == "POST":
        if body_template is None:
            request_kwargs["json"] = context
        else:
            rendered_body = _render_auto_task_webhook_template(body_template, context)
            if isinstance(rendered_body, (dict, list, int, float, bool)) or rendered_body is None:
                request_kwargs["json"] = rendered_body
            else:
                request_kwargs["content"] = str(rendered_body)

    try:
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method,
                webhook_url,
                params=request_kwargs["params"],
                json=request_kwargs["json"],
                content=request_kwargs["content"],
                timeout=10,
            )
            response.raise_for_status()
        app.logger.info(f"[AUTO-WEBHOOK] Sent {event} {status} notification via {method}")
    except Exception as e:
        app.logger.warning(f"[AUTO-WEBHOOK] Failed to send {event} {status} notification: {e}")
    
def hardcover_client_config_signature(config) -> tuple:
    try:
        rate_limit = int(config.get("HARDCOVER_RATE_LIMIT", FALLBACK_CONFIG["HARDCOVER_RATE_LIMIT"]))
    except (TypeError, ValueError):
        rate_limit = FALLBACK_CONFIG["HARDCOVER_RATE_LIMIT"]
    return (
        bool(config.get("HARDCOVER_ENRICHMENT_ENABLED", True)),
        str(config.get("HARDCOVER_API_TOKEN") or "").strip(),
        str(config.get("HARDCOVER_API_URL") or FALLBACK_CONFIG["HARDCOVER_API_URL"]).strip(),
        str(config.get("HARDCOVER_USER_AGENT") or FALLBACK_CONFIG["HARDCOVER_USER_AGENT"]).strip(),
        rate_limit,
    )


async def load_new_app_config():
    global HARDCOVER_CLIENT

    old_hardcover_signature = hardcover_client_config_signature(app.config)
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
    reset_mam_proxy_route_state()

    if (
        HARDCOVER_CLIENT is not None
        and old_hardcover_signature != hardcover_client_config_signature(app.config)
    ):
        await HARDCOVER_CLIENT.aclose()
        HARDCOVER_CLIENT = None
    
    # Update path globals
    global ORGANIZED_PATH, LOCAL_TORRENT_DOWNLOAD_PATH, REMOTE_TORRENT_DOWNLOAD_PATH
    ORGANIZED_PATH = Path(new_config.get("ORGANIZED_PATH", FALLBACK_CONFIG["ORGANIZED_PATH"])).resolve()
    LOCAL_TORRENT_DOWNLOAD_PATH = Path(
        new_config.get("LOCAL_TORRENT_DOWNLOAD_PATH", FALLBACK_CONFIG["LOCAL_TORRENT_DOWNLOAD_PATH"])
    ).resolve()
    REMOTE_TORRENT_DOWNLOAD_PATH = new_config.get("REMOTE_TORRENT_DOWNLOAD_PATH") or None
    
    global mam_session_cookies
    mam_session_cookies = {"mam_id": normalize_mam_cookie_value(app.config.get("MAM_ID"))}

    # --- CRITICAL FIX HERE ---
    global torrent_client 
    try:
        torrent_client = get_torrent_client(app.config)
        app.logger.info(f"Initialized torrent client: {app.config.get('TORRENT_CLIENT_TYPE', 'qbittorrent')}")
    except Exception as e:
        app.logger.error(f"Failed to initialize torrent client: {e}")
        torrent_client = None

    await rebuild_mam_proxy_client()

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
                        org_details = _get_torrent_metadata_summary(h)
                        success, msg = await _perform_organization(h, require_stable_source=True)
                        if success:
                            app.logger.info(f"[MONITOR] Auto-organize succeeded for {h}: {msg}")
                        else:
                            app.logger.warning(f"[MONITOR] Auto-organize failed for {h}: {msg}")
                        await send_auto_task_webhook_notification(
                            "auto_organize_on_download",
                            success,
                            task="organize_on_download",
                            message=msg,
                            **org_details,
                        )
                    except Exception as e:
                        app.logger.error(f"[MONITOR] Exception during auto-organize for {h}: {e}", exc_info=True)
                        await send_auto_task_webhook_notification(
                            "auto_organize_on_download",
                            False,
                            task="organize_on_download",
                            error=str(e),
                            **_get_torrent_metadata_summary(h),
                        )
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

def load_dynamic_ip_state() -> dict:
    if os.path.exists(IP_STATE_FILE):
        try:
            with open(IP_STATE_FILE, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, FileNotFoundError):
            pass
    return {}


def save_dynamic_ip_state(state: dict):
    with open(IP_STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)


def invalidate_dynamic_ip_state(reason: str):
    try:
        if IP_STATE_FILE.exists():
            IP_STATE_FILE.unlink()
        app.logger.info("Invalidated dynamic IP state: %s", reason)
    except FileNotFoundError:
        app.logger.info("Dynamic IP state already absent while invalidating: %s", reason)
    except Exception as exc:
        app.logger.warning("Failed to invalidate dynamic IP state (%s): %s", reason, exc)


def parse_dynamic_ip_state_datetime(value) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_dynamic_ip_host_info(host_info: dict | None) -> dict:
    payload = host_info if isinstance(host_info, dict) else {}
    asn_value = payload.get("asn")
    try:
        asn = int(asn_value) if asn_value not in (None, "") else None
    except (TypeError, ValueError):
        asn = None
    return {
        "ip": str(payload.get("ip") or "").strip(),
        "asn": asn,
        "as": str(payload.get("as") or "").strip(),
    }


def build_dynamic_ip_state(
    host_info: dict | None,
    last_mam: dict | None,
    *,
    mam_updated: bool,
    update_reason: str | None,
) -> dict:
    return {
        "version": 2,
        "host": normalize_dynamic_ip_host_info(host_info),
        "last_mam": last_mam,
        "last_update": {
            "at": datetime.now(timezone.utc).isoformat(),
            "mam_updated": bool(mam_updated),
            "mam_update_reason": update_reason,
        },
    }


def get_dynamic_ip_state_last_ip(state: dict) -> str | None:
    if not isinstance(state, dict):
        return None

    last_mam = state.get("last_mam")
    if isinstance(last_mam, dict):
        response = last_mam.get("response")
        if isinstance(response, dict):
            body = response.get("body")
            if isinstance(body, dict):
                value = str(body.get("ip") or "").strip()
                if value:
                    return value

    legacy_value = str(state.get("last_ip") or "").strip()
    return legacy_value or None


def get_dynamic_ip_update_reason(state: dict, host_info: dict) -> str | None:
    last_mam = state.get("last_mam") if isinstance(state, dict) else None
    if not isinstance(last_mam, dict):
        return "no-last-response"

    response = last_mam.get("response")
    if not isinstance(response, dict):
        return "no-last-response"

    status_code = response.get("http_status")
    if status_code != 200:
        return "last-response-error"

    body = response.get("body")
    if not isinstance(body, dict):
        return "last-response-error"

    if host_info.get("ip") != str(body.get("ip") or "").strip():
        return "ip-changed"

    try:
        last_asn = int(body.get("ASN")) if body.get("ASN") not in (None, "") else None
    except (TypeError, ValueError):
        last_asn = None
    if host_info.get("asn") != last_asn:
        return "asn-changed"

    request_info = last_mam.get("request")
    request_at = parse_dynamic_ip_state_datetime(request_info.get("at") if isinstance(request_info, dict) else None)
    if request_at is None:
        return "response-stale"

    stale_seconds = int(
        app.config.get(
            "DYNAMIC_IP_STALE_RESPONSE_SECONDS",
            FALLBACK_CONFIG["DYNAMIC_IP_STALE_RESPONSE_SECONDS"],
        )
    )
    if request_at + timedelta(seconds=stale_seconds) <= datetime.now(timezone.utc):
        return "response-stale"

    return None


async def fetch_current_dynamic_ip_host_info() -> dict:
    ip_check_url = f"{app.config.get('MAM_API_URL')}/json/jsonIp.php"
    response = await request_with_optional_proxy(
        "GET",
        ip_check_url,
        track_proxy_status=True,
        headers={"Accept": "application/json"},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"jsonIp response was not a JSON object: {type(payload).__name__}")

    current_ip = str(payload.get("ip") or "").strip()
    current_as = str(payload.get("AS") or "").strip()
    try:
        current_asn = int(payload.get("ASN")) if payload.get("ASN") not in (None, "") else None
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid ASN in jsonIp response: {payload.get('ASN')}") from exc

    if not current_ip or current_asn is None:
        raise ValueError("jsonIp response did not include both ip and ASN")

    return {
        "ip": current_ip,
        "asn": current_asn,
        "as": current_as,
    }


async def force_update_ip(
    notify_event=False,
    previous_ip=None,
    detected_ip=None,
    update_reason: str = "forced",
    host_info: dict | None = None,
):
    async with app.app_context():
        app.logger.info("Updating dynamic seedbox because: %s", update_reason)
        state = load_dynamic_ip_state()
        if previous_ip is None:
            previous_ip = get_dynamic_ip_state_last_ip(state)

        normalized_host_info = normalize_dynamic_ip_host_info(host_info)
        if not normalized_host_info.get("ip"):
            try:
                normalized_host_info = await fetch_current_dynamic_ip_host_info()
            except Exception as exc:
                app.logger.warning("Could not fetch current host info before dynamic seedbox update: %s", exc)
                normalized_host_info = normalize_dynamic_ip_host_info({
                    "ip": detected_ip or previous_ip or "",
                    "asn": None,
                    "as": "",
                })

        if not await ensure_mam_session_cookie():
            if notify_event:
                await send_auto_task_webhook_notification(
                    "auto_update_ip",
                    False,
                    task="dynamic_ip_update",
                    error="MAM cookie is not configured",
                    previous_ip=previous_ip,
                    detected_ip=detected_ip,
                )
            return
        api_cookies = dict(mam_session_cookies)
        try:
            update_url = "https://t.myanonamouse.net/json/dynamicSeedbox.php"
            update_response = await request_mam("GET", update_url, cookies=api_cookies, timeout=15)
            await sync_mam_session_cookie_from_response(update_response)
            try:
                update_data = update_response.json()
            except ValueError as exc:
                update_data = {
                    "Success": False,
                    "msg": f"Invalid JSON response: {exc}",
                }
            if not isinstance(update_data, dict):
                update_data = {
                    "Success": False,
                    "msg": f"Unexpected JSON payload: {type(update_data).__name__}",
                }

            mam_record = {
                "request": {
                    "at": datetime.now(timezone.utc).isoformat(),
                },
                "response": {
                    "http_status": update_response.status_code,
                    "body": update_data,
                },
            }
            save_dynamic_ip_state(
                build_dynamic_ip_state(
                    normalized_host_info,
                    mam_record,
                    mam_updated=True,
                    update_reason=update_reason,
                )
            )

            detected_ip = detected_ip or normalized_host_info.get("ip")
            update_message = normalize_spaces(update_data.get("msg") or f"HTTP {update_response.status_code}")
            new_ip = str(update_data.get("ip") or "").strip()

            if update_response.status_code == 200 and new_ip:
                app.logger.info("Dynamic seedbox update completed.")
                if notify_event:
                    await send_auto_task_webhook_notification(
                        "auto_update_ip",
                        True,
                        task="dynamic_ip_update",
                        previous_ip=previous_ip,
                        detected_ip=detected_ip,
                        updated_ip=new_ip,
                    )
                return

            app.logger.error(
                "Error calling dynamic seedbox update: HTTP %s - %s",
                update_response.status_code,
                update_message,
            )
            if notify_event:
                await send_auto_task_webhook_notification(
                    "auto_update_ip",
                    False,
                    task="dynamic_ip_update",
                    previous_ip=previous_ip,
                    detected_ip=detected_ip,
                    error=update_message,
                )
        except Exception as e:
            app.logger.error(f"Error calling dynamic seedbox update: {e}")
            if notify_event:
                await send_auto_task_webhook_notification(
                    "auto_update_ip",
                    False,
                    task="dynamic_ip_update",
                    previous_ip=previous_ip,
                    detected_ip=detected_ip,
                    error=str(e),
                )

async def check_and_update_ip():
    async with app.app_context():
        try:
            host_info = await fetch_current_dynamic_ip_host_info()
        except Exception as e:
            await send_auto_task_webhook_notification(
                "auto_update_ip",
                False,
                task="dynamic_ip_update",
                error=f"Could not fetch current host info: {e}",
            )
            return

        state = load_dynamic_ip_state()
        update_reason = get_dynamic_ip_update_reason(state, host_info)
        if not update_reason:
            app.logger.info("No dynamic MAM update needed; current state is ok.")
            save_dynamic_ip_state(
                build_dynamic_ip_state(
                    host_info,
                    state.get("last_mam") if isinstance(state, dict) else None,
                    mam_updated=False,
                    update_reason=None,
                )
            )
            return

        await force_update_ip(
            notify_event=True,
            previous_ip=get_dynamic_ip_state_last_ip(state),
            detected_ip=host_info.get("ip"),
            update_reason=update_reason,
            host_info=host_info,
        )


# --- VIP AUTO-BUY SCHEDULER ---
async def auto_buy_vip():
    """Automatically purchase VIP credit to keep it topped up."""
    async with app.app_context():
        if not await ensure_mam_session_cookie():
            app.logger.warning("VIP auto-buy scheduled but MAM cookie not configured")
            await send_auto_task_webhook_notification(
                "auto_buy_vip",
                False,
                task="vip_topup",
                error="MAM cookie is not configured",
            )
            return
        
        if not await login_mam():
            app.logger.warning("VIP auto-buy failed: Could not log into MAM")
            await send_auto_task_webhook_notification(
                "auto_buy_vip",
                False,
                task="vip_topup",
                error="Could not log into MAM",
            )
            return

        user_data = await fetch_mam_json_load()
        if not user_data:
            app.logger.warning("[AUTO-VIP] Could not fetch user data")
            await send_auto_task_webhook_notification(
                "auto_buy_vip",
                False,
                task="vip_topup",
                error="Could not fetch user data",
            )
            return
        max_weeks = calculate_vip_topup_weeks(user_data)
        if max_weeks < VIP_MIN_WEEKS:
            app.logger.info(f"[AUTO-VIP] Skipping top-up: max purchase {max_weeks:.2f} weeks (< {VIP_MIN_WEEKS})")
            return
        
        try:
            result = await request_bonus_store({
                'spendtype': 'VIP',
                'duration': 'max',
            })

            if result.get('success'):
                app.logger.info(f"[AUTO-VIP] Purchase successful - {result.get('amount')} weeks added, Remaining bonus: {result.get('seedbonus')}")
                await broadcast_payload({
                    'event': 'vip_purchase',
                    'success': True,
                    'amount': result.get('amount'),
                    'seedbonus': result.get('seedbonus')
                })
                await send_auto_task_webhook_notification(
                    "auto_buy_vip",
                    True,
                    task="vip_topup",
                    amount=result.get('amount'),
                    seedbonus=result.get('seedbonus'),
                )
            else:
                app.logger.warning(f"[AUTO-VIP] Purchase failed: {result}")
                await send_auto_task_webhook_notification(
                    "auto_buy_vip",
                    False,
                    task="vip_topup",
                    amount=result.get('amount'),
                    seedbonus=result.get('seedbonus'),
                    error=normalize_spaces(
                        result.get('error')
                        or result.get('message')
                        or json.dumps(result, ensure_ascii=True)
                    ),
                )
        except Exception as e:
            app.logger.error(f"[AUTO-VIP] Error during scheduled VIP purchase: {e}")
            await send_auto_task_webhook_notification(
                "auto_buy_vip",
                False,
                task="vip_topup",
                error=str(e),
            )



# --- UPLOAD CREDIT AUTO-BUY SCHEDULER ---
async def check_and_buy_upload():
    """Check ratio, buffer, and bonus thresholds, auto-purchase upload credit if needed."""
    async with app.app_context():
        if not await ensure_mam_session_cookie():
            app.logger.warning("[AUTO-UPLOAD] MAM cookie not configured")
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
            normalized_amount = normalize_upload_credit_amount(amount)
            if normalized_amount is None:
                error = f"Invalid amount: {amount} GB (whole numbers {UPLOAD_CREDIT_MIN_GB} or higher only)"
                app.logger.warning(f"[AUTO-UPLOAD-{reason.upper()}] {error}")
                return {
                    "success": False,
                    "amount": 0,
                    "seedbonus": None,
                    "error": error,
                    "reason": reason,
                }

            total_purchased = 0.0
            final_seedbonus = None

            try:
                result = await request_bonus_store({
                    'spendtype': 'upload',
                    'amount': normalized_amount,
                })
            except Exception as e:
                app.logger.error(f"[AUTO-UPLOAD-{reason.upper()}] Error: {e}")
                return {
                    "success": False,
                    "amount": total_purchased,
                    "seedbonus": final_seedbonus,
                    "error": str(e),
                    "reason": reason,
                }

            if result.get('success'):
                total_purchased = coerce_bonus_store_amount(result.get('amount'), float(normalized_amount)) or 0.0
                final_seedbonus = result.get('seedbonus')
            else:
                error = normalize_bonus_store_error(result)
                app.logger.warning(f"[AUTO-UPLOAD-{reason.upper()}] Purchase failed: {result}")
                return {
                    "success": False,
                    "amount": total_purchased,
                    "seedbonus": final_seedbonus,
                    "error": error,
                    "reason": reason,
                }

            if total_purchased <= 0:
                error = "Purchase failed: no upload credit added"
                app.logger.warning(f"[AUTO-UPLOAD-{reason.upper()}] {error}")
                return {
                    "success": False,
                    "amount": 0,
                    "seedbonus": final_seedbonus,
                    "error": error,
                    "reason": reason,
                }

            app.logger.info(f"[AUTO-UPLOAD-{reason.upper()}] Purchase successful - {total_purchased} GB added")
            await broadcast_payload({
                'event': 'upload_purchase',
                'success': True,
                'amount': total_purchased,
                'reason': reason,
                'seedbonus': final_seedbonus
            })
            return {
                "success": True,
                "amount": total_purchased,
                "seedbonus": final_seedbonus,
                "error": None,
                "reason": reason,
            }
        
        # Check ratio threshold
        if ratio_check_enabled:
            ratio_threshold = float(app.config.get("AUTO_BUY_UPLOAD_RATIO_THRESHOLD", 1.5))
            if stats['ratio'] < ratio_threshold:
                amount = float(app.config.get("AUTO_BUY_UPLOAD_RATIO_AMOUNT", 50))
                app.logger.info(f"[AUTO-UPLOAD] Ratio {stats['ratio']} below threshold {ratio_threshold}, purchasing {amount} GB")
                
                purchase_result = await purchase_upload(amount, "ratio")
                await send_auto_task_webhook_notification(
                    "auto_buy_upload_ratio",
                    purchase_result["success"],
                    task="upload_credit_ratio",
                    reason="ratio",
                    threshold=ratio_threshold,
                    current_ratio=stats.get("ratio"),
                    purchase_size=amount,
                    amount=round(float(purchase_result.get("amount") or 0), 2),
                    seedbonus=purchase_result.get("seedbonus"),
                    error=purchase_result.get("error"),
                )
                if purchase_result["success"]:
                    purchased = True
                    if purchase_result["seedbonus"] is not None:
                        current_seedbonus = purchase_result["seedbonus"]
        
        # Check buffer threshold (only if we didn't already purchase)
        if buffer_check_enabled and not purchased:
            buffer_threshold = float(app.config.get("AUTO_BUY_UPLOAD_BUFFER_THRESHOLD", 10))
            if stats['buffer_gb'] < buffer_threshold:
                amount = float(app.config.get("AUTO_BUY_UPLOAD_BUFFER_AMOUNT", 50))
                app.logger.info(f"[AUTO-UPLOAD] Buffer {stats['buffer_gb']:.2f} GB below threshold {buffer_threshold} GB, purchasing {amount} GB")
                
                purchase_result = await purchase_upload(amount, "buffer")
                await send_auto_task_webhook_notification(
                    "auto_buy_upload_buffer",
                    purchase_result["success"],
                    task="upload_credit_buffer",
                    reason="buffer",
                    threshold=buffer_threshold,
                    current_buffer_gb=stats.get("buffer_gb"),
                    purchase_size=amount,
                    amount=round(float(purchase_result.get("amount") or 0), 2),
                    seedbonus=purchase_result.get("seedbonus"),
                    error=purchase_result.get("error"),
                )
                if purchase_result["success"] and purchase_result["seedbonus"] is not None:
                    current_seedbonus = purchase_result["seedbonus"]

        if bonus_check_enabled:
            bonus_threshold = float(app.config.get("AUTO_BUY_UPLOAD_BONUS_THRESHOLD", 5000))
            amount = float(app.config.get("AUTO_BUY_UPLOAD_BONUS_AMOUNT", 50))
            seedbonus = current_seedbonus
            if seedbonus is None:
                refreshed = await get_user_stats()
                if not refreshed:
                    error = "Could not refresh user stats before bonus check"
                    app.logger.warning(f"[AUTO-UPLOAD-BONUS] {error}")
                    await send_auto_task_webhook_notification(
                        "auto_buy_upload_bonus",
                        False,
                        task="upload_credit_bonus",
                        reason="bonus",
                        threshold=bonus_threshold,
                        purchase_size=amount,
                        error=error,
                    )
                    return
                seedbonus = refreshed.get('seedbonus')

            starting_seedbonus = seedbonus
            purchase_count = 0
            total_purchased = 0.0
            failure_error = None

            while seedbonus is not None and seedbonus >= bonus_threshold:
                app.logger.info(f"[AUTO-UPLOAD] Bonus points {seedbonus} >= threshold {bonus_threshold}, purchasing {amount} GB")
                purchase_result = await purchase_upload(amount, "bonus")
                if not purchase_result["success"]:
                    failure_error = purchase_result.get("error") or "Purchase failed"
                    break
                purchase_count += 1
                total_purchased += float(purchase_result.get("amount") or 0)
                new_seedbonus = purchase_result.get("seedbonus")
                if new_seedbonus is None:
                    refreshed = await get_user_stats()
                    if not refreshed:
                        failure_error = "Could not refresh user stats after purchase"
                        app.logger.warning(f"[AUTO-UPLOAD-BONUS] {failure_error}")
                        break
                    new_seedbonus = refreshed.get('seedbonus')
                if new_seedbonus is None:
                    failure_error = "Could not determine remaining bonus points after purchase"
                    app.logger.warning(f"[AUTO-UPLOAD-BONUS] {failure_error}")
                    break
                if new_seedbonus >= seedbonus:
                    failure_error = "Bonus points did not decrease after purchase; stopping loop"
                    app.logger.warning(f"[AUTO-UPLOAD-BONUS] {failure_error}")
                    break
                seedbonus = new_seedbonus

            if purchase_count > 0 or failure_error:
                await send_auto_task_webhook_notification(
                    "auto_buy_upload_bonus",
                    failure_error is None and purchase_count > 0,
                    task="upload_credit_bonus",
                    reason="bonus",
                    threshold=bonus_threshold,
                    purchase_size=amount,
                    purchase_count=purchase_count,
                    amount=round(total_purchased, 2),
                    starting_seedbonus=starting_seedbonus,
                    seedbonus=seedbonus,
                    error=failure_error,
                )


# --- SESSION AND API HELPERS ---
async def ensure_mam_session_cookie() -> bool:
    if not mam_session_cookies.get("mam_id") and app.config.get("MAM_ID"):
        mam_session_cookies["mam_id"] = normalize_mam_cookie_value(app.config.get("MAM_ID"))
    return bool(mam_session_cookies.get("mam_id"))


def format_cookie_for_display(cookie_value: str | None) -> str:
    value = str(cookie_value or "").strip()
    if not value:
        return "Not synced"
    return value


async def login_mam():
    """Checks if the MAM session is valid by attempting to load user data."""
    result = await fetch_mam_json_load_result()
    return result["data"] is not None

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
    if not await ensure_mam_session_cookie():
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
        "tor[searchType]": "all"
    }
    for field, enabled in {"title": title_on, "author": author_on, "narrator": narrator_on, "series": series_on}.items():
        if enabled:
            params[f"tor[srchIn][{field}]"] = "true"

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
        raw_results = []
        for query_text in query_candidates:
            params["tor[text]"] = query_text
            resp = await request_mam("GET", url, params=params, cookies=mam_session_cookies, timeout=5.0)
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
    result = await fetch_mam_json_load_result()
    if result["data"] is not None:
        return jsonify({
            'status': 'connected',
            'message': 'MyAnonaMouse is connected.',
            'proxy_status': build_mam_proxy_status_payload(),
        })

    status_code = result["status_code"] if result["status_code"] is not None else 401
    return jsonify({
        'status': 'not connected',
        'message': result["message"] or 'Not logged into MAM or failed to fetch data',
        'proxy_status': build_mam_proxy_status_payload(),
    }), status_code


@app.route('/mam/user_data', methods=['GET'])
async def mam_user_data():
    result = await fetch_mam_json_load_result()
    user_data = result["data"]
    current_cookie_display = format_cookie_for_display(mam_session_cookies.get("mam_id"))
    
    if not user_data:
        status_code = result["status_code"] if result["status_code"] is not None else 401
        return jsonify({
            'error': result["message"] or 'Not logged into MAM or failed to fetch data',
            'message': result["message"] or 'Not logged into MAM or failed to fetch data',
            'current_mam_cookie': current_cookie_display,
            'proxy_status': build_mam_proxy_status_payload(),
        }), status_code
        
    if seedbonus := user_data.get("seedbonus"):
        user_data["seedbonus_formatted"] = f"{seedbonus:,}"

    user_data["message"] = "MyAnonaMouse is connected."
    user_data["current_mam_cookie"] = current_cookie_display
    user_data["proxy_status"] = build_mam_proxy_status_payload()
        
    return jsonify(user_data)

@app.route('/mam/buy_vip', methods=['POST'])
async def mam_buy_vip():
    """Buy VIP credit using bonus points. Accepts 'max' or specific weeks."""
    if not await login_mam():
        return jsonify({'success': False, 'error': 'Not logged into MAM'}), 401

    try:
        data = await request.get_json() or {}
        duration = normalize_spaces(data.get('duration', 'max')).lower()
        if duration not in VALID_VIP_DURATIONS:
            return jsonify({
                'success': False,
                'error': 'Invalid duration. Valid options are 4, 8, 12, or max.'
            }), 400

        if duration == 'max':
            user_data = await fetch_mam_json_load()
            max_weeks = calculate_vip_topup_weeks(user_data)
            if max_weeks < VIP_MIN_WEEKS:
                return jsonify({
                    'success': False,
                    'error': f"Minimum VIP purchase is {VIP_MIN_WEEKS} week."
                }), 400
        result = await request_bonus_store({
            'spendtype': 'VIP',
            'duration': duration,
        })

        if result.get('success'):
            app.logger.info(f"VIP purchase successful - Duration: {duration}, Amount added: {result.get('amount')} weeks, Remaining bonus: {result.get('seedbonus')}")
            await push_mam_stats()
        else:
            app.logger.warning(f"VIP purchase failed: {result}")

        if result.get('success'):
            return jsonify(result)
        return jsonify({
            **result,
            'success': False,
            'error': normalize_bonus_store_error(result),
        }), 400
    except Exception as e:
        app.logger.error(f"Error buying VIP credit: {e}")
        return jsonify({'success': False, 'error': 'Failed to purchase VIP'}), 503

@app.route('/mam/buy_upload', methods=['POST'])
async def mam_buy_upload():
    """
    Buy upload credit using the MAM bonus store.
    Accepts 'max' or a specific whole number of GB >= 50.
    """
    if not await login_mam():
        return jsonify({'success': False, 'error': 'Not logged into MAM'}), 401
    
    data = await request.get_json() or {}
    raw_amount = data.get('amount')
    use_max = str(raw_amount).strip().lower() == 'max'
    normalized_amount = None if use_max else normalize_upload_credit_amount(raw_amount)
    if not use_max and normalized_amount is None:
        return jsonify({
            'success': False,
            'error': f'Invalid amount: {raw_amount}. Use a whole number of GB that is {UPLOAD_CREDIT_MIN_GB} or higher, or max.'
        }), 400

    try:
        result = await request_bonus_store({
            'spendtype': 'upload',
            'amount': UPLOAD_MAX_AFFORDABLE_LITERAL if use_max else normalized_amount,
        })
    except Exception as e:
        app.logger.error(f"[BUY-UPLOAD] Exception: {e}")
        return jsonify({'success': False, 'error': 'Failed to purchase upload credit'}), 503

    if result.get('success'):
        app.logger.info(f"[BUY-UPLOAD] Success: {result}")
        await push_mam_stats()
        if result.get('amount') in (None, "") and normalized_amount is not None:
            result['amount'] = normalized_amount
        return jsonify(result)

    error = normalize_bonus_store_error(result)
    app.logger.warning(f"[BUY-UPLOAD] Purchase failed: {result}")
    return jsonify({
        **result,
        'success': False,
        'error': error,
    }), 400


@app.route('/mam/buy_wedge', methods=['POST'])
async def mam_buy_wedge():
    """Buy a personal Freeleech wedge using bonus points."""
    if not await login_mam():
        return jsonify({'success': False, 'error': 'Not logged into MAM'}), 401

    try:
        result = await request_bonus_store({
            'spendtype': 'wedges',
            'source': 'points',
        })
    except Exception as e:
        app.logger.error(f"Error buying wedge: {e}")
        return jsonify({'success': False, 'error': 'Failed to purchase wedge'}), 503

    if result.get('success'):
        app.logger.info(f"Wedge purchase successful - Remaining bonus: {result.get('seedbonus')}")
        await push_mam_stats()
        return jsonify(result)

    app.logger.warning(f"Wedge purchase failed: {result}")
    return jsonify({
        **result,
        'success': False,
        'error': normalize_bonus_store_error(result),
    }), 400
        

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

def sanitize_mam_api_url(url: str | None) -> str:
    raw_url = str(url or "").strip()
    if not raw_url:
        return "<missing>"

    try:
        parsed = urlparse(raw_url)
    except ValueError:
        return "<invalid-url>"

    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    if parsed.username or parsed.password:
        netloc = f"<redacted>@{hostname}{port}" if hostname else "<redacted>"
    else:
        netloc = parsed.netloc or hostname

    return parsed._replace(netloc=netloc, query="", fragment="").geturl() or raw_url


async def fetch_mam_json_load_result():
    """
    Unified helper to fetch data from jsonLoad.php with structured diagnostics.
    Returns a dict with keys: data, message, status_code.
    """
    url = app.config.get("MAM_API_URL")
    sanitized_url = sanitize_mam_api_url(url)
    await ensure_mam_session_cookie()
    mam_id_present = bool(mam_session_cookies.get("mam_id"))

    if not url:
        message = "MAM API URL is not configured."
        app.logger.warning("[MAM-API] %s url=%s mam_id_present=%s", message, sanitized_url, mam_id_present)
        return {"data": None, "message": message, "status_code": 500}

    if not mam_id_present:
        message = "MAM session ID is not configured."
        app.logger.warning("[MAM-API] %s url=%s mam_id_present=%s", message, sanitized_url, mam_id_present)
        return {"data": None, "message": message, "status_code": 401}

    api_url = f"{url}/jsonLoad.php"
    try:
        response = await request_mam("GET", api_url, cookies=mam_session_cookies, timeout=10)
        response.raise_for_status()
        data = response.json()
        return {"data": data, "message": "MyAnonaMouse is connected.", "status_code": response.status_code}
    except httpx.TimeoutException as exc:
        message = f"MAM API request timed out: {exc}" if str(exc).strip() else "MAM API request timed out."
        app.logger.warning(
            "[MAM-API] jsonLoad.php timeout: url=%s status=%s mam_id_present=%s error=%s",
            sanitized_url,
            "n/a",
            mam_id_present,
            str(exc),
        )
        return {"data": None, "message": message, "status_code": 504}
    except httpx.HTTPStatusError as exc:
        status_code = exc.response.status_code if exc.response is not None else 502
        response_preview = ""
        if exc.response is not None:
            response_preview = (exc.response.text or "")[:200]
        message = f"MAM API request failed with HTTP {status_code}."
        app.logger.warning(
            "[MAM-API] jsonLoad.php HTTP error: url=%s status=%s mam_id_present=%s response=%r error=%s",
            sanitized_url,
            status_code,
            mam_id_present,
            response_preview,
            str(exc),
        )
        return {"data": None, "message": message, "status_code": status_code}
    except httpx.RequestError as exc:
        message = f"MAM API request error: {exc}"
        app.logger.warning(
            "[MAM-API] jsonLoad.php request error: url=%s status=%s mam_id_present=%s error=%s",
            sanitized_url,
            "n/a",
            mam_id_present,
            str(exc),
        )
        return {"data": None, "message": message, "status_code": 502}
    except ValueError as exc:
        message = f"MAM API returned invalid JSON: {exc}"
        app.logger.warning(
            "[MAM-API] jsonLoad.php invalid JSON: url=%s status=%s mam_id_present=%s error=%s",
            sanitized_url,
            200,
            mam_id_present,
            str(exc),
        )
        return {"data": None, "message": message, "status_code": 502}
    except Exception as exc:
        message = f"Unexpected MAM API error: {exc}"
        app.logger.warning(
            "[MAM-API] jsonLoad.php unexpected error: url=%s status=%s mam_id_present=%s error=%s",
            sanitized_url,
            "n/a",
            mam_id_present,
            str(exc),
        )
        return {"data": None, "message": message, "status_code": 502}


async def fetch_mam_json_load():
    """
    Backwards-compatible wrapper returning only the JSON dict on success.
    """
    result = await fetch_mam_json_load_result()
    return result["data"]
    
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

        uploaded_gb = parse_size_to_gb(data.get('uploaded', '0 GiB'))
        downloaded_gb = parse_size_to_gb(data.get('downloaded', '0 GiB'))
        
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

    await ensure_mam_session_cookie()

    try:
        response = await request_mam("GET", torrent_url, cookies=mam_session_cookies, timeout=15.0)
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


def append_personal_freeleech_flag(torrent_url: str) -> str:
    """Appends the MAM freeleech flag to a download URL without disturbing other query params."""
    normalized_url = str(torrent_url or "").strip()
    if not normalized_url:
        return normalized_url

    parsed = urlparse(normalized_url)
    if any(key == "fl" for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
        return normalized_url

    next_query = f"{parsed.query}&fl" if parsed.query else "fl"
    return parsed._replace(query=next_query).geturl()


def build_mam_download_link(base_download_url: str, dl_value: Any, torrent_id: Any) -> str:
    """Build a MAM download URL containing exactly one current ``tid`` parameter."""
    normalized_dl = str(dl_value or "").strip()
    normalized_tid = str(torrent_id or "").strip()
    if not normalized_dl or not normalized_tid:
        return ""

    parsed = urlparse(f"{base_download_url}{normalized_dl}")
    query_parts = []
    for part in parsed.query.split("&"):
        if not part:
            continue
        query_key = unquote(part.partition("=")[0]).strip().lower()
        if query_key != "tid":
            query_parts.append(part)

    query_parts.append(f"tid={normalized_tid}")
    return parsed._replace(query="&".join(query_parts)).geturl()

# --- GENERIC TORRENT CLIENT ROUTES ---
@app.route('/client/status', methods=['GET'])
async def client_status():
    if not torrent_client: return jsonify({"status": "error", "message": "Client not initialized"}), 500
    # Only login if needed (handled by client usually, but we force login in other places)
    try:
        return jsonify(await torrent_client.get_status())
    except Exception as exc:
        app.logger.warning(f"[CLIENT-STATUS] Initial status check failed; retrying login: {exc}")
        try:
            await torrent_client.login()
            status = await torrent_client.get_status()
            app.logger.info(
                f"[CLIENT-STATUS] Retry completed with status={status.get('status')} message={status.get('message', '')}"
            )
            return jsonify(status)
        except Exception as retry_exc:
            app.logger.error(f"[CLIENT-STATUS] Retry failed after login attempt: {retry_exc}")
            return jsonify({
                "status": "error",
                "message": f"Client status retry failed: {retry_exc}",
                "display_name": getattr(torrent_client, "display_name", "Torrent Client"),
            }), 502


def build_torrent_client_probe_config(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    probe_config = app.config.copy()
    if not isinstance(payload, dict):
        return probe_config

    for key in (
        "TORRENT_CLIENT_TYPE",
        "TORRENT_CLIENT_URL",
        "TORRENT_CLIENT_USERNAME",
        "TORRENT_CLIENT_PASSWORD",
    ):
        if key not in payload:
            continue
        probe_config[key] = str(payload.get(key) or "")

    return probe_config


async def get_torrent_client_probe_status(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    probe_config = build_torrent_client_probe_config(payload)
    try:
        probe_client = get_torrent_client(probe_config)
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Unable to initialize torrent client: {exc}",
            "display_name": get_client_display_name(probe_config.get("TORRENT_CLIENT_TYPE")),
        }

    try:
        status = await probe_client.get_status()
    except Exception as exc:
        app.logger.warning(f"[CLIENT-PROBE] Initial status check failed; retrying login: {exc}")
        try:
            await probe_client.login()
            status = await probe_client.get_status()
        except Exception as retry_exc:
            app.logger.error(f"[CLIENT-PROBE] Retry failed after login attempt: {retry_exc}")
            status = {
                "status": "error",
                "message": f"Torrent client probe failed: {retry_exc}",
                "display_name": getattr(probe_client, "display_name", "Torrent Client"),
            }

    if not status.get("display_name"):
        status["display_name"] = getattr(probe_client, "display_name", "Torrent Client")
    return status


@app.route('/api/settings/test-torrent-client', methods=['POST'])
async def test_torrent_client_settings():
    payload = await request.get_json(silent=True) or {}
    status = await get_torrent_client_probe_status(payload)
    return jsonify(status)


def format_hardcover_probe_error(exc: Exception) -> tuple[str, int | None]:
    raw_message = str(exc or "").strip()
    if isinstance(exc, HardcoverAPIError):
        status_code = exc.status_code
        if status_code is not None:
            details = []
            if exc.code:
                details.append(exc.code)
            if exc.scope:
                details.append(f"required scope: {exc.scope}")
            suffix = f": {', '.join(details)}" if details else ""
            return f"HTTP {status_code}{suffix}", status_code

    if raw_message.startswith("http_"):
        try:
            status_code = int(raw_message.split("_", 1)[1].split(":", 1)[0])
        except (IndexError, ValueError):
            return raw_message or "Hardcover request failed.", None
        return f"HTTP {status_code}", status_code

    if raw_message.startswith("graphql_error:"):
        return raw_message.split(":", 1)[1].strip() or "GraphQL error", None

    if raw_message.startswith("request_error:"):
        return f"Request error: {raw_message.split(':', 1)[1].strip()}", None

    if raw_message.startswith("timeout:"):
        return f"Timeout: {raw_message.split(':', 1)[1].strip()}", None

    return raw_message or "Hardcover request failed.", None


@app.route('/api/settings/test-hardcover', methods=['POST'])
async def test_hardcover_settings():
    payload = await request.get_json(silent=True) or {}
    token = str(payload.get("HARDCOVER_API_TOKEN") or "").strip()
    if not token:
        return jsonify({
            "status": "idle",
            "message": "Enter a Hardcover API key to test the connection.",
        })

    endpoint = str(
        payload.get("HARDCOVER_API_URL")
        or app.config.get("HARDCOVER_API_URL")
        or FALLBACK_CONFIG["HARDCOVER_API_URL"]
    ).strip() or FALLBACK_CONFIG["HARDCOVER_API_URL"]
    user_agent = str(
        app.config.get("HARDCOVER_USER_AGENT")
        or FALLBACK_CONFIG["HARDCOVER_USER_AGENT"]
    ).strip() or FALLBACK_CONFIG["HARDCOVER_USER_AGENT"]
    try:
        rate_limit = int(app.config.get("HARDCOVER_RATE_LIMIT", FALLBACK_CONFIG["HARDCOVER_RATE_LIMIT"]))
    except (TypeError, ValueError):
        rate_limit = FALLBACK_CONFIG["HARDCOVER_RATE_LIMIT"]

    client = HardcoverClient(
        token,
        endpoint=endpoint,
        user_agent=user_agent,
        timeout_seconds=15.0,
        rate_limit=rate_limit,
    )
    try:
        async with client:
            # A single search validates the catalog scope and the operation that
            # enrichment depends on. Account identity is a separate optional
            # capability for library/status features.
            await client.search("MouseSearch connection test", "Book", 1)
            await client.resolve_identity()
    except HardcoverAPIError as exc:
        message, http_status = format_hardcover_probe_error(exc)
        return jsonify({
            "status": "error",
            "message": message,
            "http_status": http_status,
        })
    except Exception as exc:
        return jsonify({
            "status": "error",
            "message": str(exc) or "Hardcover request failed.",
            "http_status": None,
        })

    username = client.viewer_username or ""
    account_id = client.user_id
    if client.user_features_available:
        account_label = username or "Hardcover user"
        message = f"HTTP 200: Connected as {account_label} (ID {account_id})."
    elif client.identity_state == "unavailable":
        message = (
            "HTTP 200: Catalog access is working. This token cannot read the signed-in "
            "account, so library status display and changes will be disabled."
        )
    else:
        message = (
            "HTTP 200: Catalog access is working, but the account capability check "
            "could not be completed. Library status features will remain disabled."
        )

    return jsonify({
        "status": "success",
        "message": message,
        "http_status": 200,
        "user_actions_available": client.user_features_available,
        "me": ({"id": account_id, "username": username} if client.user_features_available else None),
    })

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
    # Kept for {Genre}/{Format}/{Category} when the path template is rendered server-side.
    catname = incoming_data.get('catname', '')
    filetype = incoming_data.get('filetype', '')
    id = incoming_data.get('id', '0')
    category = incoming_data.get('category', app.config.get("TORRENT_CLIENT_CATEGORY", ""))
    torrent_size_str = incoming_data.get('size', '0 GiB')  # e.g., "1.5 GiB"
    use_personal_freeleech_requested = coerce_bool(incoming_data.get('use_personal_freeleech'), False)
    is_public_freeleech = False
    try:
        is_public_freeleech = int(incoming_data.get('free', 0) or 0) == 1
    except (ValueError, TypeError):
        is_public_freeleech = False
    is_vip_freeleech = coerce_bool(incoming_data.get('vip_freeleech'), False)
    is_personal_freeleech = False
    try:
        is_personal_freeleech = int(incoming_data.get('personal_freeleech', 0) or 0) == 1
    except (ValueError, TypeError):
        is_personal_freeleech = False
    should_use_personal_freeleech = False
    if is_public_freeleech:
        app.logger.info("[DOWNLOAD] Personal Freeleech flag skipped: torrent is already public freeleech.")
    elif is_vip_freeleech:
        app.logger.info("[DOWNLOAD] Personal Freeleech flag skipped: torrent is already VIP freeleech.")
    elif is_personal_freeleech:
        app.logger.info("[DOWNLOAD] Personal Freeleech flag skipped: torrent already has personal freeleech.")
    elif use_personal_freeleech_requested:
        should_use_personal_freeleech = True
        app.logger.info(f"[DOWNLOAD] Personal Freeleech will be requested via download URL for torrent {id}")
    elif app.config.get("AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD", False):
        torrent_id_for_fl = None
        try:
            if id not in (None, '', '0', 0):
                torrent_id_for_fl = int(id)
        except (ValueError, TypeError):
            torrent_id_for_fl = None

        if torrent_id_for_fl is not None:
            min_size_enabled = app.config.get("AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_ENABLED", False)
            min_size_mb = app.config.get("AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_MB", 0)
            torrent_size_gb = parse_size_to_gb(torrent_size_str, default=None)
            should_use_personal_freeleech = True

            if min_size_enabled:
                if torrent_size_gb is None:
                    should_use_personal_freeleech = False
                    app.logger.info(
                        f"[DOWNLOAD] Auto Freeleech flag skipped for torrent {torrent_id_for_fl}; "
                        f"could not parse torrent size '{torrent_size_str}' for threshold check."
                    )
                elif torrent_size_gb * 1024 <= min_size_mb:
                    should_use_personal_freeleech = False
                    app.logger.info(
                        f"[DOWNLOAD] Auto Freeleech flag skipped for torrent {torrent_id_for_fl}; "
                        f"size {torrent_size_gb * 1024:.2f} MB is not greater than threshold {min_size_mb:.2f} MB."
                    )

            if should_use_personal_freeleech:
                app.logger.info(
                    f"[DOWNLOAD] Auto Freeleech will be requested via download URL for torrent {torrent_id_for_fl}"
                )

    if should_use_personal_freeleech and torrent_url:
        torrent_url = append_personal_freeleech_flag(torrent_url)
    
    # Check if download should be blocked due to low buffer
    if app.config.get("BLOCK_DOWNLOAD_ON_LOW_BUFFER", True) and await login_mam():
        stats = await get_user_stats()
        if stats:
            torrent_size_gb = parse_size_to_gb(torrent_size_str)
            buffer_gb = stats['buffer_gb']
            
            if torrent_size_gb > buffer_gb:
                # Calculate how much upload credit needed
                needed_gb = torrent_size_gb - buffer_gb
                cost_per_gb = UPLOAD_CREDIT_COST_PER_GB  # bonus points
                
                recommended_amount = max(
                    UPLOAD_CREDIT_MIN_GB,
                    math.ceil(needed_gb)
                )
                
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
                "main_cat": main_cat,
                "catname": catname,
                "filetype": filetype,
                "download_link": download_link,
                "custom_relative_path": custom_relative_path,
                "custom_destination_path": custom_destination_path,
            }

            if resolved_hash:
                if auto_organize_tracking_enabled():
                    metadata = load_database()
                    metadata[resolved_hash] = metadata_payload
                    save_database(metadata)
                    app.logger.info(f"Saved metadata for torrent hash: {resolved_hash}")

                monitoring_state[normalize_info_hash(resolved_hash)] = {
                    "added_at": time.time()
                }
                start_monitoring_loop()

                response_data = {
                    'message': result['message'],
                    'hash': resolved_hash,
                    'personal_freeleech_applied': should_use_personal_freeleech,
                }
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
            
            return jsonify({
                'message': result['message'],
                'personal_freeleech_applied': should_use_personal_freeleech,
            })
        else:
            return jsonify({'error': result.get('message', 'Unknown error')}), 400
    
    # Fallback: No MID or auto-organize disabled - use old hash-based approach
    if not hash_val:
        app.logger.warning(f"WARNING: running hash calculation for torrent URL without MID: {torrent_url}")
        hash_val = await calculate_torrent_hash_from_url(torrent_url)
    
    if auto_organize_tracking_enabled():
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
                "main_cat": main_cat,
                "catname": catname,
                "filetype": filetype,
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

        response_data = {
            'message': result['message'],
            'personal_freeleech_applied': should_use_personal_freeleech,
        }
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
    
def auto_organize_tracking_enabled():
    """
    True when torrent metadata must be persisted for auto-organization.

    Metadata has to be written for BOTH auto-organize modes: the on-add path
    organizes as soon as the download finishes, while the scheduled safety net
    (check_for_unorganized_torrents) can only act on hashes that already have a
    'pending' record in the database. Gating the write on AUTO_ORGANIZE_ON_ADD
    alone left schedule-only users with an empty database and nothing to
    organize (issue #41).
    """
    return bool(
        app.config.get("AUTO_ORGANIZE_ON_ADD")
        or app.config.get("AUTO_ORGANIZE_ON_SCHEDULE")
    )


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


MAIN_CAT_DISPLAY_NAMES = {
    13: "Audiobooks",
    14: "Ebooks",
    15: "Musicology",
    16: "Radio",
}


def split_catname(catname, main_cat=""):
    """
    Split a MAM catname into its (category, genre) parts.

    MAM reports catname as "Audiobooks - Science Fiction". A few categories
    (e.g. "Music Book MP3") have no separator at all, in which case the whole
    string is treated as the genre and the category is derived from main_cat.
    """
    text = str(catname or "").strip()
    category, separator, genre = text.partition(" - ")
    if not separator:
        category, genre = "", text
    category, genre = category.strip(), genre.strip()
    if not category:
        try:
            category = MAIN_CAT_DISPLAY_NAMES.get(int(main_cat), "")
        except (ValueError, TypeError):
            category = ""
    return category, genre


def normalize_series_number(series_number):
    """Trim redundant decimals from a series position. Mirrors normalizeSeriesNumber() in main.js."""
    text = str(series_number if series_number is not None else "").strip()
    if not text:
        return ""
    try:
        parsed = float(text)
    except (TypeError, ValueError):
        return text
    if parsed < 0:
        return ""
    return str(int(parsed)) if parsed.is_integer() else re.sub(r"\.?0+$", "", text)


def format_filetype_token(filetype):
    """Normalize a MAM filetype ("epub mobi") into a path token ("EPUB MOBI")."""
    parts = [p for p in re.split(r"[\s,]+", str(filetype or "")) if p]
    return " ".join(p.upper() for p in parts)


def build_relative_path_from_template(template, values):
    """
    Render a relative path template ("{Author}/{Genre}/{Title}") from token values.

    Values are expected to be pre-sanitized. Tokens that resolve to an empty
    string collapse away, so an unmatched {Series} does not leave an empty
    directory level behind. Mirrors buildRelativePathFromTemplate() in main.js.
    """
    output = str(template or "").replace("\\", "/")
    for token, value in values.items():
        output = output.replace(token, str(value or ""))
    output = re.sub(r"/+", "/", output).strip("/")
    return output


def relative_path_tokens_from_metadata(torrent_meta):
    """Build the template token map for a stored torrent metadata record."""
    category, genre = split_catname(
        torrent_meta.get("catname"),
        torrent_meta.get("main_cat", ""),
    )

    # series_info is normally {"<id>": [name, number]}, but older records written by
    # the search-snatched path stored a plain display string instead.
    raw_series = torrent_meta.get("series_info")
    if isinstance(raw_series, str):
        raw_series = parse_series_info(raw_series)
    if not isinstance(raw_series, dict):
        raw_series = {}

    series_name, series_number = "", ""
    series_entries = list(raw_series.values())
    if series_entries and isinstance(series_entries[0], (list, tuple)) and series_entries[0]:
        first = series_entries[0]
        series_name = str(first[0] or "").strip()
        series_number = normalize_series_number(first[1]) if len(first) > 1 else ""

    def clean(value):
        text = str(value or "").strip()
        return sanitize_filename(text) if text else ""

    return {
        "{Author}": clean(torrent_meta.get("author")),
        "{Series}": clean(series_name),
        "{SeriesNumber}": clean(series_number),
        "{Title}": clean(torrent_meta.get("title")),
        "{Genre}": clean(genre),
        "{Format}": clean(format_filetype_token(torrent_meta.get("filetype"))),
        "{Category}": clean(category),
    }

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


def format_mam_search_error(exc: Exception) -> str:
    detail = str(exc).strip()

    if isinstance(exc, httpx.ConnectTimeout):
        return "MAM timed out while connecting to the server. Please try again."
    if isinstance(exc, httpx.ReadTimeout):
        return "MAM timed out while waiting for a response. Please try again."
    if isinstance(exc, httpx.TimeoutException):
        return "MAM search timed out. Please try again."
    if isinstance(exc, httpx.HTTPStatusError):
        status_code = exc.response.status_code if exc.response is not None else "unknown"
        return f"MAM returned an HTTP {status_code} error."
    if isinstance(exc, httpx.RequestError):
        error_name = exc.__class__.__name__
        if detail:
            return f"MAM request failed ({error_name}: {detail})."
        return f"MAM request failed ({error_name})."

    if detail:
        return f"MAM search failed: {detail}"
    return "MAM search failed due to an unexpected error."


def hardcover_enrichment_is_active() -> bool:
    token = str(app.config.get("HARDCOVER_API_TOKEN") or "").strip()
    return bool(app.config.get("HARDCOVER_ENRICHMENT_ENABLED", True) and token)


def create_hardcover_client() -> HardcoverClient | None:
    global HARDCOVER_CLIENT
    if not hardcover_enrichment_is_active():
        return None

    token = str(app.config.get("HARDCOVER_API_TOKEN") or "").strip()
    endpoint = str(app.config.get("HARDCOVER_API_URL") or FALLBACK_CONFIG["HARDCOVER_API_URL"]).strip()
    user_agent = str(app.config.get("HARDCOVER_USER_AGENT") or FALLBACK_CONFIG["HARDCOVER_USER_AGENT"]).strip()
    rate_limit = int(app.config.get("HARDCOVER_RATE_LIMIT", FALLBACK_CONFIG["HARDCOVER_RATE_LIMIT"]))
    if HARDCOVER_CLIENT is None:
        HARDCOVER_CLIENT = HardcoverClient(
            token,
            endpoint=endpoint,
            user_agent=user_agent,
            timeout_seconds=30.0,
            rate_limit=rate_limit,
        )
    return HARDCOVER_CLIENT


async def preload_hardcover_user_book_cache() -> None:
    global HARDCOVER_USER_BOOK_PRELOAD_ACTIVE

    client = create_hardcover_client()
    if client is None or HARDCOVER_USER_BOOK_PRELOAD_ACTIVE:
        return
    await client.resolve_identity()
    if not client.user_features_available:
        return

    HARDCOVER_USER_BOOK_PRELOAD_ACTIVE = True
    try:
        await client.user_book_map()
    except Exception as exc:
        app.logger.warning(f"[HARDCOVER] User book cache preload failed: {exc}")
    finally:
        HARDCOVER_USER_BOOK_PRELOAD_ACTIVE = False


def get_cached_hardcover_series_response(series_id: int) -> dict | None:
    entry = hardcover_series_response_cache.get(int(series_id))
    if not isinstance(entry, dict):
        return None

    fetched_at = float(entry.get("fetched_at") or 0)
    if (time.time() - fetched_at) > HARDCOVER_SERIES_CACHE_TTL_SECONDS:
        hardcover_series_response_cache.pop(int(series_id), None)
        return None

    payload = entry.get("payload")
    return copy.deepcopy(payload) if isinstance(payload, dict) else None


def set_cached_hardcover_series_response(series_id: int, payload: dict) -> None:
    hardcover_series_response_cache[int(series_id)] = {
        "fetched_at": time.time(),
        "payload": copy.deepcopy(payload),
    }


def serialize_hardcover_user_book(user_book: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(user_book, dict):
        return None

    try:
        user_book_id = int(user_book.get("id"))
        status_id = int(user_book.get("status_id"))
    except (TypeError, ValueError):
        return None
    if user_book_id <= 0 or status_id <= 0:
        return None

    def positive_int(value):
        try:
            number = int(value)
        except (TypeError, ValueError):
            return None
        return number if number >= 0 else None

    def non_negative_float(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number >= 0 else None

    status_obj = user_book.get("user_book_status") or {}
    status_label = str(status_obj.get("status") or "").strip() if isinstance(status_obj, dict) else ""
    return {
        "id": user_book_id,
        "book_id": positive_int(user_book.get("book_id")),
        "edition_id": positive_int(user_book.get("edition_id")),
        "user_id": positive_int(user_book.get("user_id")),
        "status_id": status_id,
        "status": status_label,
        "privacy_setting_id": positive_int(user_book.get("privacy_setting_id")) or 1,
        "rating": non_negative_float(user_book.get("rating")),
        "updated_at": str(user_book.get("updated_at") or "").strip(),
    }


def prune_hardcover_enrichment_batches() -> None:
    cutoff = time.time() - HARDCOVER_ENRICHMENT_BATCH_TTL_SECONDS
    expired = [
        search_id for search_id, batch in hardcover_enrichment_batches.items()
        if float(batch.get("updated_at") or batch.get("created_at") or 0) < cutoff
    ]
    for search_id in expired:
        hardcover_enrichment_batches.pop(search_id, None)


def initialize_hardcover_enrichment_batch(search_id: str, results: list[dict]) -> None:
    prune_hardcover_enrichment_batches()
    now = time.time()
    source_results: dict[str, dict[str, Any]] = {}
    for index, result in enumerate(results):
        torrent_id = str(result.get("id") or "")
        if not torrent_id:
            continue
        source_results[torrent_id] = {
            "index": index,
            "result": result,
        }
    hardcover_enrichment_batches[search_id] = {
        "created_at": now,
        "updated_at": now,
        "completed": True,
        "total": len(results),
        "results": {},
        "source_results": source_results,
        "queued_torrent_ids": set(),
        "shared_enrichments": {},
    }


def store_hardcover_enrichment_result(search_id: str, index: int, torrent_id: str, enrichment: dict) -> None:
    batch = hardcover_enrichment_batches.get(search_id)
    if batch is None:
        initialize_hardcover_enrichment_batch(search_id, [])
        batch = hardcover_enrichment_batches[search_id]

    batch["updated_at"] = time.time()
    queued_torrent_ids = batch.get("queued_torrent_ids")
    if isinstance(queued_torrent_ids, set):
        queued_torrent_ids.discard(torrent_id)
    batch["results"][torrent_id] = {
        "index": index,
        "torrent_id": torrent_id,
        "enrichment": enrichment,
    }


def queue_hardcover_enrichment_results(search_id: str, torrent_ids: list[Any]) -> list[dict[str, Any]]:
    batch = hardcover_enrichment_batches.get(search_id)
    if batch is None:
        return []

    source_results = batch.get("source_results") or {}
    queued_torrent_ids = batch.setdefault("queued_torrent_ids", set())
    results_by_torrent = batch.get("results") or {}
    selected_entries: list[dict[str, Any]] = []
    seen_torrent_ids: set[str] = set()

    for raw_torrent_id in torrent_ids:
        torrent_id = str(raw_torrent_id or "").strip()
        if not torrent_id or torrent_id in seen_torrent_ids:
            continue
        seen_torrent_ids.add(torrent_id)
        if torrent_id in queued_torrent_ids or torrent_id in results_by_torrent:
            continue
        entry = source_results.get(torrent_id)
        if not isinstance(entry, dict):
            continue
        queued_torrent_ids.add(torrent_id)
        selected_entries.append(entry)

    batch["updated_at"] = time.time()
    if selected_entries:
        batch["completed"] = False
    return sorted(selected_entries, key=lambda item: int(item.get("index", 0)))


async def run_hardcover_enrichment_batch(search_id: str, results: list[dict]):
    batch = hardcover_enrichment_batches.get(search_id)
    if batch is None:
        return

    client = create_hardcover_client()
    if client is None:
        queued_torrent_ids = batch.get("queued_torrent_ids")
        if isinstance(queued_torrent_ids, set):
            for result in results:
                queued_torrent_ids.discard(str(result.get("id") or ""))
        batch["completed"] = not batch.get("queued_torrent_ids")
        batch["updated_at"] = time.time()
        return

    threshold = float(app.config.get("HARDCOVER_MATCH_THRESHOLD", FALLBACK_CONFIG["HARDCOVER_MATCH_THRESHOLD"]))
    concurrency = int(app.config.get("HARDCOVER_CONCURRENCY", FALLBACK_CONFIG["HARDCOVER_CONCURRENCY"]))
    per_page = int(app.config.get("HARDCOVER_SEARCH_PER_PAGE", FALLBACK_CONFIG["HARDCOVER_SEARCH_PER_PAGE"]))
    resolver = HardcoverResolver(
        client,
        HardcoverEnrichmentConfig(
            match_threshold=threshold,
            concurrency=concurrency,
            per_page=per_page,
        ),
    )
    runner = HardcoverBatchRunner(resolver, concurrency)
    shared_enrichments = batch.setdefault("shared_enrichments", {})

    async def publish(index: int, result: dict, enrichment: dict):
        torrent_id = str(result.get("id") or "")
        if not torrent_id:
            return
        store_hardcover_enrichment_result(search_id, index, torrent_id, enrichment)
        payload = {
            "event": "hardcover-enrichment",
            "search_id": search_id,
            "torrent_id": torrent_id,
            "index": index,
            "enrichment": enrichment,
        }
        await broadcast_payload(payload)

    started = time.monotonic()
    try:
        await runner.run(results, publish, shared_cache=shared_enrichments)
        batch = hardcover_enrichment_batches.get(search_id)
        if batch is not None:
            batch["completed"] = not batch.get("queued_torrent_ids")
            batch["updated_at"] = time.time()
        hardcover_rpm = await client.rate_controller.current_requests_per_minute()
        app.logger.info(
            f"[HARDCOVER] search_id={search_id} enriched={len(results)} "
            f"duration_ms={(time.monotonic() - started) * 1000:.1f} "
            f"rpm={hardcover_rpm}"
        )
    except Exception as e:
        batch = hardcover_enrichment_batches.get(search_id)
        if batch is not None:
            queued_torrent_ids = batch.get("queued_torrent_ids")
            if isinstance(queued_torrent_ids, set):
                for result in results:
                    queued_torrent_ids.discard(str(result.get("id") or ""))
            batch["completed"] = not batch.get("queued_torrent_ids")
            batch["updated_at"] = time.time()
            batch["error"] = str(e)
        app.logger.error(f"[HARDCOVER] Batch failed search_id={search_id}: {e}", exc_info=True)


@app.route('/hardcover/enrichment/<search_id>', methods=['GET'])
async def hardcover_enrichment_status(search_id):
    prune_hardcover_enrichment_batches()
    batch = hardcover_enrichment_batches.get(str(search_id or ""))
    if not batch:
        return jsonify({
            "search_id": search_id,
            "completed": False,
            "total": 0,
            "results": [],
        })

    results = sorted(
        batch.get("results", {}).values(),
        key=lambda item: int(item.get("index", 0)),
    )
    return jsonify({
        "search_id": search_id,
        "completed": bool(batch.get("completed")),
        "total": int(batch.get("total") or 0),
        "results": results,
        "error": batch.get("error", ""),
    })


@app.route('/hardcover/enrichment/<search_id>/queue', methods=['POST'])
async def hardcover_enrichment_queue(search_id):
    prune_hardcover_enrichment_batches()
    normalized_search_id = str(search_id or "")
    batch = hardcover_enrichment_batches.get(normalized_search_id)
    if not batch:
        return jsonify({
            "status": "error",
            "message": "Hardcover enrichment batch not found.",
            "search_id": normalized_search_id,
        }), 404

    data = await request.get_json(silent=True) or {}
    torrent_ids = data.get("torrent_ids") or []
    if not isinstance(torrent_ids, list):
        return jsonify({
            "status": "error",
            "message": "torrent_ids must be an array.",
            "search_id": normalized_search_id,
        }), 400

    queued_entries = queue_hardcover_enrichment_results(normalized_search_id, torrent_ids)
    queued_results = [copy.deepcopy(entry.get("result") or {}) for entry in queued_entries if isinstance(entry.get("result"), dict)]
    if queued_results:
        app.add_background_task(
            run_hardcover_enrichment_batch,
            normalized_search_id,
            queued_results,
        )

    return jsonify({
        "status": "success",
        "search_id": normalized_search_id,
        "queued": len(queued_results),
        "completed": bool(batch.get("completed")),
    })


@app.route('/hardcover/series/<int:series_id>', methods=['GET'])
async def hardcover_series_details(series_id):
    client = create_hardcover_client()
    if client is None or int(series_id) <= 0:
        return jsonify({"series": []})

    cached_payload = get_cached_hardcover_series_response(series_id)
    if cached_payload is not None:
        return jsonify(cached_payload)

    def extract_image_url(image):
        if not image:
            return ""
        if isinstance(image, str):
            return image
        if isinstance(image, dict):
            for key in ("url", "image_url", "large", "medium", "small", "original"):
                value = image.get(key)
                if isinstance(value, str) and value:
                    return value
        return ""

    def normalize_position(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    try:
        series = await client.series_details(series_id)
        if not isinstance(series, dict):
            return jsonify({"series": []})

        entries = []
        for entry in series.get("book_series") or []:
            if not isinstance(entry, dict):
                continue
            book = entry.get("book") or {}
            if not isinstance(book, dict):
                continue
            title = str(book.get("title") or "").strip()
            slug = str(book.get("slug") or "").strip()
            if not title or not slug:
                continue
            entries.append({
                "position": normalize_position(entry.get("position")),
                "book": {
                    "id": book.get("id"),
                    "slug": slug,
                    "title": title,
                    "release_year": book.get("release_year"),
                    "image_url": extract_image_url(book.get("image")),
                },
            })

        entries.sort(key=lambda item: (
            item.get("position") is None,
            item.get("position") if item.get("position") is not None else float("inf"),
            str(item.get("book", {}).get("title") or "").lower(),
        ))

        payload = {
            "series": [{
                "id": series.get("id"),
                "name": series.get("name") or "",
                "slug": series.get("slug") or "",
                "author": {
                    "name": str((series.get("author") or {}).get("name") or "").strip(),
                    "slug": str((series.get("author") or {}).get("slug") or "").strip(),
                },
                "books_count": int(series.get("books_count") or len(entries) or 0),
                "book_series": entries,
            }]
        }
        set_cached_hardcover_series_response(series_id, payload)
        return jsonify(payload)
    except Exception as exc:
        app.logger.error(f"[HARDCOVER] Series fetch failed series_id={series_id}: {exc}", exc_info=True)
        if cached_payload is not None:
            return jsonify(cached_payload)
        return jsonify({"series": [], "error": str(exc)}), 500


@app.route('/hardcover/user-book/<int:book_id>', methods=['GET'])
async def hardcover_get_user_book(book_id):
    client = create_hardcover_client()
    if client is None:
        return jsonify({
            "status": "error",
            "message": "Hardcover integration is not configured.",
        }), 503

    if int(book_id) <= 0:
        return jsonify({
            "status": "error",
            "message": "A valid Hardcover book_id is required.",
        }), 400

    await client.resolve_identity()
    if not client.user_features_available:
        return jsonify({
            "status": "error",
            "message": "This Hardcover token cannot read the signed-in account, so library status actions are unavailable.",
        }), 403

    try:
        user_book = await client.user_book_for_book(book_id)
    except Exception as exc:
        app.logger.error(f"[HARDCOVER] User book lookup failed book_id={book_id}: {exc}", exc_info=True)
        return jsonify({
            "status": "error",
            "message": f"Hardcover status lookup failed: {exc}",
        }), 502

    return jsonify({
        "status": "success",
        "book_id": int(book_id),
        "user_book": serialize_hardcover_user_book(user_book),
    })


@app.route('/hardcover/user-book/status', methods=['POST'])
async def hardcover_update_user_book_status():
    client = create_hardcover_client()
    if client is None:
        return jsonify({
            "status": "error",
            "message": "Hardcover integration is not configured.",
        }), 503

    await client.resolve_identity()
    if not client.user_features_available:
        return jsonify({
            "status": "error",
            "message": "This Hardcover token cannot read the signed-in account, so status changes are disabled.",
        }), 403

    payload = await request.get_json(silent=True) or {}
    action = str(payload.get("action") or "").strip().lower()
    try:
        book_id = int(payload.get("book_id") or 0)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Invalid Hardcover status request."}), 400

    if book_id <= 0:
        return jsonify({"status": "error", "message": "Missing Hardcover book ID."}), 400

    status_id = None
    if action != "remove":
        try:
            status_id = int(payload.get("status_id") or 0)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "Invalid Hardcover status request."}), 400
        if status_id not in {1, 2, 3, 5}:
            return jsonify({"status": "error", "message": "Unsupported Hardcover status."}), 400

    try:
        current_user_book = await client.user_book_for_book(book_id)
        serialized_current = serialize_hardcover_user_book(current_user_book)
        if serialized_current is None:
            if action == "remove":
                return jsonify({
                    "status": "error",
                    "message": "This Hardcover title is not in your library yet, so there is no status to remove.",
                }), 404

            assert status_id is not None
            created_user_book = await client.create_user_book(
                book_id,
                status_id=status_id,
                privacy_setting_id=1,
            )
            serialized_created = serialize_hardcover_user_book(created_user_book)
            if serialized_created is None:
                return jsonify({
                    "status": "error",
                    "message": "Hardcover returned an invalid status create response.",
                }), 502
            return jsonify({
                "status": "success",
                "message": "Hardcover status added.",
                "book_id": book_id,
                "user_book": serialized_created,
            })

        if action == "remove":
            await client.delete_user_book(serialized_current["id"])
            return jsonify({
                "status": "success",
                "message": "Hardcover status removed.",
                "book_id": book_id,
                "user_book": None,
            })

        assert status_id is not None
        if serialized_current["status_id"] == status_id:
            return jsonify({
                "status": "success",
                "message": "Hardcover status is already set.",
                "book_id": book_id,
                "user_book": serialized_current,
            })

        updated_user_book = await client.update_user_book_status(
            serialized_current["id"],
            status_id,
            edition_id=serialized_current.get("edition_id"),
            privacy_setting_id=serialized_current.get("privacy_setting_id"),
            rating=serialized_current.get("rating"),
        )
    except HardcoverAPIError as exc:
        return jsonify({
            "status": "error",
            "message": f"Hardcover status update failed: {exc}",
        }), 502

    serialized_updated = serialize_hardcover_user_book(updated_user_book)
    if serialized_updated is None:
        return jsonify({
            "status": "error",
            "message": "Hardcover returned an invalid status update response.",
        }), 502

    return jsonify({
        "status": "success",
        "message": "Hardcover status updated.",
        "book_id": book_id,
        "user_book": serialized_updated,
    })


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
    search_id = uuid.uuid4().hex[:12]

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
        "tor[searchType]": request.args.get("searchType", "all"),
        "isbn": "true", "description": "true", "mediaInfo": "true"
    }
    srch_in_fields = {
        "title": title_on, "author": author_on, "narrator": narrator_on,
        "series": series_on, "description": description_on,
        "tags": tags_on, "filenames": filenames_on,
    }
    for field, enabled in srch_in_fields.items():
        if enabled:
            params[f"tor[srchIn][{field}]"] = "true"
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
        response = await request_mam(
            "GET",
            f"{app.config['MAM_API_URL']}/tor/js/loadSearchJSONbasic.php",
            params=params,
            headers=headers,
        )
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
            # MAM rejects the link with "Invalid download link: missing tid"
            # unless the torrent id is passed as a query param.
            dl_hash = item.get('dl')
            torrent_id = item.get('id')
            item['download_link'] = build_mam_download_link(
                base_dl_url,
                dl_hash,
                torrent_id,
            )

            # 2. Handle Thumbnails
            if not item.get('thumbnail'):
                if item.get('id'):
                    item['thumbnail'] = f"https://cdn.myanonamouse.net/t/p/small/{item['id']}.webp"
                    item["has_mam_cover"] = True
                else:
                    cat = item.get('category', '')
                    item['thumbnail'] = f"https://static.myanonamouse.net/pic/cats/3/{cat}.png"
                    item["has_mam_cover"] = False
            else:
                item["has_mam_cover"] = True

            # 3. Decode Metadata (Author, Narrator, Series)
            # Note: rank_results may have already partially parsed these into strings.
            # parse_mam_metadata handles both JSON strings AND plain strings safely.
            item['author_info'] = parse_mam_metadata(item.get('author_info', ''))
            item['narrator_info'] = parse_mam_metadata(item.get('narrator_info', ''))

            # Overwrite series_display with our cleaner, HTML-decoded version
            item['series_display'] = parse_mam_metadata(item.get('series_info', ''), is_series=True)

            # Carry the server-evaluated VIP entitlement into the download payload so
            # the download route does not spend a wedge on VIP Freeleech torrents.
            item['vip_freeleech'] = int(
                coerce_bool(item.get('fl_vip'), False) and is_vip_active
            )

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

        rendered_results = await render_template(
            "partials/results.html",
            results=display_results,
            search_id=search_id,
            HARDCOVER_ENRICHMENT_ACTIVE=hardcover_enrichment_is_active(),
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
        if display_results and hardcover_enrichment_is_active():
            initialize_hardcover_enrichment_batch(search_id, copy.deepcopy(display_results))
        return rendered_results
    except Exception as e:
        error_message = format_mam_search_error(e)
        app.logger.error(
            f"[SEARCH] Failed query_len={len(query)}: {error_message}",
            exc_info=True,
        )
        return await render_template(
            "partials/results.html",
            error_message=error_message,
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
    if hardcover_enrichment_is_active():
        app.add_background_task(preload_hardcover_user_book_cache)

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
    route_mode = str(request.args.get("route", "direct") or "direct").strip().lower()
    use_mam_route = route_mode == "mam"

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
    resolver_errors: list[str] = []

    try:
        for resolver_name, resolver_url in resolvers:
            try:
                response = await request_with_optional_proxy(
                    "GET",
                    resolver_url,
                    force_proxy=use_mam_route,
                    force_direct=not use_mam_route,
                    allow_fallback=not use_mam_route,
                    headers={"Accept": "application/json, text/plain;q=0.9, */*;q=0.8"},
                    timeout=5.0,
                )
                response.raise_for_status()

                resolved_ip = extract_ip(response.text)
                if resolved_ip:
                    return jsonify({
                        'ip': resolved_ip,
                        'route': 'mam' if use_mam_route else 'direct',
                        'proxy_status': build_mam_proxy_status_payload(),
                    })

                app.logger.warning(
                    f"Public IP resolver {resolver_name} returned an unusable response"
                )
                resolver_errors.append(f"{resolver_name}: unusable response")
            except Exception as resolver_error:
                error_text = f"{resolver_name}: {resolver_error}"
                resolver_errors.append(error_text)
                app.logger.warning(
                    f"Public IP resolver {resolver_name} failed: {resolver_error}"
                )
    except Exception as e:
        app.logger.error(f"Failed to fetch public IP: {e}")

    app.logger.error("Failed to fetch public IP from all configured resolvers")
    return jsonify({
        'error': 'Could not fetch IP',
        'resolver_errors': resolver_errors,
        'route': 'mam' if use_mam_route else 'direct',
        'proxy_status': build_mam_proxy_status_payload(),
    }), 500
    
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
    await ensure_mam_session_cookie()
    fwd_headers = {h: request.headers.get(h) for h in ("If-None-Match", "If-Modified-Since", "Range") if request.headers.get(h)}
    
    async with FETCH_SEMAPHORE:
        # We allow up to 3 redirects manually to ensure we attach cookies every time
        redirect_count = 0
        current_url = url
        
        while redirect_count < 3:
            # Disable auto-follow so we can inspect the headers ourselves
            r = await send_mam_stream(
                "GET",
                current_url,
                headers=fwd_headers,
                cookies=mam_session_cookies,
                follow_redirects=False,
            )
            
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
    previous_mam_id = normalize_mam_cookie_value(app.config.get("MAM_ID"))
    boolean_fields = {
        "AUTO_ORGANIZE_ON_ADD",
        "AUTO_ORGANIZE_ON_SCHEDULE",
        "AUTO_ORGANIZE_USE_COPY",
        "HAPTICS_ENABLED",
        "HARDCOVER_ENRICHMENT_ENABLED",
        "ENABLE_DYNAMIC_IP_UPDATE",
        "MAM_PROXY_ENABLED",
        "MAM_PROXY_ONLY",
        "MAM_PROXY_FALLBACK_DIRECT",
        "AUTO_BUY_VIP",
        "AUTO_BUY_UPLOAD_ON_RATIO",
        "AUTO_BUY_UPLOAD_ON_BUFFER",
        "AUTO_BUY_UPLOAD_ON_BONUS",
        "BLOCK_DOWNLOAD_ON_LOW_BUFFER",
        "AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD",
        "AUTO_BUY_PERSONAL_FL_ON_DOWNLOAD_MIN_SIZE_ENABLED",
    }
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
    next_mam_id = normalize_mam_cookie_value(config_to_update.get("MAM_ID"))
    if next_mam_id != previous_mam_id:
        invalidate_dynamic_ip_state("MAM session cookie changed")
    save_config(config_to_update)
    await load_new_app_config()
    if app.config.get("ENABLE_DYNAMIC_IP_UPDATE"):
        interval_seconds = int(
            app.config.get(
                "DYNAMIC_IP_CHECK_INTERVAL_SECONDS",
                FALLBACK_CONFIG["DYNAMIC_IP_CHECK_INTERVAL_SECONDS"],
            )
        )
        misfire_grace_seconds = max(1, int(interval_seconds * 0.8))
        scheduler.add_job(
            check_and_update_ip,
            'interval',
            seconds=interval_seconds,
            id='ip_check_job',
            replace_existing=True,
            misfire_grace_time=misfire_grace_seconds,
        )
        scheduler.add_job(
            id='manual_ip_update_job',
            func=check_and_update_ip,
            trigger='date',
            run_date=datetime.now() + timedelta(seconds=2),
            replace_existing=True,
        )
    else:
        for job_id in ('ip_check_job', 'initial_ip_check_job', 'manual_ip_update_job'):
            try:
                scheduler.remove_job(job_id)
            except:
                pass
    
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

    # Update the auto-organize safety net based on new settings. Without this the
    # job was only ever registered during startup(), so toggling the setting in the
    # UI did nothing until the app was restarted (issue #41).
    if app.config.get("AUTO_ORGANIZE_ON_SCHEDULE"):
        organize_hours = int(app.config.get("AUTO_ORGANIZE_INTERVAL_HOURS", 1) or 1)
        scheduler.add_job(
            check_for_unorganized_torrents,
            'interval',
            hours=organize_hours,
            id='organize_safety_net_job',
            replace_existing=True,
            misfire_grace_time=max(1, int(organize_hours * 3600 * 0.8)),
        )
        app.logger.info(f"Auto-organize safety net scheduled every {organize_hours}h.")
    else:
        try:
            scheduler.remove_job('organize_safety_net_job')
            app.logger.info("Auto-organize safety net disabled.")
        except:
            pass

    # Get the new display name from the source of truth
    new_type = config_to_update.get("TORRENT_CLIENT_TYPE")
    display_name = get_client_display_name(new_type)

    return jsonify({
        "status": "success", 
        "message": "Settings updated!",
        "client_display_name": display_name,
        "proxy_status": build_mam_proxy_status_payload(),
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


@app.route("/update_results_sort_mode", methods=["POST"])
async def update_results_sort_mode():
    payload = await request.get_json(silent=True) or {}
    requested_sort_mode = str(payload.get("sort_mode") or "").strip()
    if requested_sort_mode not in RESULTS_SORT_MODES:
        return jsonify({"status": "error", "message": "Invalid results sort mode."}), 400

    config_to_update = app.config.copy()
    config_to_update["RESULTS_SORT_MODE"] = requested_sort_mode
    save_config(config_to_update)
    app.config["RESULTS_SORT_MODE"] = requested_sort_mode
    return jsonify({"status": "success", "sort_mode": requested_sort_mode})


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

def build_source_tree_snapshot(content_path: Path) -> tuple[tuple[str, int], ...] | None:
    """Return a deterministic snapshot of the current source files."""
    try:
        if not content_path.exists():
            return None

        if content_path.is_file():
            stat_result = content_path.stat()
            return ((content_path.name, int(stat_result.st_size)),)

        if not content_path.is_dir():
            return tuple()

        entries = []
        source_files = sorted(
            (path for path in content_path.rglob('*') if path.is_file()),
            key=lambda path: path.as_posix().casefold(),
        )
        for source_file in source_files:
            try:
                stat_result = source_file.stat()
            except FileNotFoundError:
                return None
            rel_path = source_file.relative_to(content_path).as_posix()
            entries.append((rel_path, int(stat_result.st_size)))
        return tuple(entries)
    except FileNotFoundError:
        return None


async def wait_for_stable_source_tree(
    content_path: Path,
    *,
    poll_interval_seconds: int = 2,
    max_wait_seconds: int = 30,
    required_stable_count: int = 2,
) -> tuple[bool, tuple[tuple[str, int], ...] | None]:
    """Wait until the source tree stops changing across consecutive polls."""
    deadline = time.monotonic() + max_wait_seconds
    previous_snapshot = None
    last_snapshot = None
    stable_count = 0

    while True:
        snapshot = build_source_tree_snapshot(content_path)
        last_snapshot = snapshot

        if snapshot and snapshot == previous_snapshot:
            stable_count += 1
        else:
            stable_count = 0

        previous_snapshot = snapshot

        if snapshot and stable_count >= required_stable_count:
            return True, snapshot

        if time.monotonic() >= deadline:
            return False, last_snapshot

        await asyncio.sleep(poll_interval_seconds)

async def _perform_organization(hash_val: str, *, require_stable_source: bool = False) -> tuple[bool, str]:
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
        # No per-download path was chosen (the confirm modal only builds one when
        # AUTO_ORGANIZE_ON_ADD is on), so render the configured template here.
        template = str(app.config.get("REL_PATH_TEMPLATE") or FALLBACK_CONFIG["REL_PATH_TEMPLATE"])
        rel_path = build_relative_path_from_template(template, relative_path_tokens_from_metadata(torrent_meta))
        if not rel_path:
            rel_path = str(Path(sanitize_filename(torrent_meta.get('author') or '')) / sanitize_filename(torrent_meta.get('title') or ''))
        dest_path = organized_path / rel_path
    # --- CHANGED LOGIC END ---
    
    stable_snapshot = None
    if require_stable_source:
        source_ready, stable_snapshot = await wait_for_stable_source_tree(
            content_path,
            poll_interval_seconds=2,
            max_wait_seconds=30,
            required_stable_count=2,
        )

        if not content_path.exists():
            app.logger.debug(f"[ORGANIZE] Source path missing: {content_path}")
            await broadcast_toast(f"Auto-organization failed for '{torrent_meta.get('title', 'Unknown')}': Source path missing", "danger")
            return False, f"Source missing: {content_path}"

        if not source_ready or not stable_snapshot:
            app.logger.warning(f"[ORGANIZE] Source tree did not stabilize within 30s: {content_path}")
            await broadcast_toast(
                f"Auto-organization delayed for '{torrent_meta.get('title', 'Unknown')}': Source files still changing",
                "warning"
            )
            return False, f"Source tree did not stabilize within 30s: {content_path}"
    else:
        stable_snapshot = build_source_tree_snapshot(content_path)
        if not content_path.exists():
            app.logger.debug(f"[ORGANIZE] Source path missing: {content_path}")
            await broadcast_toast(f"Auto-organization failed for '{torrent_meta.get('title', 'Unknown')}': Source path missing", "danger")
            return False, f"Source missing: {content_path}"
        if not stable_snapshot:
            await broadcast_toast(f"Auto-organization delayed for '{torrent_meta.get('title', 'Unknown')}': No files linked", "warning")
            return False, "No files found."
    
    try: dest_path.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        app.logger.error(f"[ORGANIZE] Failed to create destination path {dest_path}: {e}")
        return False, f"Dest create failed: {e}"
    
    files_linked, files_exist = 0, 0
    failed_files = []
    source_base_path = content_path.parent if content_path.is_file() else content_path

    for rel_path_str, _ in stable_snapshot:
        source_file = source_base_path / Path(rel_path_str)
        dest_file = dest_path / Path(rel_path_str)
        try:
            dest_file.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            failed_files.append((rel_path_str, f"Destination parent create failed: {e}"))
            continue

        if dest_file.exists():
            files_exist += 1
            app.logger.debug(f"[ORGANIZE] Exists: {dest_file}")
            continue

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
            failed_files.append((rel_path_str, str(e)))

    total = files_linked + files_exist
    if total == 0:
        await broadcast_toast(f"Auto-organization delayed for '{torrent_meta.get('title', 'Unknown')}': No files linked", "warning")
        return False, "No files found."

    final_snapshot = build_source_tree_snapshot(content_path)
    if require_stable_source and final_snapshot != stable_snapshot:
        app.logger.warning(f"[ORGANIZE] Source tree changed during organization for {hash_val}")
        await broadcast_toast(
            f"Auto-organization delayed for '{torrent_meta.get('title', 'Unknown')}': Source files changed during linking",
            "warning"
        )
        return False, f"Source tree changed during organization: {content_path}"

    if failed_files:
        failed_count = len(failed_files)
        first_failed_path, first_error = failed_files[0]
        await broadcast_toast(
            f"Auto-organization delayed for '{torrent_meta.get('title', 'Unknown')}': {failed_count} file operation(s) failed",
            "warning"
        )
        return False, (
            f"Organization incomplete: {failed_count} file operation(s) failed. "
            f"First failure: {first_failed_path} ({first_error})"
        )
    
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
        if not pending:
            organized = sum(1 for m in metadata.values() if m.get('status') == 'organized')
            app.logger.info(
                f"[SAFETY NET] Nothing to organize: {len(metadata)} tracked torrent(s) "
                f"({organized} already organized, 0 pending). If downloads are missing here, "
                f"they were added while both AUTO_ORGANIZE_ON_ADD and AUTO_ORGANIZE_ON_SCHEDULE "
                f"were disabled and are not tracked."
            )
        succeeded = 0
        failed = 0
        last_error = None
        for h in pending:
            try:
                success, msg = await _perform_organization(h)
                if success:
                    succeeded += 1
                else:
                    failed += 1
                    last_error = msg
                    app.logger.warning(f"[SAFETY NET] Organization failed for {h}: {msg}")
            except Exception as e:
                failed += 1
                last_error = str(e)
                app.logger.error(f"[SAFETY NET] Exception during organization of {h}: {e}", exc_info=True)
        if pending:
            await send_auto_task_webhook_notification(
                "auto_organize_on_schedule",
                failed == 0 and succeeded > 0,
                task="organize_on_schedule",
                pending_count=len(pending),
                organized_count=succeeded,
                failed_count=failed,
                error=last_error,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=None, type=int)
    args = parser.parse_args()
    
    # Priority: CLI arg > PORT env var > hardcoded default (5000)
    port = args.port or int(os.getenv("PORT", 5000))
    
    app.run(host=args.host, port=port, debug=True, use_reloader=False)
