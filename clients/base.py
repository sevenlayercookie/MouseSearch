# clients/base.py
from abc import ABC, abstractmethod
from typing import Any

class TorrentClient(ABC):
    def __init__(self, config):
        self.config = config
        self.session_cookies = {}

    def normalize_torrent_info(self, info: dict | None, *, hash_val: str | None = None) -> dict | None:
        if info is None:
            return None

        normalized = dict(info)
        normalized_hash = (
            normalized.get("hash")
            or normalized.get("hashString")
            or normalized.get("hash_string")
            or hash_val
            or ""
        )
        normalized_name = normalized.get("name") or normalized.get("base_filename") or ""
        normalized_save_path = (
            normalized.get("save_path")
            or normalized.get("downloadDir")
            or normalized.get("download_dir")
            or ""
        )
        normalized_total_size = (
            normalized.get("total_size")
            or normalized.get("totalSize")
            or normalized.get("size")
            or 0
        )
        normalized_comment = normalized.get("comment") or ""
        normalized_progress = normalized.get("progress")
        if normalized_progress is None:
            normalized_progress = normalized.get("percentDone")
        if normalized_progress is None:
            normalized_progress = 0
        normalized_eta = normalized.get("eta", -1)
        normalized_state = normalized.get("state") or "unknown"
        normalized_category = normalized.get("category") or normalized.get("label") or ""
        normalized_tracker_error = normalized.get("tracker_error") or normalized.get("errorString") or ""

        try:
            progress_value = float(normalized_progress)
        except (TypeError, ValueError):
            progress_value = 0.0
        if progress_value > 1:
            progress_value = progress_value / 100.0 if progress_value <= 100 else 1.0
        progress_value = max(0.0, min(progress_value, 1.0))

        try:
            total_size_value = int(float(normalized_total_size))
        except (TypeError, ValueError):
            total_size_value = 0
        if total_size_value < 0:
            total_size_value = 0

        try:
            eta_value = int(float(normalized_eta))
        except (TypeError, ValueError):
            eta_value = -1

        normalized["hash"] = str(normalized_hash).strip().lower()
        normalized["name"] = str(normalized_name or "").strip()
        normalized["save_path"] = str(normalized_save_path or "").strip()
        normalized["total_size"] = total_size_value
        normalized["comment"] = str(normalized_comment or "").strip()
        normalized["progress"] = progress_value
        normalized["eta"] = eta_value
        normalized["state"] = str(normalized_state or "unknown").strip() or "unknown"
        normalized["category"] = str(normalized_category or "").strip()
        normalized["tracker_error"] = str(normalized_tracker_error or "").strip()
        return normalized

    def normalize_torrent_info_map(self, torrents: dict[str, dict]) -> dict[str, dict]:
        normalized: dict[str, dict] = {}
        for hash_val, info in (torrents or {}).items():
            normalized_info = self.normalize_torrent_info(info, hash_val=hash_val)
            if normalized_info is None:
                continue
            if normalized_info.get("hash"):
                normalized[normalized_info["hash"]] = normalized_info
        return normalized

    def normalize_torrent_metadata_list(self, torrents: list[dict[str, Any]]) -> list[dict]:
        normalized: list[dict] = []
        for info in torrents or []:
            normalized_info = self.normalize_torrent_info(info)
            if normalized_info is None:
                continue
            normalized.append(normalized_info)
        return normalized

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Returns the user-friendly display name of the client."""
        pass

    @abstractmethod
    async def login(self) -> bool:
        """Authenticates with the torrent client."""
        pass

    @abstractmethod
    async def get_status(self) -> dict:
        """Returns {'status': 'connected'|'error', 'message': str}"""
        pass

    @abstractmethod
    async def get_categories(self) -> dict:
        """Returns a dict of categories."""
        pass

    @abstractmethod
    async def add_torrent(self, torrent_url: str, category: str, is_auto_organize: bool = False) -> dict:
        """Adds a torrent. Returns {'status': 'success'|'error', 'message': str}"""
        pass

    @abstractmethod
    async def get_torrent_info(self, hash_val: str) -> dict:
        """Returns specific torrent info (name, save_path, etc)."""
        pass
    
    @abstractmethod
    async def get_files(self, hash_val: str) -> list:
        """Returns the list of files for a specific torrent."""
        pass

    @abstractmethod
    async def get_api_version(self) -> str:
        """Returns version string of the client."""
        pass

    @abstractmethod
    async def get_torrents_with_metadata(self) -> list:
        """Returns list of all torrents with metadata including comment field."""
        pass
