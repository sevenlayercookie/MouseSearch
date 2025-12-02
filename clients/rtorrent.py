import httpx
import xml.etree.ElementTree as ET
from clients.base import TorrentClient
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
                cmds.append(f'd.custom2.set="{comment}"')

            await self._request("load.start_verbose", cmds)
            return {"status": "success", "message": "Torrent added to rTorrent"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    async def get_torrent_info(self, torrent_hash: str):
        try:
            # Fetch specific fields
            # d.state (1=open/0=closed), d.is_active (1=started/0=stopped), d.complete (1=done)
            name = await self._request("d.name", [torrent_hash])
            down_rate = await self._request("d.down.rate", [torrent_hash])
            done = await self._request("d.completed_bytes", [torrent_hash])
            size = await self._request("d.size_bytes", [torrent_hash])
            label = await self._request(self.label_attr, [torrent_hash])
            
            is_open = await self._request("d.state", [torrent_hash]) 
            is_active = await self._request("d.is_active", [torrent_hash]) 
            is_hash_checking = await self._request("d.is_hash_checking", [torrent_hash])
            is_complete = await self._request("d.complete", [torrent_hash])

            return self._format_data(
                torrent_hash, name, down_rate, done, size, label, 
                is_open, is_active, is_hash_checking, is_complete
            )
        except:
            return None

    async def get_torrent_info_batch(self, hashes: list):
        if not hashes: return {"torrents": {}}
        try:
            # Fetch ALL torrents in "main" view (most efficient in XMLRPC)
            cmds = [
                "d.hash=", "d.name=", "d.down.rate=", "d.completed_bytes=", "d.size_bytes=", 
                self.label_attr + "=", "d.state=", "d.is_active=", "d.is_hash_checking=", "d.complete="
            ]
            data = await self._request("d.multicall2", ["", "main"] + cmds)
            
            result = {}
            target_hashes = set(hashes)
            
            for row in data:
                h = row[0]
                if h in target_hashes:
                    result[h] = self._format_data(h, *row[1:])
            return {"torrents": result}
        except:
            return {"torrents": {}}

    async def get_torrents_with_metadata(self):
        """
        Returns list of all torrents with metadata.
        Decodes URL-encoded comments common in ruTorrent (e.g., MID%3D123 -> MID=123).
        """
        try:
            # Fetch hash and comment (d.custom2)
            data = await self._request("d.multicall2", ["", "main", "d.hash=", "d.custom2="])
            
            results = []
            for r in data:
                raw_hash = r[0]
                raw_comment = r[1] or ""
                
                # FIX: Unquote the comment to handle URL-encoded characters
                # 'MID%3D123' becomes 'MID=123'
                clean_comment = unquote(raw_comment)
                
                results.append({
                    "hash": raw_hash, 
                    "comment": clean_comment
                })
            
            return results
        except Exception:
            return []

    def _format_data(self, hash_val, name, down_rate, done, size, label, is_open, is_active, is_hashing, is_complete):
        state = "paused"
        if is_hashing: state = "checkingUP" if is_complete else "checkingDL"
        elif is_open == 0 or is_active == 0: state = "paused"
        elif is_complete: state = "uploading"
        else: state = "downloading"

        progress = (done / size) if size > 0 else 0
        eta = 8640000
        if state == "downloading" and down_rate > 0:
            eta = int((size - done) / down_rate)

        return {
            "name": name,
            "hash": hash_val,
            "progress": progress,
            "eta": eta,
            "state": state,
            "category": label,
            "save_path": "" 
        }