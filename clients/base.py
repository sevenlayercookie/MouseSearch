# clients/base.py
from abc import ABC, abstractmethod

class TorrentClient(ABC):
    def __init__(self, config):
        self.config = config
        self.session_cookies = {}

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