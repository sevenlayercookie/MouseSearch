# app.py - Quart (async) version
from quart import Quart, request, render_template, Response, jsonify, send_file
import httpx
import json
import argparse
import os
import time
import hashlib
from datetime import datetime, timedelta
from dotenv import load_dotenv
from httpx import Limits, Timeout, AsyncHTTPTransport
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import re
from pathlib import Path

import logging # for hypercorn logging
import sys # for stderr logging

from static.language_dict import language_dict

import asyncio

from clients import get_torrent_client, get_client_display_name, get_available_clients
from hashing import calculate_torrent_hash_from_url

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

@app.before_serving
async def startup():
    # 1. Load the configuration FIRST
    await load_new_app_config()

    # 2. Use app.config (instead of initial_config) to check settings
    if app.config.get("ENABLE_FILESYSTEM_THUMBNAIL_CACHE", True):
        app.logger.debug("Cache cleanup task started")
        app.add_background_task(cleanup_cache_task)
    
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
            monitoring_state[h] = {"added_at": current_time - 20} 
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
    
    global monitor_task
    if monitor_task:
        monitor_task.cancel()


# --- LOGGING CONFIGURATION (NOISY LIBS SILENCED) ---
# Configure root logger
logging.basicConfig(
    level=logging.DEBUG,
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

if __name__ != '__main__':
    logger = logging.getLogger('hypercorn.error')
    app.logger.handlers = logger.handlers
    app.logger.setLevel(logging.DEBUG)
else:
    app.logger.setLevel(logging.DEBUG)

scheduler = AsyncIOScheduler()

load_dotenv()

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
    "TORRENT_DOWNLOAD_PATH": "/downloads/torrents",
    "AUTO_ORGANIZE_ON_ADD": False,
    "AUTO_ORGANIZE_ON_SCHEDULE": False,
    "AUTO_ORGANIZE_INTERVAL_HOURS": 1,
    "ENABLE_DYNAMIC_IP_UPDATE": False,
    "DYNAMIC_IP_UPDATE_INTERVAL_HOURS": 3,
    "AUTO_BUY_VIP": False,
    "AUTO_BUY_VIP_INTERVAL_HOURS": 24,
    "AUTO_BUY_UPLOAD_ON_RATIO": False,
    "AUTO_BUY_UPLOAD_RATIO_THRESHOLD": 1.5,
    "AUTO_BUY_UPLOAD_RATIO_AMOUNT": 10,
    "AUTO_BUY_UPLOAD_ON_BUFFER": False,
    "AUTO_BUY_UPLOAD_BUFFER_THRESHOLD": 10,
    "AUTO_BUY_UPLOAD_BUFFER_AMOUNT": 10,
    "AUTO_BUY_UPLOAD_CHECK_INTERVAL_HOURS": 6,
    "BLOCK_DOWNLOAD_ON_LOW_BUFFER": True,
    "ENABLE_FILESYSTEM_THUMBNAIL_CACHE": True,
    "THUMBNAIL_CACHE_MAX_SIZE_MB": 500
}

# Set up data directory and paths
DATA_PATH = Path(os.getenv("DATA_PATH", FALLBACK_CONFIG["DATA_PATH"])).resolve()
DATA_PATH.mkdir(parents=True, exist_ok=True)

UPLOAD_OPTIONS_FILE = Path("./static/upload_options.json")

CONFIG_FILE = DATA_PATH / "config.json"
DATABASE_FILE = DATA_PATH / "database.json"
IP_STATE_FILE = DATA_PATH / "ip_state.json"


# --- Setup:thumbnail cache ---
THUMB_CACHE_DIR = DATA_PATH / "cache/thumbnails"

# These will be set from config
ORGANIZED_PATH = None
TORRENT_DOWNLOAD_PATH = None

def load_config():
    config = FALLBACK_CONFIG.copy()
    json_config = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
             json_config = json.load(f)

    env_config = {key: os.getenv(key) for key in config.keys() if os.getenv(key) is not None}
    config.update(env_config) 
    config.update(json_config) 
    return config

