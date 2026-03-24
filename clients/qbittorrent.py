import logging
import os
from urllib.parse import urlsplit, urlunsplit

import httpx
from httpx import RequestError, HTTPStatusError
from .base import TorrentClient
from hashing import calculate_torrent_hash_from_bytes


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

class QBittorrentClient(TorrentClient):
    def __init__(self, config):
        super().__init__(config)
        self.base_url = config.get("TORRENT_CLIENT_URL")
        self.username = config.get("TORRENT_CLIENT_USERNAME")
        self.password = config.get("TORRENT_CLIENT_PASSWORD")
        self.force_start_on_add = str(os.getenv("QB_FORCE_START", "false")).strip().lower() in {
            "1", "true", "t", "yes", "y", "on"
        }

    @property
    def display_name(self) -> str:
        return "qBittorrent"

    async def login(self) -> bool:
        """Authenticates with qBittorrent and stores session cookies."""
        if not all([self.base_url, self.username, self.password]):
            logger.warning(
                "qBittorrent login skipped due to missing configuration: url=%s username_present=%s password_present=%s",
                _sanitize_base_url(self.base_url),
                bool(self.username),
                bool(self.password),
            )
            return False

        sanitized_url = _sanitize_base_url(self.base_url)
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/api/v2/auth/login",
                    data={'username': self.username, 'password': self.password},
                )
                if "Ok" in response.text:
                    self.session_cookies = dict(response.cookies)
                    return True

                logger.warning(
                    "qBittorrent login rejected: url=%s status=%s response=%r",
                    sanitized_url,
                    response.status_code,
                    response.text[:200],
                )
        except HTTPStatusError as exc:
            status_code = exc.response.status_code if exc.response is not None else "unknown"
            logger.error(
                "qBittorrent login HTTP error: url=%s status=%s error=%s",
                sanitized_url,
                status_code,
                str(exc),
            )
        except RequestError as exc:
            logger.error(
                "qBittorrent login request error: url=%s status=%s error=%s",
                sanitized_url,
                "n/a",
                str(exc),
            )
        except Exception as exc:
            logger.error(
                "qBittorrent login unexpected error: url=%s status=%s error=%s",
                sanitized_url,
                "n/a",
                str(exc),
            )
        return False

    async def get_files(self, hash_val: str) -> list:
        """Returns the file list for a specific torrent hash."""
        try:
            async with httpx.AsyncClient(cookies=self.session_cookies) as client:
                response = await client.get(
                    f"{self.base_url}/api/v2/torrents/files",
                    params={'hash': hash_val}
                )
                response.raise_for_status()
                return response.json()
        except (RequestError, HTTPStatusError) as e:
            return []

    async def get_status(self) -> dict:
        """Returns connection status and version info."""
        sanitized_url = _sanitize_base_url(self.base_url)
        try:
            async with httpx.AsyncClient(cookies=self.session_cookies) as client:
                response = await client.get(f"{self.base_url}/api/v2/app/version")
                
                # If 403/401, try re-login
                if response.status_code in [401, 403]:
                    logger.warning(
                        "qBittorrent status check unauthorized: url=%s status=%s",
                        sanitized_url,
                        response.status_code,
                    )
                    if await self.login():
                        return await self.get_status()  # Retry once
                    else:
                        return {
                            "status": "error", 
                            "message": "Authentication failed",
                            "display_name": self.display_name
                        }
                
                response.raise_for_status()
                return {
                    "status": "success",
                    "message": f"{self.display_name} is connected.",
                    "version": response.text,
                    "display_name": self.display_name
                }
        # FIX: Catch both RequestError (Network down) AND HTTPStatusError (502/500/404)
        except (RequestError, HTTPStatusError, Exception) as e:
            status_code = e.response.status_code if isinstance(e, HTTPStatusError) and e.response is not None else "n/a"
            logger.error(
                "qBittorrent status check failed: url=%s status=%s error=%s",
                sanitized_url,
                status_code,
                str(e),
            )
            return {
                "status": "error", 
                "message": f"Failed to connect: {e}",
                "display_name": self.display_name
            }

    async def get_categories(self) -> dict:
        """Returns dict of categories from qBittorrent."""
        try:
            async with httpx.AsyncClient(cookies=self.session_cookies) as client:
                response = await client.get(f"{self.base_url}/api/v2/torrents/categories")
                return response.json() if response.status_code == 200 else {}
        except (RequestError, HTTPStatusError):
            return {}

    async def add_torrent(self, torrent_url: str, category: str, is_auto_organize: bool = False, **kwargs) -> dict:
        """Adds a torrent to qBittorrent."""
        torrent_data = kwargs.get("torrent_data")
        torrent_filename = kwargs.get("torrent_filename") or "download.torrent"
        added_hash = calculate_torrent_hash_from_bytes(torrent_data) if torrent_data is not None else None

        payload = {}
        if category:
            payload["category"] = category

        # qBittorrent v4.1+ requires a dummy Referer header to prevent CSRF errors
        request_headers = {'Referer': self.base_url}
        
        # Note: kwargs handles 'mid' gracefully by ignoring it
        
        try:
            async with httpx.AsyncClient(cookies=self.session_cookies) as client:
                if torrent_data is not None:
                    files = {
                        "torrents": (torrent_filename, torrent_data, "application/x-bittorrent")
                    }
                    response = await client.post(
                        f"{self.base_url}/api/v2/torrents/add",
                        data=payload,
                        files=files,
                        headers=request_headers
                    )
                else:
                    payload['urls'] = torrent_url
                    response = await client.post(
                        f"{self.base_url}/api/v2/torrents/add",
                        data=payload,
                        headers=request_headers
                    )

                if response.status_code == 415:
                    return {'status': 'error', 'message': 'Invalid torrent file (HTTP 415 from qBittorrent)'}
                response.raise_for_status()
                if "Ok." in response.text or response.text.strip() == "":
                    message = 'Torrent added successfully'
                    if self.force_start_on_add and added_hash:
                        force_start_response = await client.post(
                            f"{self.base_url}/api/v2/torrents/setForceStart",
                            data={"hashes": added_hash, "value": "true"},
                            headers=request_headers
                        )
                        force_start_response.raise_for_status()
                    elif self.force_start_on_add and not added_hash:
                        message = 'Torrent added successfully (force start skipped: hash unavailable)'

                    result = {'status': 'success', 'message': message}
                    if added_hash:
                        result['hash'] = added_hash
                    return result
                return {'status': 'error', 'message': response.text or 'Unknown error'}
        except (RequestError, HTTPStatusError) as e:
            return {'status': 'error', 'message': f'Failed to communicate with qBittorrent: {e}'}

    async def get_torrent_info(self, hash_val: str) -> dict:
        """Returns specific torrent info (name, save_path, etc)."""
        try:
            async with httpx.AsyncClient(cookies=self.session_cookies) as client:
                response = await client.get(
                    f"{self.base_url}/api/v2/torrents/info",
                    params={'hashes': hash_val},
                )
                response.raise_for_status()
                data = response.json()
                if data:
                    return self.normalize_torrent_info(data[0])  # qB returns a list
                return None
        except (RequestError, HTTPStatusError):
            return None

    async def get_torrent_info_batch(self, hash_list: list) -> dict:
        """Returns info for multiple torrents (qBittorrent-specific extension)."""
        try:
            hashes_param = '|'.join(hash_list)
            async with httpx.AsyncClient(cookies=self.session_cookies) as client:
                response = await client.get(
                    f"{self.base_url}/api/v2/torrents/info",
                    params={'hashes': hashes_param},
                )
                response.raise_for_status()
                torrent_list = response.json()
                # Return dict indexed by hash for easy lookup
                torrents_by_hash = {t['hash']: t for t in torrent_list if t.get('hash')}
                return {'torrents': self.normalize_torrent_info_map(torrents_by_hash)}
        except (RequestError, HTTPStatusError) as e:
            return {'error': f'Failed to fetch batch torrent info: {e}'}

    async def get_api_version(self) -> str:
        return "v2"

    async def get_torrents_with_metadata(self) -> list:
        try:
            async with httpx.AsyncClient(cookies=self.session_cookies) as client:
                response = await client.get(f"{self.base_url}/api/v2/torrents/info")
                response.raise_for_status()
                return self.normalize_torrent_metadata_list(response.json())
        except (RequestError, HTTPStatusError):
            return []
