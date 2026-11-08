import asyncio
import os
import tempfile
import unittest

from clients.rtorrent import RTorrentClient


CANNED_VERSION_XML = (
    b"<?xml version='1.0'?>\n"
    b"<methodResponse><params><param><value><string>0.9.8</string></value></param>"
    b"</params></methodResponse>"
)


async def _read_scgi_request(reader):
    """Reads one SCGI request and returns (headers_dict, body_bytes)."""
    length_digits = b""
    while True:
        ch = await reader.readexactly(1)
        if ch == b":":
            break
        length_digits += ch
    header_len = int(length_digits.decode("ascii"))
    raw_headers = await reader.readexactly(header_len)
    await reader.readexactly(1)  # trailing comma

    headers = {}
    parts = raw_headers.split(b"\x00")
    for i in range(0, len(parts) - 1, 2):
        headers[parts[i].decode("ascii")] = parts[i + 1].decode("ascii")

    body = await reader.readexactly(int(headers["CONTENT_LENGTH"]))
    return headers, body


class SocketPathTests(unittest.TestCase):
    def test_extracts_socket_path(self):
        client = RTorrentClient({"TORRENT_CLIENT_URL": "scgi+unix:///rpc.sock"})
        self.assertEqual(client.socket_path, "/rpc.sock")

    def test_unquotes_socket_path(self):
        client = RTorrentClient({"TORRENT_CLIENT_URL": "scgi+unix:///tmp/my%20sock"})
        self.assertEqual(client.socket_path, "/tmp/my sock")

    def test_http_url_leaves_socket_path_unset(self):
        client = RTorrentClient({"TORRENT_CLIENT_URL": "http://localhost/RPC2"})
        self.assertIsNone(client.socket_path)

    def test_empty_socket_path_raises(self):
        with self.assertRaises(ValueError):
            RTorrentClient({"TORRENT_CLIENT_URL": "scgi+unix://"})


class ScgiExchangeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="mousesearch-scgi-test-")
        self.sock_path = os.path.join(self.tmpdir, "rpc.sock")

    def tearDown(self):
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)
        os.rmdir(self.tmpdir)

    def _run_with_server(self, response_bytes, captured):
        client = RTorrentClient({"TORRENT_CLIENT_URL": f"scgi+unix://{self.sock_path}"})

        async def handler(reader, writer):
            captured["request"] = await _read_scgi_request(reader)
            writer.write(response_bytes)
            await writer.drain()
            writer.close()

        async def run():
            server = await asyncio.start_unix_server(handler, path=self.sock_path)
            async with server:
                logged_in = await client.login()
                version = await client.get_api_version()
            return logged_in, version

        return asyncio.run(run())

    def test_round_trip_and_framing(self):
        captured = {}
        logged_in, version = self._run_with_server(CANNED_VERSION_XML, captured)

        self.assertTrue(logged_in)
        self.assertEqual(version, "0.9.8")

        headers, body = captured["request"]
        self.assertEqual(headers.get("SCGI"), "1")
        self.assertEqual(int(headers["CONTENT_LENGTH"]), len(body))
        self.assertIn(b"<methodName>system.client_version</methodName>", body)

    def test_response_with_header_preamble(self):
        captured = {}
        response = b"Status: 200 OK\r\nContent-Type: text/xml\r\n\r\n" + CANNED_VERSION_XML
        logged_in, version = self._run_with_server(response, captured)

        self.assertTrue(logged_in)
        self.assertEqual(version, "0.9.8")

    def test_missing_socket_login_fails(self):
        missing = os.path.join(self.tmpdir, "does-not-exist.sock")
        client = RTorrentClient({"TORRENT_CLIENT_URL": f"scgi+unix://{missing}"})

        with self.assertLogs("clients.rtorrent", level="ERROR"):
            logged_in = asyncio.run(client.login())

        self.assertFalse(logged_in)


if __name__ == "__main__":
    unittest.main()
