from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from mocap_studio.server import run_server


class ControllerStub:
    def snapshot(self):
        return {"ready": True}

    def connect(self, body):
        self.connected = body

    def disconnect(self):
        pass

    def command(self, command, body):
        return command


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.static = Path(self.temporary.name)
        (self.static / "index.html").write_text("<main>studio</main>")
        (self.static / "app.js").write_text("window.ok=true")
        self.patcher = patch("mocap_studio.server.STATIC_ROOT", self.static)
        self.patcher.start()
        self.server = run_server(ControllerStub(), port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(1)
        self.patcher.stop()
        self.temporary.cleanup()

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        result = response.status, dict(response.getheaders()), data
        connection.close()
        return result

    def test_host_header_and_json_validation(self) -> None:
        status, headers, body = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"status": "ok"})
        self.assertIn("Content-Security-Policy", headers)
        status, _, _ = self.request("GET", "/api/health", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("GET", "/api/health", headers={"Host": "127.999.1.1"})
        self.assertEqual(status, 403)

        status, _, body = self.request(
            "POST", "/api/connect", "[]", {"Content-Type": "application/json"}
        )
        self.assertEqual(status, 400)
        self.assertIn("object", json.loads(body)["error"])
        status, _, _ = self.request(
            "POST", "/api/connect", '{"fps":NaN}', {"Content-Type": "application/json"}
        )
        self.assertEqual(status, 400)

    def test_static_spa_assets_traversal_and_head(self) -> None:
        status, _, body = self.request("GET", "/")
        self.assertEqual((status, body), (200, b"<main>studio</main>"))
        status, _, body = self.request("GET", "/takes/one")
        self.assertEqual((status, body), (200, b"<main>studio</main>"))
        self.assertEqual(self.request("GET", "/missing.js")[0], 404)
        self.assertEqual(self.request("GET", "/%2e%2e/secret")[0], 403)
        status, headers, body = self.request("HEAD", "/app.js")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertEqual(int(headers["Content-Length"]), len(b"window.ok=true"))


if __name__ == "__main__":
    unittest.main()
