import logging
from urllib.parse import urlsplit, urlunsplit

import httpx
from httpx import HTTPStatusError, RequestError
import json
from .base import TorrentClient


logger = logging.getLogger(__name__)


def _sanitize_base_url(url: str | None) -> str:
    raw_url = str(url or "").strip()
    if not raw_url:
        return "<missing>"

    try:
        parsed = urlsplit(raw_url)
    except ValueError:
        return "<invalid-url>"

    hostname = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    if parsed.username or parsed.password:
        netloc = f"<redacted>@{hostname}{port}" if hostname else "<redacted>"
    else:
        netloc = parsed.netloc or hostname

    sanitized = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    return sanitized or raw_url

class DelugeClient(TorrentClient):
    """
    Client for interacting with the Deluge Web API (JSON-RPC).
    """
    def __init__(self, config):
        super().__init__(config)
        raw_url = config.get("TORRENT_CLIENT_URL", "http://localhost:8112")
        
        # Ensure URL ends with /json
        if not raw_url.strip().endswith("/json"):
            self.base_url = f"{raw_url.rstrip('/')}/json"
        else:
            self.base_url = raw_url

        self.password = config.get("TORRENT_CLIENT_PASSWORD")
        self.session_cookies = {}
        self._request_id = 0
        self._last_error_message = ""

    @property
    def display_name(self) -> str:
        return "Deluge"

    def _get_id(self):
        self._request_id += 1
        return self._request_id

    def _set_last_error(self, message: str | None):
        self._last_error_message = str(message or "").strip()

    def _clear_last_error(self):
        self._last_error_message = ""

    async def _request(self, method: str, params: list = None):
        """Internal helper for Deluge JSON-RPC."""
        if params is None:
            params = []

        sanitized_url = _sanitize_base_url(self.base_url)

        payload = {
            "method": method,
            "params": params,
            "id": self._get_id()
        }

        # Deluge specifically checks for this header to allow non-browser clients
        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'X-Deluge-Web-Client': 'MouseSearch'
        }

        try:
            # verify=False is useful if you ever switch to HTTPS with self-signed certs
            async with httpx.AsyncClient(cookies=self.session_cookies, timeout=15.0, verify=False) as client:
                response = await client.post(
                    self.base_url,
                    json=payload,
                    headers=headers
                )
                
                # IMPORTANT: Deluge rotates sessions, we must capture cookies on every request
                if response.cookies:
                    self.session_cookies.update(response.cookies)

                response.raise_for_status()
                
                try:
                    response_json = response.json()
                except json.JSONDecodeError:
                    # Fallback if the server sends a bad response
                    logger.error(
                        "Deluge RPC invalid JSON response: url=%s method=%s status=%s error=%s",
                        sanitized_url,
                        method,
                        response.status_code,
                        response.text[:200],
                    )
                    raise Exception(f"Invalid JSON response from Deluge: {response.text}")

                if response_json.get("error") is not None:
                    error_msg = response_json["error"].get("message", "Unknown Deluge Error")
                    # If session expired, clear cookies so next login attempt works
                    if "session" in str(error_msg).lower() or "not authenticated" in str(error_msg).lower():
                        self.session_cookies = {}
                    logger.warning(
                        "Deluge RPC API error: url=%s method=%s status=%s error=%s",
                        sanitized_url,
                        method,
                        response.status_code,
                        str(error_msg),
                    )
                    self._set_last_error(f"Deluge API Error: {error_msg}")
                    raise Exception(f"Deluge API Error: {error_msg}")

                self._clear_last_error()
                return response_json.get("result")

        except HTTPStatusError as e:
            status_code = e.response.status_code if e.response is not None else "unknown"
            self._set_last_error(f"HTTP error communicating with Deluge: {e}")
            logger.error(
                "Deluge RPC HTTP error: url=%s method=%s status=%s error=%s",
                sanitized_url,
                method,
                status_code,
                str(e),
            )
            raise Exception(f"HTTP error communicating with Deluge: {e}")
        except RequestError as e:
            self._set_last_error(f"Network error communicating with Deluge: {e}")
            logger.error(
                "Deluge RPC request error: url=%s method=%s status=%s error=%s",
                sanitized_url,
                method,
                "n/a",
                str(e),
            )
            raise Exception(f"Network error communicating with Deluge: {e}")
        except Exception as e:
            if not self._last_error_message:
                self._set_last_error(str(e))
            logger.error(
                "Deluge RPC unexpected error: url=%s method=%s status=%s error=%s",
                sanitized_url,
                method,
                "n/a",
                str(e),
            )
            raise

    async def _ensure_daemon_connection(self):
        """
        The Deluge WebUI is separate from the Daemon. 
        We must ensure the WebUI is actually connected to a daemon node.
        """
        try:
            connected = await self._request("web.connected")
            if not connected:
                # Get available hosts
                hosts = await self._request("web.get_hosts")
                if not hosts:
                    return # No hosts to connect to
                
                # host structure: [id, ip, port, status]
                # We prioritize Online, but try first available as fallback
                target_host = hosts[0][0]
                await self._request("web.connect", [target_host])
        except Exception:
            pass # We swallow errors here and let the actual API call fail if connection didn't work

    async def login(self) -> bool:
        if not self.password:
            self._set_last_error("Deluge password is not configured")
            logger.warning(
                "Deluge login skipped due to missing password: url=%s password_present=%s",
                _sanitize_base_url(self.base_url),
                False,
            )
            return False

        try:
            # 1. Auth with WebUI
            is_authed = await self._request("auth.login", [self.password])
            if not is_authed:
                self._set_last_error("Authentication failed")
                logger.warning(
                    "Deluge login rejected: url=%s",
                    _sanitize_base_url(self.base_url),
                )
                return False
            
            # 2. Ensure WebUI is connected to the backend Daemon
            await self._ensure_daemon_connection()
            self._clear_last_error()
            return True
        except Exception as exc:
            if not self._last_error_message:
                self._set_last_error(str(exc))
            logger.warning(
                "Deluge login failed: url=%s error=%s",
                _sanitize_base_url(self.base_url),
                str(exc),
            )
            return False

    async def get_status(self) -> dict:
        try:
            # Implicit check: if we have no cookies, we aren't logged in
            if not self.session_cookies:
                if not await self.login():
                    return {
                        "status": "error", 
                        "message": self._last_error_message or "Authentication failed",
                        "display_name": self.display_name # <--- ADDED
                    }

            # Double check daemon connection
            connected = await self._request("web.connected")
            if not connected:
                await self._ensure_daemon_connection()
                if not await self._request("web.connected"):
                    return {
                        "status": "error", 
                        "message": "Deluge WebUI online, but daemon disconnected",
                        "display_name": self.display_name # <--- ADDED
                    }

            version = await self._request("daemon.get_version")
            self._clear_last_error()
            
            return {
                "status": "success",
                "message": "Deluge is connected.",
                "version": f"Deluge {version}",
                "display_name": self.display_name
            }
        except Exception as e:
            return {
                "status": "error", 
                "message": f"Connection failed: {e}",
                "display_name": self.display_name # <--- ADDED
            }

    async def get_categories(self) -> dict:
        try:
            plugins = await self._request("core.get_enabled_plugins")
            if "Label" in plugins:
                labels = await self._request("label.get_labels")
                return {l: {'name': l} for l in labels}
            return {}
        except Exception:
            return {}

    async def add_torrent(self, torrent_url: str, category: str, is_auto_organize: bool = False, **kwargs) -> dict:
        try:
            # Deluge options dict
            # We do NOT add 'label' here, because the plugin ignores it in this dict.
            options = {
                "add_paused": False,
            }

            # Handle MID (Metadata ID) -> Store in Comment field
            if kwargs.get('mid'):
                mid_val = kwargs['mid']
                options['comment'] = f"MID={mid_val}"

            # 1. Add Torrent
            torrent_hash = await self._request("core.add_torrent_url", [torrent_url, options])
            
            if torrent_hash:
                # 2. Set Category (Label) Explicitly
                if category:
                    await self._set_category(torrent_hash, category)

                return {'status': 'success', 'message': 'Torrent added successfully'}
            else:
                return {'status': 'error', 'message': 'Deluge failed to add torrent (Invalid URL or Magnet)'}

        except Exception as e:
            return {'status': 'error', 'message': f'Failed to add torrent: {e}'}

    async def _set_category(self, torrent_hash: str, category: str):
        """
        Helper to safely set the label. 
        It checks if the label exists first, creates it if missing, then applies it.
        """
        try:
            # Check enabled plugins to ensure Label plugin is running
            plugins = await self._request("core.get_enabled_plugins")
            if "Label" not in plugins:
                return # Plugin disabled, cannot set label

            # Get existing labels
            existing_labels = await self._request("label.get_labels")
            
            # Create label if it doesn't exist
            if category not in existing_labels:
                await self._request("label.add", [category])
            
            # Apply label to torrent
            await self._request("label.set_torrent", [torrent_hash, category])
        except Exception:
            # We swallow errors here so we don't report the whole add_torrent as failed 
            # just because the label couldn't be set.
            pass

    async def get_torrent_info(self, hash_val: str) -> dict:
        keys = ["name", "save_path", "total_size", "progress", "eta", "state", "label", "comment"]
        try:
            data = await self._request("core.get_torrent_status", [hash_val, keys])
            if not data: return None

            return self.normalize_torrent_info({
                'hash': hash_val,
                'name': data.get('name'),
                'save_path': data.get('save_path'),
                'total_size': data.get('total_size'),
                'comment': data.get('comment', ''),
                # FIX: Divide by 100 to normalize to 0.0-1.0 scale
                'progress': data.get('progress', 0) / 100,
                'eta': data.get('eta', -1),
                'state': self._map_state(data.get('state')),
                'category': data.get('label', ''),
            }, hash_val=hash_val)
        except Exception:
            return None

    async def get_torrent_info_batch(self, hash_list: list) -> dict:
        keys = ["hash", "name", "save_path", "total_size", "progress", "eta", "state", "label", "comment"]
        try:
            query_filter = {"hash": hash_list}
            data = await self._request("core.get_torrents_status", [query_filter, keys])
            
            result = {}
            for h, info in data.items():
                result[h] = self.normalize_torrent_info({
                    'hash': h,
                    'name': info.get('name'),
                    'save_path': info.get('save_path'),
                    'total_size': info.get('total_size'),
                    'comment': info.get('comment', ''),
                    # FIX: Divide by 100 here as well
                    'progress': info.get('progress', 0) / 100,
                    'eta': info.get('eta', -1),
                    'state': self._map_state(info.get('state')),
                    'category': info.get('label', ''),
                }, hash_val=h)
            return {'torrents': self.normalize_torrent_info_map(result)}
        except Exception as e:
            return {'error': str(e)}

    def _map_state(self, deluge_state: str) -> str:
        if not deluge_state: return "unknown"
        s = deluge_state.lower()
        if "checking" in s or "allocating" in s: return "checking"
        if "downloading" in s: return "downloading"
        if "seeding" in s: return "uploading"
        if "paused" in s: return "paused"
        if "queued" in s: return "queued"
        if "error" in s: return "error"
        return "unknown"

    async def get_files(self, hash_val: str) -> list:
        try:
            data = await self._request("core.get_torrent_status", [hash_val, ["files"]])
            files = data.get("files", [])
            return [{"name": f.get("path"), "size": f.get("size")} for f in files]
        except Exception:
            return []

    async def get_api_version(self) -> str:
        try:
            return await self._request("daemon.get_version")
        except:
            return "Unknown"

    async def get_torrents_with_metadata(self) -> list:
        try:
            keys = ["hash", "name", "comment", "save_path"]
            data = await self._request("core.get_torrents_status", [{}, keys])
            result = []
            for h, info in data.items():
                result.append({
                    'hash': h,
                    'name': info.get('name'),
                    'save_path': info.get('save_path'),
                    'comment': info.get('comment', ''),
                })
            return self.normalize_torrent_metadata_list(result)
        except Exception:
            return []