def save_config(config):
    config_to_save = {key: config.get(key) for key in FALLBACK_CONFIG.keys()}
    with open(CONFIG_FILE, "w") as f:
        json.dump(config_to_save, f, indent=4)

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
    
async def load_new_app_config():
    new_config = load_config()
    app.secret_key = new_config["QUART_SECRET_KEY"]
    app.config.update(new_config)
    
    # Load upload options
    app.config["UPLOAD_OPTIONS"] = load_upload_options()
    
    # Update path globals
    global ORGANIZED_PATH, TORRENT_DOWNLOAD_PATH
    ORGANIZED_PATH = Path(new_config.get("ORGANIZED_PATH", FALLBACK_CONFIG["ORGANIZED_PATH"])).resolve()
    TORRENT_DOWNLOAD_PATH = Path(new_config.get("TORRENT_DOWNLOAD_PATH", FALLBACK_CONFIG["TORRENT_DOWNLOAD_PATH"])).resolve()
    
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
                            torrent_hash = torrent.get('hash', '')
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

            active_hashes = list(monitoring_state.keys())
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
                app.logger.info(f"[MONITOR] Torrent {h} finished. Triggering Auto-Organize.")

                if h in torrents_info:
                    final_status = {h: torrents_info[h]}
                    await broadcast_payload({
                        "event": "torrent-progress",
                        "torrents": final_status
                    })

                try:
                    success, msg = await _perform_organization(h)
                    if not success:
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

if app.config.get("ENABLE_DYNAMIC_IP_UPDATE"):
    interval_hours = int(app.config.get("DYNAMIC_IP_UPDATE_INTERVAL_HOURS", 3))
    scheduler.add_job(check_and_update_ip, 'interval', hours=interval_hours, id='ip_check_job', replace_existing=True)
    scheduler.add_job(check_and_update_ip, 'date', run_date=datetime.now() + timedelta(seconds=5), id='initial_ip_check_job')

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

if app.config.get("AUTO_BUY_VIP"):
    interval_hours = int(app.config.get("AUTO_BUY_VIP_INTERVAL_HOURS", 24))
    scheduler.add_job(auto_buy_vip, 'interval', hours=interval_hours, id='vip_buy_job', replace_existing=True)
    scheduler.add_job(auto_buy_vip, 'date', run_date=datetime.now() + timedelta(seconds=10), id='initial_vip_buy_job')

