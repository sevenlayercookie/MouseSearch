import httpx
from httpx import RequestError, HTTPStatusError
from .base import TorrentClient

class QBittorrentClient(TorrentClient):
    def __init__(self, config):
        super().__init__(config)
        self.base_url = config.get("TORRENT_CLIENT_URL")
        self.username = config.get("TORRENT_CLIENT_USERNAME")
        self.password = config.get("TORRENT_CLIENT_PASSWORD")

    @property
    def display_name(self) -> str:
        return "qBittorrent"

    async def login(self) -> bool:
        """Authenticates with qBittorrent and stores session cookies."""
        if not all([self.base_url, self.username, self.password]):
            return False
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.base_url}/api/v2/auth/login",
                    data={'username': self.username, 'password': self.password},
                )
                if "Ok" in response.text:
                    self.session_cookies = dict(response.cookies)
                    return True
        except (RequestError, HTTPStatusError):
            pass
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
        try:
            async with httpx.AsyncClient(cookies=self.session_cookies) as client:
                response = await client.get(f"{self.base_url}/api/v2/app/version")
                
                # If 403/401, try re-login
                if response.status_code in [401, 403]:
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
        payload = {'urls': torrent_url, 'category': category}
        # qBittorrent v4.1+ requires a dummy Referer header to prevent CSRF errors
        request_headers = {'Referer': self.base_url}
        
        # Note: kwargs handles 'mid' gracefully by ignoring it
        
        try:
            async with httpx.AsyncClient(cookies=self.session_cookies) as client:
                response = await client.post(
                    f"{self.base_url}/api/v2/torrents/add",
                    data=payload,
                    headers=request_headers
                )
                response.raise_for_status()
                if "Ok." in response.text:
                    return {'status': 'success', 'message': 'Torrent added successfully'}
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
                    return data[0]  # qB returns a list
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
                torrents_by_hash = {t['hash']: t for t in torrent_list}
                return {'torrents': torrents_by_hash}
        except (RequestError, HTTPStatusError) as e:
            return {'error': f'Failed to fetch batch torrent info: {e}'}

    async def get_api_version(self) -> str:
        return "v2"

    async def get_torrents_with_metadata(self) -> list:
        try:
            async with httpx.AsyncClient(cookies=self.session_cookies) as client:
                response = await client.get(f"{self.base_url}/api/v2/torrents/info")
                response.raise_for_status()
                return response.json()
        except (RequestError, HTTPStatusError):
            return []