"""Exercise real loopback HTTP/SSE closes through the Windows server entry point."""
from __future__ import annotations

import http.client
import json
import queue
import socket
import struct
import subprocess
import sys
import threading
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows socket close regression")
def test_windows_api_releases_closed_http_and_sse_connections(tmp_path: Path):
    process = subprocess.Popen([sys.executable, "-m", "worklead", "--sidecar", "--data-dir", str(tmp_path)],
                               cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                               creationflags=subprocess.CREATE_NO_WINDOW)
    messages: queue.Queue[str] = queue.Queue()

    def read_ready():
        for line in process.stdout:
            messages.put(line)

    threading.Thread(target=read_ready, daemon=True).start()
    try:
        ready = json.loads(messages.get(timeout=30))
        assert ready["event"] == "ready"
        port = ready["port"]
        token = ready["token"]
        for attempt in range(20):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                connection.request("GET", "/v1/health", headers={"Authorization": "Bearer " + token})
                response = connection.getresponse()
                assert response.status == 200
                response.read()
            finally:
                connection.close()
            with socket.create_connection(("127.0.0.1", port), timeout=5) as stream:
                stream.sendall((f"GET /v1/events HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                                f"Authorization: Bearer {token}\r\nAccept: text/event-stream\r\n\r\n").encode())
                received = b""
                while b"connected" not in received:
                    chunk = stream.recv(4096)
                    assert chunk
                    received += chunk
                if attempt % 2:
                    stream.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("hh", 1, 0))
    finally:
        process.stdin.close()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise
        finally:
            process.stdout.close()
    assert process.returncode == 0
    log = (tmp_path / "logs" / "backend.log").read_text(encoding="utf-8")
    assert "WinError 10022" not in log
    assert "Exception in callback" not in log