# --- UPLOAD CREDIT AUTO-BUY SCHEDULER ---
async def check_and_buy_upload():
    """Check ratio and buffer thresholds, auto-purchase upload credit if needed."""
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
        
        purchased = False
        
        # Check ratio threshold
        if ratio_check_enabled:
            ratio_threshold = float(app.config.get("AUTO_BUY_UPLOAD_RATIO_THRESHOLD", 1.5))
            if stats['ratio'] < ratio_threshold:
                amount = float(app.config.get("AUTO_BUY_UPLOAD_RATIO_AMOUNT", 10))
                app.logger.info(f"[AUTO-UPLOAD] Ratio {stats['ratio']} below threshold {ratio_threshold}, purchasing {amount} GB")
                
                try:
                    epoch_ms = int(time.time() * 1000)
                    api_url = f"{app.config.get('MAM_API_URL')}/json/bonusBuy.php/"
                    params = {'spendtype': 'upload', 'amount': amount, '_': epoch_ms}
                    
                    async with httpx.AsyncClient() as client:
                        response = await client.get(api_url, params=params, cookies=mam_session_cookies, timeout=10)
                        update_cookies(response)
                        response.raise_for_status()
                        result = response.json()
                        
                        if result.get('success'):
                            app.logger.info(f"[AUTO-UPLOAD-RATIO] Purchase successful - {amount} GB added")
                            await broadcast_payload({
                                'event': 'upload_purchase',
                                'success': True,
                                'amount': amount,
                                'reason': 'ratio',
                                'seedbonus': result.get('seedbonus')
                            })
                            purchased = True
                        else:
                            app.logger.warning(f"[AUTO-UPLOAD-RATIO] Purchase failed: {result}")
                except Exception as e:
                    app.logger.error(f"[AUTO-UPLOAD-RATIO] Error: {e}")
        
        # Check buffer threshold (only if we didn't already purchase)
        if buffer_check_enabled and not purchased:
            buffer_threshold = float(app.config.get("AUTO_BUY_UPLOAD_BUFFER_THRESHOLD", 10))
            if stats['buffer_gb'] < buffer_threshold:
                amount = float(app.config.get("AUTO_BUY_UPLOAD_BUFFER_AMOUNT", 10))
                app.logger.info(f"[AUTO-UPLOAD] Buffer {stats['buffer_gb']:.2f} GB below threshold {buffer_threshold} GB, purchasing {amount} GB")
                
                try:
                    epoch_ms = int(time.time() * 1000)
                    api_url = f"{app.config.get('MAM_API_URL')}/json/bonusBuy.php/"
                    params = {'spendtype': 'upload', 'amount': amount, '_': epoch_ms}
                    
                    async with httpx.AsyncClient() as client:
                        response = await client.get(api_url, params=params, cookies=mam_session_cookies, timeout=10)
                        update_cookies(response)
                        response.raise_for_status()
                        result = response.json()
                        
                        if result.get('success'):
                            app.logger.info(f"[AUTO-UPLOAD-BUFFER] Purchase successful - {amount} GB added")
                            await broadcast_payload({
                                'event': 'upload_purchase',
                                'success': True,
                                'amount': amount,
                                'reason': 'buffer',
                                'seedbonus': result.get('seedbonus')
                            })
                        else:
                            app.logger.warning(f"[AUTO-UPLOAD-BUFFER] Purchase failed: {result}")
                except Exception as e:
                    app.logger.error(f"[AUTO-UPLOAD-BUFFER] Error: {e}")

if app.config.get("AUTO_BUY_UPLOAD_ON_RATIO") or app.config.get("AUTO_BUY_UPLOAD_ON_BUFFER"):
    interval_hours = int(app.config.get("AUTO_BUY_UPLOAD_CHECK_INTERVAL_HOURS", 6))
    scheduler.add_job(check_and_buy_upload, 'interval', hours=interval_hours, id='upload_check_job', replace_existing=True)
    scheduler.add_job(check_and_buy_upload, 'date', run_date=datetime.now() + timedelta(seconds=15), id='initial_upload_check_job')

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

