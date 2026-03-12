# clients/transmission.py
import httpx
from httpx import RequestError
import json
import re
import posixpath
import base64
from pathlib import Path
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
        self._rpc_mode = "auto"  # auto | jsonrpc | legacy
        self._rpc_id_counter = 0
        self._session_download_dir_cache = None

    @property
    def display_name(self) -> str:
        return "Transmission"

    def _next_rpc_id(self) -> int:
        self._rpc_id_counter += 1
        return self._rpc_id_counter

    def _to_snake(self, value: str) -> str:
        if not isinstance(value, str):
            return value
        value = value.replace('-', '_')
        value = re.sub(r'(?<!^)(?=[A-Z])', '_', value).lower()
        return value

    def _modern_method_name(self, method: str) -> str:
        return self._to_snake(method)

    def _modernize_arguments(self, arguments: dict | None) -> dict | None:
        if not arguments:
            return arguments
        modernized = {}
        for key, value in arguments.items():
            normalized_key = self._to_snake(key)
            if normalized_key == "fields" and isinstance(value, list):
                modernized[normalized_key] = [self._to_snake(item) if isinstance(item, str) else item for item in value]
            else:
                modernized[normalized_key] = value
        return modernized

    def _build_request(self, method: str, arguments: dict = None, mode: str = "legacy") -> dict:
        if mode == "jsonrpc":
            payload = {
                "jsonrpc": "2.0",
                "method": self._modern_method_name(method),
                "id": self._next_rpc_id(),
            }
            if arguments is not None:
                payload["params"] = self._modernize_arguments(arguments)
            return payload

        payload = {"method": method}
        if arguments is not None:
            payload["arguments"] = arguments
        return payload

    def _normalize_response(self, rpc_response: dict, mode: str):
        if mode == "jsonrpc":
            if 'error' in rpc_response and rpc_response['error']:
                raise Exception(self._extract_rpc_error_message(rpc_response['error']))
            result = rpc_response.get('result', {})
            if isinstance(result, str):
                if result.strip().lower() == "success":
                    return {}
                raise Exception(result.strip())
            return result if isinstance(result, dict) else {}

        if 'error' in rpc_response and rpc_response['error']:
            raise Exception(self._extract_rpc_error_message(rpc_response['error']))

        result = rpc_response.get('result')
        if result != 'success':
            if isinstance(result, str) and result.strip():
                raise Exception(result.strip())
            raise Exception("Transmission RPC request failed")

        if 'arguments' in rpc_response and isinstance(rpc_response['arguments'], dict):
            return rpc_response['arguments']
        return {}

    def _get_any(self, data: dict, *keys, default=None):
        for key in keys:
            if key in data:
                return data[key]
        return default

    def _normalize_hash(self, value) -> str:
        return str(value or '').strip().lower()

    def _extract_rpc_error_message(self, error_payload) -> str:
        """Returns a concise, user-meaningful error message from RPC error payload."""
        if isinstance(error_payload, dict):
            data = error_payload.get('data') if isinstance(error_payload.get('data'), dict) else {}
            detailed = data.get('error_string')
            if isinstance(detailed, str) and detailed.strip():
                return detailed.strip()

            message = error_payload.get('message')
            code = error_payload.get('code')
            if isinstance(message, str) and message.strip():
                msg = message.strip()
                return f"{msg} (code {code})" if code is not None else msg

        return str(error_payload)

    def _is_method_not_found_message(self, value) -> bool:
        if not isinstance(value, str):
            return False
        normalized = value.strip().lower()
        return normalized in {
            "method not found",
            "method name not recognized",
            "invalid request",
        }

    def _extract_tracker_error(self, torrent_info: dict) -> str:
        """Extracts a user-meaningful tracker/session error string when available."""
        direct_error = self._get_any(torrent_info, 'error_string', 'errorString', default='')
        if isinstance(direct_error, str) and direct_error.strip():
            return direct_error.strip()

        tracker_stats = self._get_any(torrent_info, 'tracker_stats', 'trackerStats', default=[])
        if isinstance(tracker_stats, list):
            for entry in tracker_stats:
                if not isinstance(entry, dict):
                    continue
                result_text = self._get_any(entry, 'last_announce_result', 'lastAnnounceResult', default='')
                if isinstance(result_text, str):
                    cleaned = result_text.strip()
                    if cleaned and cleaned.lower() not in {"success", "succeeded"}:
                        return cleaned
        return ""

    def _safe_category_segment(self, category: str) -> str:
        segment = str(category or "").strip()
        if not segment:
            return ""
        segment = segment.strip("/")
        if not segment or segment in {".", ".."}:
            return ""
        if "/" in segment or "\\" in segment:
            return ""
        return segment

    async def _get_session_download_dir(self) -> str | None:
        if self._session_download_dir_cache:
            return self._session_download_dir_cache

        session_result = await self._rpc_request("session-get", {"fields": ["download_dir", "download-dir"]})
        default_dir = self._get_any(session_result, 'download_dir', 'download-dir')
        if default_dir:
            self._session_download_dir_cache = str(default_dir)
        return self._session_download_dir_cache

    def _map_download_dir_to_local(self, download_dir: str | None, session_download_dir: str | None) -> str | None:
        remote_dir = str(download_dir or "").strip()
        if not remote_dir:
            return None

        local_base = str(
            self.config.get("LOCAL_TORRENT_DOWNLOAD_PATH")
            or self.config.get("TORRENT_DOWNLOAD_PATH")
            or ""
        ).strip()
        if not local_base:
            return remote_dir

        remote_base = str(
            self.config.get("REMOTE_TORRENT_DOWNLOAD_PATH")
            or session_download_dir
            or ""
        ).strip()
        if not remote_base:
            return local_base

        normalized_remote = posixpath.normpath(remote_dir)
        normalized_base = posixpath.normpath(remote_base)

        rel_path = posixpath.relpath(normalized_remote, normalized_base)
        if rel_path in {".", ""}:
            return local_base
        if rel_path.startswith("../") or rel_path == "..":
            return remote_dir

        return str(Path(local_base) / Path(rel_path))

    def _map_content_name_for_local_base(
        self,
        torrent_name: str | None,
        download_dir: str | None,
        session_download_dir: str | None,
    ) -> str:
        return str(torrent_name or "").strip()

    async def _compute_download_dir(self, category: str) -> str | None:
        """Returns Transmission download directory for this add operation.

        - No category: default session download directory
        - Category selected: <default>/<category>
        """
        default_dir = await self._get_session_download_dir()
        if not default_dir:
            return None

        category_segment = self._safe_category_segment(category)
        if not category_segment:
            return default_dir

        return posixpath.join(default_dir.rstrip('/'), category_segment)

    async def _rpc_request(self, method: str, arguments: dict = None, _allow_fallback: bool = True):
        """Performs a JSON-RPC request, handling auth, CSRF, and response normalization."""
        headers = {'Content-Type': 'application/json'}
        if self.session_id:
            headers['X-Transmission-Session-Id'] = self.session_id

        # Use Basic Auth if credentials are provided
        auth = (self.username, self.password) if self.username or self.password else None

        mode = self._rpc_mode if self._rpc_mode in {"jsonrpc", "legacy"} else "legacy"
        request_body = self._build_request(method, arguments, mode=mode)
        
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
                rpc_response = response.json()

                if mode == "jsonrpc" and _allow_fallback and rpc_response.get('error', {}).get('code') == -32601:
                    self._rpc_mode = "legacy"
                    return await self._rpc_request(method, arguments, _allow_fallback=False)

                if mode == "jsonrpc" and _allow_fallback and self._is_method_not_found_message(rpc_response.get('result')):
                    self._rpc_mode = "legacy"
                    return await self._rpc_request(method, arguments, _allow_fallback=False)

                if mode == "legacy" and _allow_fallback:
                    error_payload = rpc_response.get('error') if isinstance(rpc_response, dict) else None
                    if isinstance(error_payload, dict) and error_payload.get('code') in {-32600, -32601}:
                        self._rpc_mode = "jsonrpc"
                        return await self._rpc_request(method, arguments, _allow_fallback=False)
                    if self._is_method_not_found_message(rpc_response.get('result')):
                        self._rpc_mode = "jsonrpc"
                        return await self._rpc_request(method, arguments, _allow_fallback=False)

                normalized = self._normalize_response(rpc_response, mode)
                self._rpc_mode = mode
                return normalized

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
            data = await self._rpc_request("session-get", {"fields": ["version", "rpc-version-semver", "rpc_version_semver"]})
            version = self._get_any(data, 'version', default='Unknown')
            rpc_version_semver = self._get_any(data, 'rpc_version_semver', 'rpc-version-semver')
            
            return {
                "status": "success",
                "message": f"{self.display_name} is connected.",
                "version": version,
                "rpc_version_semver": rpc_version_semver,
                "rpc_mode": self._rpc_mode,
                "display_name": self.display_name
            }
        except Exception as e:
            return {
                "status": "error", 
                "message": f"Connection failed: {e}", 
                "rpc_mode": self._rpc_mode,
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
            default_dir = self._get_any(session_result, 'download_dir', 'download-dir', default='/downloads')
            
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
        torrent_data = kwargs.get("torrent_data")
        arguments = {
            'labels': [category] if category else []
        }
        if torrent_data is not None:
            arguments['metainfo'] = base64.b64encode(torrent_data).decode('ascii')
        else:
            arguments['filename'] = torrent_url
        
        # NOTE: Transmission doesn't support setting comments/tags specifically during add 
        # easily via RPC in all versions, so ignoring 'mid' is the safest path here.
        
        try:
            download_dir = await self._compute_download_dir(category)
            if download_dir:
                arguments['download-dir'] = download_dir

            result = await self._rpc_request("torrent-add", arguments)
            
            if 'torrent_added' in result or 'torrent-added' in result:
                added = result.get('torrent_added') or result.get('torrent-added') or {}
                name = added.get('name', 'Unknown')
                hash_value = self._normalize_hash(self._get_any(added, 'hashString', 'hash_string', default=''))
                return {'status': 'success', 'message': f'Torrent "{name}" added successfully', 'hash': hash_value}
            elif 'torrent_duplicate' in result or 'torrent-duplicate' in result:
                duplicate = result.get('torrent_duplicate') or result.get('torrent-duplicate') or {}
                name = duplicate.get('name', 'Unknown')
                hash_value = self._normalize_hash(self._get_any(duplicate, 'hashString', 'hash_string', default=''))
                return {
                    'status': 'error',
                    'message': f'Torrent client error (Transmission): Torrent "{name}" is already a duplicate',
                    'hash': hash_value,
                }
            else:
                return {'status': 'error', 'message': f'Torrent client error (Transmission): Unknown response: {result}'}

        except Exception as e:
            return {'status': 'error', 'message': f'Torrent client error (Transmission): {e}'}

    async def get_torrent_info(self, hash_val: str) -> dict:
        """Returns specific torrent info."""
        fields = [
            "hashString", "name", "downloadDir", "totalSize", "comment",
            "percentDone", "rateDownload", "rateUpload", "status",
            "errorString", "eta", "queuePosition", "trackerStats"
        ]
        
        try:
            session_download_dir = await self._get_session_download_dir()
            # Note: We do NOT send "format": "table" like the WebUI does.
            # By omitting it, we get the default "objects" format, which returns a nice dictionary.
            result = await self._rpc_request("torrent-get", {"ids": [hash_val], "fields": fields})
            
            torrents = result.get('torrents', [])
            if torrents:
                info = torrents[0]
                remote_download_dir = self._get_any(info, 'download_dir', 'downloadDir')
                mapped_save_path = self._map_download_dir_to_local(
                    remote_download_dir,
                    session_download_dir
                )
                mapped_name = self._map_content_name_for_local_base(
                    info.get('name'),
                    remote_download_dir,
                    session_download_dir,
                )
                return self.normalize_torrent_info({
                    'hash': self._normalize_hash(self._get_any(info, 'hash_string', 'hashString')),
                    'name': mapped_name,
                    'save_path': mapped_save_path,
                    'total_size': self._get_any(info, 'total_size', 'totalSize'),
                    'comment': info.get('comment'),
                    'progress': self._get_any(info, 'percent_done', 'percentDone', default=0),
                    'eta': info.get('eta', -1),
                    'state': self._map_status(info.get('status', 0)),
                    'tracker_error': self._extract_tracker_error(info),
                })
            return {}
        except Exception:
            return {}
    
    async def get_torrent_info_batch(self, hash_list: list) -> dict:
        """Optimized batch fetch for multiple torrents."""
        fields = [
            "hashString", "name", "downloadDir", "totalSize", "comment",
            "percentDone", "rateDownload", "rateUpload", "status",
            "errorString", "eta", "queuePosition", "trackerStats"
        ]
        
        try:
            session_download_dir = await self._get_session_download_dir()
            # Transmission accepts a list of hashes directly in 'ids'
            result = await self._rpc_request("torrent-get", {"ids": hash_list, "fields": fields})
            
            torrents = result.get('torrents', [])
            torrents_by_hash = {}
            
            for t in torrents:
                h = self._normalize_hash(self._get_any(t, 'hash_string', 'hashString'))
                if h:
                    mapped_save_path = self._map_download_dir_to_local(
                        self._get_any(t, 'download_dir', 'downloadDir'),
                        session_download_dir
                    )
                    torrents_by_hash[h] = self.normalize_torrent_info({
                        'hash': h,
                        'name': t.get('name'),
                        'save_path': mapped_save_path,
                        'total_size': self._get_any(t, 'total_size', 'totalSize'),
                        'comment': t.get('comment'),
                        'progress': self._get_any(t, 'percent_done', 'percentDone', default=0),
                        'eta': t.get('eta', -1),
                        'state': self._map_status(t.get('status', 0)),
                        'tracker_error': self._extract_tracker_error(t),
                    }, hash_val=h)
            
            return {'torrents': self.normalize_torrent_info_map(torrents_by_hash)}
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
            return self._get_any(result, "version", default="Unknown")
        except Exception:
            return "Unknown"

    async def get_torrents_with_metadata(self) -> list:
        fields = ["hashString", "name", "comment", "downloadDir", "totalSize"]
        try:
            session_download_dir = await self._get_session_download_dir()
            result = await self._rpc_request("torrent-get", {"fields": fields})
            torrents = result.get('torrents', [])
            mapped = []
            for t in torrents:
                mapped_save_path = self._map_download_dir_to_local(
                    self._get_any(t, 'download_dir', 'downloadDir'),
                    session_download_dir
                )
                mapped.append({
                    'hash': self._normalize_hash(self._get_any(t, 'hash_string', 'hashString')),
                    'name': t.get('name'),
                    'save_path': mapped_save_path,
                    'comment': t.get('comment', ''),
                })
            return self.normalize_torrent_metadata_list(mapped)
        except Exception:
            return []
