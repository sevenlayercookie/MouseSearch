import httpx
import xml.etree.ElementTree as ET
from clients.base import TorrentClient
from pathlib import Path
from urllib.parse import unquote

class RTorrentClient(TorrentClient):
    display_name = "rTorrent"

    def __init__(self, config):
        super().__init__(config)
        # rTorrent typically runs behind Nginx/Apache at /RPC2
        self.url = config.get("TORRENT_CLIENT_URL", "http://localhost/RPC2")
        self.username = config.get("TORRENT_CLIENT_USERNAME", "")
        self.password = config.get("TORRENT_CLIENT_PASSWORD", "")
        
        # Standard ruTorrent label field is usually d.custom1
        self.label_attr = "d.custom1" 
        self.comment_attr = "d.custom2"

    async def _request(self, method: str, params: list = None):
        """
        Internal helper to construct XML-RPC requests manually 
        to ensure async compatibility with httpx.
        """
        if params is None:
            params = []

        # Build XML payload manually to avoid blocking xmlrpc libraries
        xml_params = ""
        for p in params:
            if isinstance(p, int):
                # i8 is safer for file sizes
                xml_params += f"<param><value><i8>{p}</i8></value></param>"
            elif isinstance(p, str):
                # Basic XML escaping
                safe_str = p.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                xml_params += f"<param><value><string>{safe_str}</string></value></param>"
            elif isinstance(p, float):
                xml_params += f"<param><value><double>{p}</double></value></param>"
        
        payload = f"""<?xml version='1.0'?>
<methodCall>
<methodName>{method}</methodName>
<params>{xml_params}</params>
</methodCall>"""

        headers = {"Content-Type": "text/xml"}
        auth = (self.username, self.password) if self.username else None

        try:
            # verify=False handles self-signed certs often found on seedboxes
            async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
                resp = await client.post(self.url, content=payload, headers=headers, auth=auth)
                resp.raise_for_status()
                return self._parse_xml_response(resp.text)
        except Exception as e:
            raise Exception(f"rTorrent connection failed: {e}")

    def _parse_xml_response(self, xml_str):
        """Parses the XML-RPC response."""
        try:
            # .strip() is crucial for some webservers that add newlines before <?xml>
            root = ET.fromstring(xml_str.strip())
            
            # Check for Faults
            fault = root.find(".//fault")
            if fault:
                err = fault.find(".//string")
                raise Exception(f"XML-RPC Fault: {err.text if err is not None else 'Unknown'}")
            
            # Recursive parser for params
            def parse_node(node):
                if node.find("string") is not None:
                    return node.find("string").text or ""
                if node.find("i8") is not None:
                    return int(node.find("i8").text)
                if node.find("i4") is not None:
                    return int(node.find("i4").text)
                if node.find("int") is not None:
                    return int(node.find("int").text)
                if node.find("double") is not None:
                    return float(node.find("double").text)
                if node.find("array") is not None:
                    data_node = node.find("array/data")
                    return [parse_node(v) for v in data_node.findall("value")]
                if node.find("struct") is not None:
                    data = {}
                    for member in node.findall("struct/member"):
                        key = member.find("name").text
                        val = parse_node(member.find("value"))
                        data[key] = val
                    return data
                return None

            # Success response is usually inside params -> param -> value
            param = root.find(".//params/param/value")
            if param is not None:
                return parse_node(param)
            return None
        except Exception as e:
            # Include snippet of raw response in error for debugging
            raise Exception(f"Failed to parse rTorrent response: {e} | Raw: {xml_str[:100]}...")

    # --- ABSTRACT METHODS IMPLEMENTATION ---

    async def get_api_version(self):
        """Required by abstract base class."""
        try:
            return await self._request("system.client_version")
        except:
            return "Unknown"

    async def get_files(self, torrent_hash):
        """
        Required by abstract base class.
        Returns list of files: [{'name': '...', 'size': 123}, ...]
        """
        try:
            # f.multicall: target, glob, cmd1, cmd2...
            # f.path gives relative path, f.size_bytes gives size
            files_data = await self._request("f.multicall", [torrent_hash, "", "f.path=", "f.size_bytes="])
            
            result = []
            for f in files_data:
                # f is a list [path, size]
                if len(f) >= 2:
                    result.append({
                        "name": f[0],
                        "size": f[1]
                    })
            return result
        except Exception:
            return []

    # --- STANDARD METHODS ---

    async def login(self):
        # Ping command
        await self._request("system.client_version")
        return True

    async def get_status(self):
        try:
            version = await self._request("system.client_version")
            return {
                "status": "success",
                "message": f"{self.display_name} is connected.",
                "version": f"rTorrent {version}",
                "display_name": self.display_name
            }
        except Exception as e:
            return {
                "status": "error", 
                "message": str(e),
                "display_name": self.display_name # <--- ADDED
            }

    async def get_categories(self):
        try:
            # Fetch all unique labels currently in use
            # d.multicall2 signature: target, view, cmd...
            labels = await self._request("d.multicall2", ["", "main", self.label_attr + "="])
            unique = set(r[0] for r in labels if r and r[0])
            return {l: {"name": l} for l in unique}
        except:
            return {}

    async def add_torrent(self, torrent_url: str, category: str = "", **kwargs):
        try:
            # load.start_verbose downloads the URL and starts it
            cmds = ["", torrent_url]
            
            # 1. Set Category (Label)
            if category:
                cmds.append(f'{self.label_attr}.set="{category}"')
            
            # 2. Set Comment (MID) if provided
            # This mimics what ruTorrent does. We store the MID in d.custom2
            # so we can retrieve it later via get_torrents_with_metadata.
            if kwargs.get("mid"):
                mid_val = kwargs["mid"]
                # Format strictly as MID=12345 so the app regex matches it
                comment = f"MID={mid_val}"
                
                # Optional: If you want ruTorrent to parse URLS, add VRS24mrker prefix, 
                # but plain text is safer for your app's regex.
                cmds.append(f'{self.comment_attr}.set="{comment}"')

            await self._request("load.start_verbose", cmds)
            return {"status": "success", "message": "Torrent added to rTorrent"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def get_torrent_info(self, torrent_hash: str):
        try:
            # Fetch specific fields
            # d.state (1=open/0=closed), d.is_active (1=started/0=stopped), d.complete (1=done)
            name = await self._request("d.name", [torrent_hash])
            directory = await self._request("d.directory", [torrent_hash])
            is_multi_file = await self._request("d.is_multi_file", [torrent_hash])
            down_rate = await self._request("d.down.rate", [torrent_hash])
            done = await self._request("d.completed_bytes", [torrent_hash])
            size = await self._request("d.size_bytes", [torrent_hash])
            label = await self._request(self.label_attr, [torrent_hash])
            comment = await self._request(self.comment_attr, [torrent_hash])
            
            is_open = await self._request("d.state", [torrent_hash]) 
            is_active = await self._request("d.is_active", [torrent_hash]) 
            is_hash_checking = await self._request("d.is_hash_checking", [torrent_hash])
            is_complete = await self._request("d.complete", [torrent_hash])

            return self._format_data(
                torrent_hash, name, directory, is_multi_file, down_rate, done, size, label, comment,
                is_open, is_active, is_hash_checking, is_complete
            )
        except:
            return None

    async def get_torrent_info_batch(self, hashes: list):
        if not hashes: return {"torrents": {}}
        try:
            data = await self._request(
                "d.multicall2",
                ["", "main"] + self._get_torrent_info_multicall_cmds()
            )

            result = {}
            target_hashes = {
                self._normalize_hash(raw_hash) for raw_hash in hashes if self._normalize_hash(raw_hash)
            }

            for row in data:
                hash_val, info = self._parse_torrent_info_multicall_row(row)
                if hash_val and info and hash_val in target_hashes:
                    result[hash_val] = info

            return {"torrents": self.normalize_torrent_info_map(result)}
        except:
            return {"torrents": {}}

    async def get_torrents_with_metadata(self):
        """
        Returns list of all torrents with metadata.
        Decodes URL-encoded comments common in ruTorrent (e.g., MID%3D123 -> MID=123).
        """
        try:
            data = await self._request(
                "d.multicall2",
                ["", "main", "d.hash=", "d.name=", "d.directory=", "d.is_multi_file=", self.label_attr + "=", self.comment_attr + "="]
            )
            
            results = []
            for r in data:
                raw_hash = self._normalize_hash(r[0] if len(r) > 0 else "")
                raw_name = r[1] if len(r) > 1 else ""
                raw_directory = r[2] if len(r) > 2 else ""
                raw_is_multi_file = r[3] if len(r) > 3 else 0
                raw_category = r[4] if len(r) > 4 else ""
                raw_comment = r[5] if len(r) > 5 else ""
                
                # FIX: Unquote the comment to handle URL-encoded characters
                # 'MID%3D123' becomes 'MID=123'
                clean_comment = unquote(raw_comment)
                
                results.append(self.normalize_torrent_info({
                    "hash": raw_hash,
                    "name": raw_name,
                    "save_path": self._build_save_path(raw_directory, raw_name, raw_is_multi_file),
                    "category": raw_category,
                    "comment": clean_comment,
                }, hash_val=raw_hash))
            
            return results
        except Exception:
            return []

    def _normalize_hash(self, value) -> str:
        return str(value or "").strip().lower()

    def _get_torrent_info_multicall_cmds(self):
        return [
            "d.hash=",
            "d.name=",
            "d.directory=",
            "d.is_multi_file=",
            "d.down.rate=",
            "d.completed_bytes=",
            "d.size_bytes=",
            self.label_attr + "=",
            self.comment_attr + "=",
            "d.state=",
            "d.is_active=",
            "d.is_hash_checking=",
            "d.complete=",
        ]

    def _parse_torrent_info_multicall_row(self, row):
        hash_val = self._normalize_hash(row[0] if len(row) > 0 else "")
        if not hash_val:
            return "", None

        info = self._format_data(
            hash_val,
            row[1] if len(row) > 1 else "",
            row[2] if len(row) > 2 else "",
            row[3] if len(row) > 3 else 0,
            row[4] if len(row) > 4 else 0,
            row[5] if len(row) > 5 else 0,
            row[6] if len(row) > 6 else 0,
            row[7] if len(row) > 7 else "",
            row[8] if len(row) > 8 else "",
            row[9] if len(row) > 9 else 0,
            row[10] if len(row) > 10 else 0,
            row[11] if len(row) > 11 else 0,
            row[12] if len(row) > 12 else 0,
        )
        return hash_val, info

    def _is_truthy(self, value) -> bool:
        try:
            return int(value) != 0
        except (TypeError, ValueError):
            return bool(value)

    def _build_save_path(self, directory, name, is_multi_file) -> str:
        directory_str = str(directory or "").strip()
        if not directory_str:
            return ""
        if self._is_truthy(is_multi_file):
            return str(Path(directory_str).parent)
        return directory_str

    def _map_state(self, is_open, is_active, is_hashing, is_complete) -> str:
        complete = self._is_truthy(is_complete)
        if self._is_truthy(is_hashing):
            return "checkingUP" if complete else "checkingDL"
        if not self._is_truthy(is_open) or not self._is_truthy(is_active):
            return "pausedUP" if complete else "pausedDL"
        return "uploading" if complete else "downloading"

    def _format_data(self, hash_val, name, directory, is_multi_file, down_rate, done, size, label, comment, is_open, is_active, is_hashing, is_complete):
        state = self._map_state(is_open, is_active, is_hashing, is_complete)

        progress = (done / size) if size > 0 else 0
        eta = -1
        if state == "downloading" and down_rate > 0:
            eta = int((size - done) / down_rate)

        return self.normalize_torrent_info({
            "name": name,
            "hash": self._normalize_hash(hash_val),
            "progress": progress,
            "eta": eta,
            "state": state,
            "category": label,
            "save_path": self._build_save_path(directory, name, is_multi_file),
            "comment": unquote(str(comment or "").strip()),
            "total_size": size,
            "tracker_error": "",
        }, hash_val=self._normalize_hash(hash_val))
