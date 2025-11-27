# clients/__init__.py
from .qbittorrent import QBittorrentClient

# Registry mapping config strings to Client Classes
CLIENT_MAP = {
    "qbittorrent": QBittorrentClient,
    # Future: "transmission": TransmissionClient
}

def get_torrent_client(config):
    """
    Factory function to create the appropriate torrent client instance.
    """
    client_type = config.get("TORRENT_CLIENT_TYPE", "qbittorrent").lower()
    
    client_class = CLIENT_MAP.get(client_type)
    if client_class:
        return client_class(config)
        
    raise ValueError(f"Unsupported torrent client type: {client_type}")

def get_client_display_name(client_type):
    """
    Retrieves the display name defined in the client class itself.
    """
    if not client_type:
        client_type = "qbittorrent"
        
    client_class = CLIENT_MAP.get(client_type.lower())
    if client_class:
        # Instantiate with empty config just to access the property.
        # This relies on the client __init__ being lightweight (no network calls).
        try:
            return client_class({}).display_name
        except Exception:
            pass # Fallback if something goes wrong
            
    # Fallback to title case if class not found
    return client_type.title()