"""Check preview isolation and packaged asset routing, without touching live services."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="aiwu-preview-") as temporary:
        os.environ["DAFEIYU_HOME"] = temporary
        from backend import server

        stopped = {"astrbot": {"up": False, "pid": None, "port": 6185},
                   "napcat": {"up": False, "pid": None, "port": 6099},
                   "onebot": {"up": False, "port": 6199}}
        with patch.object(server, "_snapshot_napcat", return_value={"logged_in": False}), \
             patch.object(server.processes, "status", return_value=stopped), \
             patch.object(server.metrics, "snapshot", return_value={}):
            fresh = server.start_server(auto_start=False, auto_update=False)
            client = http.client.HTTPConnection(*fresh.server_address, timeout=5)
            try:
                client.request("GET", "/api/state")
                response = client.getresponse()
                value = json.loads(response.read())
                assert response.status == 200
                assert not value["setup_complete"] and not value["installed"]
                assert not value["has_key"] and not value["owner_qq"] and not value["bot_qq"]
                assert not server.config_store.CONFIG_FILE.exists()
            finally:
                client.close()
                fresh.shutdown()
                fresh.server_close()
        before = set(Path(temporary).rglob("*"))

        def forbidden(*args, **kwargs):
            raise AssertionError("Preview tried to access installed services")

        server.processes.status = forbidden
        server.processes.start_all = forbidden
        server.config_store.load = forbidden
        server.lovehouse.libraries = forbidden
        server.napcat_api.login_status = forbidden
        instance = server.start_server(preview=True)
        connection = http.client.HTTPConnection(*instance.server_address, timeout=5)
        try:
            for path in ("/api/state", "/api/status", "/api/lovehouse/status", "/api/plugins", "/api/updates"):
                connection.request("GET", path)
                response = connection.getresponse()
                assert response.status == 200, path
                value = json.loads(response.read())
                if path == "/api/state":
                    assert value["preview"] and not value["owner_qq"]
            connection.request("POST", "/api/start", body=b"{}", headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            assert response.status == 403
            response.read()
            connection.request("GET", "/assets/avatars/dafeiyu.png")
            response = connection.getresponse()
            assert response.status == 200 and response.read().startswith(b"\x89PNG")
            connection.request("GET", "/assets/../LICENSE")
            response = connection.getresponse()
            assert response.status == 404
            response.read()
            assert before == set(Path(temporary).rglob("*")), "Preview wrote user state"
        finally:
            connection.close()
            instance.shutdown()
            instance.server_close()
    print("PASS: fresh profile, preview isolation, blocked writes, assets and traversal")


if __name__ == "__main__":
    main()
