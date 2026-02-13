# hashing.py - Torrent hash calculation utilities
import hashlib
import bencodepy
import httpx


def calculate_torrent_hash_from_bytes(torrent_bytes: bytes) -> str | None:
    """
    Calculates a torrent info hash from raw .torrent bytes.
    """
    try:
        torrent_data = bencodepy.decode(torrent_bytes)
        if b'info' not in torrent_data:
            return None
        bencoded_info = bencodepy.encode(torrent_data[b'info'])
        return hashlib.sha1(bencoded_info).hexdigest()
    except Exception:
        return None


async def calculate_torrent_hash_from_url(url: str) -> str | None:
    """
    Downloads a .torrent file from the given URL and calculates its info hash.
    
    Args:
        url: The URL to download the .torrent file from
        
    Returns:
        The SHA1 hash of the torrent's info dictionary, or None if failed
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=10)
            response.raise_for_status()
            return calculate_torrent_hash_from_bytes(response.content)
    except Exception as e:
        # Note: We can't use app.logger here since this is a separate module
        # The calling code should handle logging
        return None