# --- QUART ROUTES ---
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
    Buy upload credit using pre-calculated recipes.
    Accepts 'max' or a specific number found in upload_options.json.
    """
    if not await login_mam():
        return jsonify({'success': False, 'error': 'Not logged into MAM'}), 401
    
    data = await request.get_json()
    raw_amount = data.get('amount')

    # 1. Handle 'max' special case
    if str(raw_amount).lower() == 'max':
        chunks = ['max']
        app.logger.info("Processing 'max' upload purchase.")

    # 2. Handle numeric amounts via lookup table
    else:
        # Normalize input to string key (e.g. 10 -> "10", 2.5 -> "2.5")
        try:
            val = float(raw_amount)
            key = str(int(val)) if val.is_integer() else str(val)
        except (ValueError, TypeError):
            return jsonify({'success': False, 'error': 'Invalid amount format'}), 400

        options = app.config.get("UPLOAD_OPTIONS", {})
        
        if key not in options:
             return jsonify({
                'success': False, 
                'error': f'Invalid amount: {raw_amount} GB. Valid amounts are limited to 3 requests.'
            }), 400
            
        chunks = options[key]
        app.logger.info(f"Processing purchase for {key} GB using chunks: {chunks}")

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
                    msg = result.get('message', 'Unknown error')
                    app.logger.warning(f"[BUY-UPLOAD] Chunk {chunk} failed: {msg}")
                    errors.append(f"Failed on {chunk}: {msg}")
                    break # Stop on first failure
                    
            except Exception as e:
                app.logger.error(f"[BUY-UPLOAD] Exception on chunk {chunk}: {e}")
                errors.append(f"Error on {chunk}: {str(e)}")
                break

    # 4. Return result
    success = len(errors) == 0
    
    if total_purchased > 0 or (chunks == ['max'] and success):
        await push_mam_stats()
        
        msg = f"Purchased {total_purchased} GB successfully."
        if chunks == ['max']: msg = "Purchased Max upload credit."
        
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
        # Parse uploaded and downloaded (format: "123.45 GiB")
        def parse_size(size_str):
            if not size_str: return 0.0
            parts = size_str.split()
            if len(parts) != 2: return 0.0
            value = float(parts[0])
            unit = parts[1].upper()
            
            if 'TIB' in unit or 'TB' in unit: return value * 1024
            elif 'GIB' in unit or 'GB' in unit: return value
            elif 'MIB' in unit or 'MB' in unit: return value / 1024
            elif 'KIB' in unit or 'KB' in unit: return value / (1024 * 1024)
            return value
        
        uploaded_gb = parse_size(data.get('uploaded', '0 GiB'))
        downloaded_gb = parse_size(data.get('downloaded', '0 GiB'))
        ratio = float(data.get('ratio', 0))
        seedbonus = float(data.get('seedbonus', 0))
        
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
    # --------------------------------
    
    torrent_url = incoming_data.get('torrent_url') or incoming_data.get('url')
    author = incoming_data.get('author', 'Unknown')
    title = incoming_data.get('title', 'Unknown')
    id = incoming_data.get('id', '0')
    category = incoming_data.get('category', app.config.get("TORRENT_CLIENT_CATEGORY", ""))
    torrent_size_str = incoming_data.get('size', '0 GiB')  # e.g., "1.5 GiB"
    
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
                cost_per_gb = 500  # bonus points
                
                # Get valid amounts from UPLOAD_OPTIONS (all keys as floats, sorted)
                upload_options = app.config.get("UPLOAD_OPTIONS", {})
                valid_amounts = sorted([float(k) for k in upload_options.keys()])
                
                # Find smallest valid amount that covers the needed GB
                if valid_amounts:
                    recommended_amount = next((amt for amt in valid_amounts if amt >= needed_gb), valid_amounts[-1])
                else:
                    # Fallback if UPLOAD_OPTIONS not loaded
                    recommended_amount = max(1, needed_gb)
                
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
    
    # Check if MID is present - if so, skip hash calculation
    if id and id != '0' and app.config.get("AUTO_ORGANIZE_ON_ADD"):
        app.logger.info(f"MID {id} detected - adding torrent without hash calculation")
        
        # Add torrent immediately
        result = await torrent_client.add_torrent(torrent_url, category, mid=id)
        
        if result['status'] == 'success':
            # Extract additional metadata from incoming_data
            series_info = parse_series_info(incoming_data.get('series_info', ''))
            main_cat = incoming_data.get('main_cat', '')
            download_link = incoming_data.get('download_link', '')
            
            # Store in pending_mid_resolutions for later hash resolution
            pending_mid_resolutions[id] = {
                "added_at": time.time(),
                "metadata": {
                    "mid": id,
                    "author": author,
                    "title": title,
                    "added_on": datetime.now().isoformat(),
                    "status": "pending",
                    "retry_count": 0,
                    "series_info": series_info,
                    "category": get_category_name(main_cat),
                    "download_link": download_link,
                    "custom_relative_path": custom_relative_path
                }
            }
            app.logger.info(f"Added MID {id} to pending_mid_resolutions for hash resolution")
            start_monitoring_loop()
            
            return jsonify({'message': result['message']})
        else:
            return jsonify({'error': result.get('message', 'Unknown error')}), 400
    
    # Fallback: No MID or auto-organize disabled - use old hash-based approach
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
            metadata[hash_val] = {
                "mid": id, "author": author, "title": title,
                "added_on": datetime.now().isoformat(),
                "status": "pending", "retry_count": 0,
                "series_info": series_info,
                "category": get_category_name(main_cat),
                "download_link": download_link,
                "custom_relative_path": custom_relative_path
            }
            save_database(metadata)
            app.logger.info(f"Saved metadata for torrent hash: {hash_val}")
    
    result = await torrent_client.add_torrent(torrent_url, category)
    
    if result['status'] == 'success':
        # Start Monitoring
        if hash_val and app.config.get("AUTO_ORGANIZE_ON_ADD"):
            monitoring_state[hash_val] = {
                "added_at": time.time()
            }
            start_monitoring_loop()
            app.logger.info(f"Registered {hash_val} for active monitoring.")

        response_data = {'message': result['message']}
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
        
        # MID not found in client
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
        return await render_template("partials/results.html", error_message="Login failed")
    query = request.args.get("query", "")
    if not query: 
        return await render_template("partials/results.html", results=[])

    params = {
        "tor[text]": query,
        "tor[sortType]": "default", "perpage": 50, "thumbnail": "true", "dlLink": "true",
        "tor[browse_lang][]": language_dict.get(request.args.get("language", "English"), 1),
        "tor[srchIn][title]": "on" if request.args.get("search_in_title") else "off",
        "tor[srchIn][author]": "on" if request.args.get("search_in_author") else "off",
        "tor[srchIn][narrator]": "on" if request.args.get("search_in_narrator") else "off",
        "tor[srchIn][series]": "on" if request.args.get("search_in_series") else "off",
        "tor[searchType]": request.args.get("searchType", "all"),
        "isbn": "true", "description": "true", "mediaInfo": "true"
    }
    if (media_type := request.args.get("media_type", "13")) != "all":
        params["tor[main_cat][]"] = media_type

    headers = {"Cookie": "; ".join([f"{k}={v}" for k, v in mam_session_cookies.items()])}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{app.config['MAM_API_URL']}/tor/js/loadSearchJSONbasic.php", params=params, headers=headers)
            update_cookies(response)
            response.raise_for_status()
            json_data = response.json()
            results = json_data.get("data", [])
            base_dl_url = f"{app.config['MAM_API_URL']}/tor/download.php/"
            for item in results:
                if dl_hash := item.get('dl'): item['download_link'] = base_dl_url + dl_hash
                else: item['download_link'] = '' 

                # Only set if thumbnail is missing
                if not item.get('thumbnail'):
                    # Optimistically assume the CDN link works to save time
                    # You can add logic here: if item['id'] is missing, go straight to category
                    if item.get('id'):
                        item['thumbnail'] = f"https://cdn.myanonamouse.net/t/p/small/{item['id']}.webp"
                    else:
                        cat = item.get('category', '')
                        item['thumbnail'] = f"https://static.myanonamouse.net/pic/cats/3/{cat}.png"

            ranked = rank_results(results)
            client_status_data = await torrent_client.get_status() if torrent_client else {"status": "error"}
            client_connected = client_status_data.get("status") == "success"
            categories = await torrent_client.get_categories() if client_connected else {}
            
            # Fetch torrents with metadata and build MID-to-hash mapping
            mid_to_hash = {}
            if client_connected and torrent_client:
                try:
                    all_torrents = await torrent_client.get_torrents_with_metadata()
                    for torrent in all_torrents:
                        comment = torrent.get('comment', '')
                        if comment:
                            # Parse MID from comment using regex: MID=(\d+)
                            mid_match = re.search(r'MID=(\d+)', comment)
                            if mid_match:
                                mid = mid_match.group(1)
                                torrent_hash = torrent.get('hash', '')
                                if torrent_hash:
                                    mid_to_hash[mid] = torrent_hash
                    app.logger.debug(f"Built MID-to-hash mapping with {len(mid_to_hash)} entries")
                except Exception as e:
                    app.logger.warning(f"Failed to fetch torrents with metadata: {e}")
            
            # Mark results as downloaded if MID matches
            for item in ranked:
                item_id = str(item.get('id', ''))
                if item_id in mid_to_hash:
                    item['my_snatched'] = 1
            
            # Add snatched results to database.json with 'unknown' status
            metadata = load_database()
            for item in ranked:
                if item.get('my_snatched') == 1:
                    item_id = str(item.get('id', ''))
                    torrent_hash = mid_to_hash.get(item_id)
                    
                    # Only add if hash is available and not already in database
                    if torrent_hash and torrent_hash not in metadata:
                        metadata[torrent_hash] = {
                            "mid": item_id,
                            "author": item.get('author_info', ''),
                            "title": item.get('title', ''),
                            "added_on": datetime.now().isoformat(),
                            "status": "unknown",
                            "retry_count": 0,
                            "series_info": parse_series_info(item.get('series_info', '')),
                            "category": get_category_name(item.get('main_cat', '')),
                            "download_link": item.get('download_link', '')
                        }
                        app.logger.info(f"Added snatched torrent to database: {item.get('title')} (MID: {item_id}, hash: {torrent_hash})")
            
            # Save updated metadata if any snatched torrents were added
            if any(item.get('my_snatched') == 1 for item in ranked):
                save_database(metadata)
            
            return await render_template("partials/results.html", results=ranked, CLIENT_STATUS="CONNECTED" if client_connected else "NOT CONNECTED", categories=categories, TORRENT_CLIENT_CATEGORY=app.config.get("TORRENT_CLIENT_CATEGORY", ""))
    except Exception as e:
        return await render_template("partials/results.html", error_message=f"Error: {e}")

@app.route("/")
async def index():
    # Determine display name dynamically from the class
    c_type = app.config.get("TORRENT_CLIENT_TYPE", "qbittorrent")
    display_name = get_client_display_name(c_type)
    
    # NEW: Get list of all registered clients
    available_clients = get_available_clients()

    return await render_template(
        "index.html", 
        CLIENT_DISPLAY_NAME=display_name,
        AVAILABLE_CLIENTS=available_clients, # Pass the list here
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
                max_size_bytes = app.config.get("THUMBNAIL_CACHE_MAX_SIZE_MB", 500) * 1024 * 1024
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
                        app.logger.info(f"[CACHE-CLEANUP] Deleted {files_deleted_size} oldest files to enforce {app.config.get('THUMBNAIL_CACHE_MAX_SIZE_MB', 500)}MB size limit (freed {(sum(f['size'] for f in file_stats[:files_deleted_size]) / 1024 / 1024):.2f} MB)")
                            
        except Exception as e:
            app.logger.error(f"Error during cache cleanup: {e}")
        
        # Sleep for 24 hours before checking again
        await asyncio.sleep(86400)

@app.route('/system/public_ip')
async def get_public_ip():
    """
    Fetches the backend's public IP address.
    """
    
    try:
        # We use httpx instead of os.system('curl') because it is async,
        # non-blocking, and works reliably in serverless environments.
        async with httpx.AsyncClient() as client:
            # Fetch IPv4 address
            response = await client.get('https://ifconfig.me/ip', timeout=5.0)
            return jsonify({'ip': response.text.strip()})
    except Exception as e:
        app.logger.error(f"Failed to fetch public IP: {e}")
        return jsonify({'error': 'Could not fetch IP'}), 500
    
FETCH_SEMAPHORE = asyncio.Semaphore(200)

@app.route("/proxy_thumbnail")
async def proxy_thumbnail():
    url = request.args.get("url")
    if not url or UPSTREAM_CLIENT is None: return "Error", 400
    
    cache_enabled = app.config.get("ENABLE_FILESYSTEM_THUMBNAIL_CACHE", True)
    
    # --- Cache Read ---
    cache_key = hashlib.md5(url.encode()).hexdigest()
    cache_path = os.path.join(THUMB_CACHE_DIR, cache_key)
    
    if cache_enabled:
        # Ensure cache directory exists only when caching is enabled
        os.makedirs(THUMB_CACHE_DIR, exist_ok=True)
        
        if os.path.exists(cache_path):
            # Check if younger than 30 days (2592000 seconds)
            if time.time() - os.path.getmtime(cache_path) < 2592000:
                response = await send_file(cache_path)
                response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                response.headers["X-mousesearch-Cache-Status"] = "HIT"
                return response
            
    # --- Upstream Fetch ---
    fwd_headers = {h: request.headers.get(h) for h in ("If-None-Match", "If-Modified-Since", "Range") if request.headers.get(h)}
    async with FETCH_SEMAPHORE:
        req = UPSTREAM_CLIENT.build_request("GET", url, headers=fwd_headers, cookies=mam_session_cookies)
        r = await UPSTREAM_CLIENT.send(req, stream=True)
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
                    # Atomic move: prevents partial files if the script crashes mid-download
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

@app.route("/update_settings", methods=["POST"])
async def update_settings():
    form = await request.form
    config_to_update = app.config.copy()
    boolean_fields = {"AUTO_ORGANIZE_ON_ADD", "AUTO_ORGANIZE_ON_SCHEDULE", "ENABLE_DYNAMIC_IP_UPDATE", "AUTO_BUY_VIP", "AUTO_BUY_UPLOAD_ON_RATIO", "AUTO_BUY_UPLOAD_ON_BUFFER", "BLOCK_DOWNLOAD_ON_LOW_BUFFER"}
    for key in FALLBACK_CONFIG.keys():
        if key in boolean_fields: config_to_update[key] = key in form
        elif key in form: config_to_update[key] = form[key]
    if form.get("TORRENT_CLIENT_PASSWORD"): config_to_update["TORRENT_CLIENT_PASSWORD"] = form.get("TORRENT_CLIENT_PASSWORD")
    save_config(config_to_update)
    await load_new_app_config()
    if app.config.get("ENABLE_DYNAMIC_IP_UPDATE"):
        scheduler.add_job(id='manual_ip_update_job', func=force_update_ip, trigger='date', run_date=datetime.now() + timedelta(seconds=2))
    
    # Update VIP auto-buy scheduler based on new settings
    if app.config.get("AUTO_BUY_VIP"):
        interval_hours = int(app.config.get("AUTO_BUY_VIP_INTERVAL_HOURS", 24))
        scheduler.add_job(auto_buy_vip, 'interval', hours=interval_hours, id='vip_buy_job', replace_existing=True)
    else:
        # Remove the job if disabled
        try:
            scheduler.remove_job('vip_buy_job')
        except:
            pass
    
    # Update upload credit auto-buy scheduler based on new settings
    if app.config.get("AUTO_BUY_UPLOAD_ON_RATIO") or app.config.get("AUTO_BUY_UPLOAD_ON_BUFFER"):
        interval_hours = int(app.config.get("AUTO_BUY_UPLOAD_CHECK_INTERVAL_HOURS", 6))
        scheduler.add_job(check_and_buy_upload, 'interval', hours=interval_hours, id='upload_check_job', replace_existing=True)
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
    
    content_path = Path(TORRENT_DOWNLOAD_PATH) / info.get('name')
    organized_path = Path(ORGANIZED_PATH)
    torrent_meta = metadata[hash_val]
    
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
            # NO FILTERING: Link everything found in the torrent
            rel_path = source_file.relative_to(base_path)
            dest_file = dest_path / rel_path
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            if dest_file.exists(): 
                files_exist += 1
                app.logger.debug(f"[ORGANIZE] Exists: {dest_file}")
            else:
                try: 
                    os.link(source_file, dest_file)
                    files_linked += 1
                    app.logger.debug(f"[ORGANIZE] Linked: {source_file} -> {dest_file}")
                except Exception as e:
                    app.logger.error(f"[ORGANIZE] Link error {source_file}: {e}")

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
    return True, details

@app.route('/events')
async def events():
    """Server-Sent Events endpoint with heartbeat to prevent timeouts."""
    queue = asyncio.Queue()
    connected_websockets.add(queue)

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

if app.config.get("AUTO_ORGANIZE_ON_SCHEDULE"):
    hours = int(app.config.get("AUTO_ORGANIZE_INTERVAL_HOURS", 1))
    scheduler.add_job(check_for_unorganized_torrents, 'interval', hours=hours, id='organize_safety_net_job', replace_existing=True)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", default=None, type=int)
    args = parser.parse_args()
    
    # Priority: CLI arg > PORT env var > hardcoded default (5000)
    port = args.port or int(os.getenv("PORT", 5000))
    
    app.run(host=args.host, port=port, debug=True, use_reloader=False)