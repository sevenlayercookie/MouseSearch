# clients/transmission.py
import httpx
from httpx import RequestError
import json
from .base import TorrentClient

class TransmissionClient(TorrentClient):
    """
    Client for interacting with a Transmission RPC server.
    Supports both Legacy (v4.0.x) and JSON-RPC 2.0 (v4.1.0+) response formats.
    """
    def __init__(self, config):
        super().__init__(config)
        raw_url = config.get("TORRENT_CLIENT_URL", "http://localhost:9091/transmission/rpc")
        
        # Auto-fix URL if user forgot the endpoint path
        # Transmission ALWAYS needs /transmission/rpc at the end
        if not raw_url.strip().endswith("/transmission/rpc"):
            self.base_url = f"{raw_url.rstrip('/')}/transmission/rpc"
        else:
            self.base_url = raw_url
            
        self.username = config.get("TORRENT_CLIENT_USERNAME")
        self.password = config.get("TORRENT_CLIENT_PASSWORD")
        self.session_cookies = {}
        self.session_id = None
        self._rpc_id_counter = 0

    @property
    def display_name(self) -> str:
        return "Transmission"

    def _get_next_rpc_id(self):
        """Generates a unique ID for each RPC request."""
        self._rpc_id_counter += 1
        return self._rpc_id_counter

    def _build_request(self, method: str, arguments: dict = None) -> dict:
        """Constructs a JSON-RPC request payload."""
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "id": self._get_next_rpc_id()
        }
        if arguments is not None:
            # Transmission expects parameters in 'arguments', standard JSON-RPC in 'params'.
            # Fortunately, Transmission usually accepts 'arguments' in the input even in newer versions,
            # but we use 'arguments' here to match the legacy behavior which is strictest.
            payload["arguments"] = arguments
        return payload

    async def _rpc_request(self, method: str, arguments: dict = None):
        """Performs a JSON-RPC request, handling auth, CSRF, and response normalization."""
        headers = {'Content-Type': 'application/json'}
        if self.session_id:
            headers['X-Transmission-Session-Id'] = self.session_id

        # Use Basic Auth if credentials are provided
        auth = (self.username, self.password) if self.username or self.password else None

        request_body = self._build_request(method, arguments)
        
        try:
            async with httpx.AsyncClient(auth=auth, timeout=10.0) as client:
                response = await client.post(
                    self.base_url, 
                    content=json.dumps(request_body),
                    headers=headers
                )

                # Handle CSRF/Session ID renewal (409 Conflict)
                if response.status_code == 409:
                    self.session_id = response.headers.get('X-Transmission-Session-Id')
                    if self.session_id:
                        # Retry the request with the new session ID
                        headers['X-Transmission-Session-Id'] = self.session_id
                        response = await client.post(
                            self.base_url, 
                            content=json.dumps(request_body),
                            headers=headers
                        )
                    else:
                        response.raise_for_status() # Re-raise if no new ID in 409

                response.raise_for_status()
                
                # Check for RPC errors within the JSON response
                rpc_response = response.json()
                
                # --- RESPONSE NORMALIZATION FIX ---
                # Transmission 4.0.x returns data in 'arguments', and 'result' is just "success".
                # Transmission 4.1.x returns data in 'result'.
                
                # Check for application-level errors first
                if rpc_response.get('result') != 'success' and 'arguments' not in rpc_response and 'result' not in rpc_response:
                     # This catches weird edge cases or standard JSON-RPC errors
                     if 'error' in rpc_response:
                        raise Exception(f"RPC Error: {rpc_response['error']}")

                # Return the actual data dict
                if 'arguments' in rpc_response:
                    return rpc_response['arguments']
                
                return rpc_response.get('result', {})

        except RequestError as e:
            raise Exception(f"Network error communicating with Transmission: {e}")
        except Exception as e:
            # Catch generic exceptions
            raise e

    async def login(self) -> bool:
        """Implicit login via session-get."""
        try:
            await self._rpc_request("session-get", {"fields": ["version"]})
            return True
        except Exception:
            return False

    async def get_status(self) -> dict:
        """Returns connection status and version info."""
        try:
            data = await self._rpc_request("session-get", {"fields": ["version"]})
            version = data.get('version', 'Unknown')
            
            return {
                "status": "success",
                "message": f"{self.display_name} is connected.",
                "version": version,
                "display_name": self.display_name
            }
        except Exception as e:
            return {
                "status": "error", 
                "message": f"Connection failed: {e}", 
                "display_name": self.display_name # <--- ADDED
            }

    async def get_categories(self) -> dict:
        """
        Retrieves groups to act as categories.
        """
        try:
            # Transmission 4.0.x might not support group-get widely yet, handling graceful fallback
            try:
                result = await self._rpc_request("group-get")
                groups = result.get('group', []) # Transmission 4.0 returns 'group' list, not 'groups'
            except:
                groups = []
            
            categories = {
                g['name']: {'name': g['name'], 'savePath': None} 
                for g in groups if 'name' in g
            }

            # Add default download directory
            session_result = await self._rpc_request("session-get", {"fields": ["download-dir"]})
            default_dir = session_result.get('download-dir', '/downloads')
            
            if 'default' not in categories:
                categories['default'] = {'name': 'Default', 'savePath': default_dir}
            
            return categories
        except Exception:
            # Fallback if group-get fails entirely
            return {'default': {'name': 'Default', 'savePath': ''}}

    async def add_torrent(self, torrent_url: str, category: str, is_auto_organize: bool = False, **kwargs) -> dict:
        """
        Adds a torrent to Transmission.
        accepts **kwargs to gracefully handle 'mid' argument without crashing.
        """
        arguments = {
            'filename': torrent_url,
            'labels': [category] if category else []
        }
        
        # NOTE: Transmission doesn't support setting comments/tags specifically during add 
        # easily via RPC in all versions, so ignoring 'mid' is the safest path here.
        
        try:
            result = await self._rpc_request("torrent-add", arguments)
            
            if 'torrent-added' in result:
                name = result['torrent-added'].get('name', 'Unknown')
                return {'status': 'success', 'message': f'Torrent "{name}" added successfully'}
            elif 'torrent-duplicate' in result:
                name = result['torrent-duplicate'].get('name', 'Unknown')
                return {'status': 'error', 'message': f'Torrent "{name}" is already a duplicate'}
            else:
                return {'status': 'error', 'message': f'Unknown response: {result}'}

        except Exception as e:
            return {'status': 'error', 'message': f'Failed to add torrent: {e}'}

    async def get_torrent_info(self, hash_val: str) -> dict:
        """Returns specific torrent info."""
        # 1. We MUST explicitly ask for 'eta' and 'queuePosition'
        fields = [
            "hashString", "name", "downloadDir", "totalSize", "comment", 
            "percentDone", "rateDownload", "rateUpload", "status", 
            "errorString", "eta", "queuePosition"
        ]
        
        try:
            # Note: We do NOT send "format": "table" like the WebUI does.
            # By omitting it, we get the default "objects" format, which returns a nice dictionary.
            result = await self._rpc_request("torrent-get", {"ids": [hash_val], "fields": fields})
            
            torrents = result.get('torrents', [])
            if torrents:
                info = torrents[0]
                return {
                    'hash': info.get('hashString'),
                    'name': info.get('name'),
                    'save_path': info.get('downloadDir'),
                    'total_size': info.get('totalSize'),
                    'comment': info.get('comment'),
                    'progress': info.get('percentDone', 0),
                    # Now 'eta' will be present in the response dict
                    'eta': info.get('eta', -1), 
                    'state': self._map_status(info.get('status', 0)),
                }
            return {}
        except Exception:
            return {}
    
    async def get_torrent_info_batch(self, hash_list: list) -> dict:
        """Optimized batch fetch for multiple torrents."""
        fields = [
            "hashString", "name", "downloadDir", "totalSize", "comment", 
            "percentDone", "rateDownload", "rateUpload", "status", 
            "errorString", "eta", "queuePosition"
        ]
        
        try:
            # Transmission accepts a list of hashes directly in 'ids'
            result = await self._rpc_request("torrent-get", {"ids": hash_list, "fields": fields})
            
            torrents = result.get('torrents', [])
            torrents_by_hash = {}
            
            for t in torrents:
                h = t.get('hashString')
                if h:
                    torrents_by_hash[h] = {
                        'hash': h,
                        'name': t.get('name'),
                        'save_path': t.get('downloadDir'),
                        'total_size': t.get('totalSize'),
                        'comment': t.get('comment'),
                        'progress': t.get('percentDone', 0),
                        'eta': t.get('eta', -1),
                        'state': self._map_status(t.get('status', 0)),
                    }
            
            return {'torrents': torrents_by_hash}
        except Exception as e:
            return {'error': f'Batch fetch failed: {e}'}
            
    def _map_status(self, status_code: int) -> str:
        """Maps Transmission numeric status to human-readable string."""
        # 0: Stopped, 1: Check wait, 2: Check, 3: Download wait, 4: Download, 5: Seed wait, 6: Seed
        mapping = {
            0: "paused",
            1: "checking",
            2: "checking",
            3: "queued",
            4: "downloading",
            5: "queuedUP",
            6: "uploading"
        }
        return mapping.get(status_code, "unknown")

    async def get_files(self, hash_val: str) -> list:
        try:
            result = await self._rpc_request("torrent-get", {"ids": [hash_val], "fields": ["files"]})
            torrents = result.get('torrents', [])
            if torrents:
                return torrents[0].get('files', [])
            return []
        except Exception:
            return []

    async def get_api_version(self) -> str:
        try:
            result = await self._rpc_request("session-get", {"fields": ["version"]})
            return result.get("version", "Unknown")
        except Exception:
            return "Unknown"

    async def get_torrents_with_metadata(self) -> list:
        fields = ["hashString", "name", "comment", "downloadDir", "totalSize"]
        try:
            result = await self._rpc_request("torrent-get", {"fields": fields})
            torrents = result.get('torrents', [])
            mapped = []
            for t in torrents:
                mapped.append({
                    'hash': t.get('hashString'),
                    'name': t.get('name'),
                    'save_path': t.get('downloadDir'),
                    'comment': t.get('comment', ''),
                })
            return mapped
        except Exception:
            return []