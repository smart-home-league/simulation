"""
Local web dashboard for the Webots supervisor.

- Serves the dashboard HTML from supervisor_window.html.
- WebSocket on the same port for live state (only when changed), commands
  (run, relocate), and upload (writes directly to robot/robot.py).
- Pure standard-library Python; no extra dependencies.
"""

import base64
import hashlib
import json
import os
import socket
import struct
import threading
import time
from pathlib import Path
from socketserver import ThreadingTCPServer
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse


# -----------------------------------------------------------------------------
# Shared state (unchanged public API for the supervisor)
# -----------------------------------------------------------------------------


class _SharedState:
    """Thread-safe container for scoreboard data and team info."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.team_name: str = ""
        self.points: int = 0
        self.percent: float = 0.0
        self.last_upload_filename: Optional[str] = None
        self.new_code_available: bool = False
        self.run_requested: bool = False
        self.relocate_requested: bool = False
        self.remaining_seconds: float = 0.0
        self.game_over: bool = False
        self.room_pcts: dict = {}
        self.current_room: int = -1
        self.score_log: list = []
        self.battery: Optional[float] = None  # U19 only; None = hide
        self.end_requested: bool = False
        self.subleague: str = ""


_STATE = _SharedState()


def set_subleague(subleague: str) -> None:
    """Set the subleague (U14 or U19) for the header display."""
    with _STATE.lock:
        _STATE.subleague = (subleague or "").strip()


def update_score(
    points: int,
    percent: float,
    remaining_seconds: float,
    game_over: bool,
    score_log: Optional[list] = None,
) -> None:
    """Update current score and run status (called from supervisor loop)."""
    with _STATE.lock:
        _STATE.points = int(points)
        _STATE.percent = float(percent)
        _STATE.remaining_seconds = float(max(0.0, remaining_seconds))
        _STATE.game_over = bool(game_over)
        _STATE.score_log = list(score_log) if score_log is not None else []


def set_team_name(name: str) -> None:
    """Set the displayed team name (called from supervisor when read from robot)."""
    with _STATE.lock:
        _STATE.team_name = (name or "").strip()


def set_battery(battery: Optional[float]) -> None:
    """Set battery level for U19; None hides the battery (U14)."""
    with _STATE.lock:
        _STATE.battery = float(battery) if battery is not None else None


def set_room_stats(room_pcts: dict, current_room: int) -> None:
    """Set room cleaning percentages and current room (U14, from supervisor)."""
    with _STATE.lock:
        _STATE.room_pcts = dict(room_pcts) if room_pcts else {}
        _STATE.current_room = (
            int(current_room) if current_room is not None else -1
        )


def get_state_snapshot() -> dict:
    """Return a copy of the current state as a plain dict."""
    with _STATE.lock:
        return {
            "teamName": _STATE.team_name,
            "points": _STATE.points,
            "percent": _STATE.percent,
            "lastUploadFilename": _STATE.last_upload_filename,
            "hasCode": _STATE.last_upload_filename is not None,
            "remainingSeconds": _STATE.remaining_seconds,
            "gameOver": _STATE.game_over,
            "roomPcts": _STATE.room_pcts,
            "currentRoom": _STATE.current_room,
            "scoreLog": _STATE.score_log,
            "battery": _STATE.battery,
            "subleague": _STATE.subleague,
        }


def consume_new_code_flag() -> bool:
    """Return True once when new code has been uploaded since last call."""
    with _STATE.lock:
        flag = _STATE.new_code_available
        _STATE.new_code_available = False
        return flag


def consume_run_request() -> bool:
    """Return True once when the user clicked the 'Run' button."""
    with _STATE.lock:
        flag = _STATE.run_requested
        _STATE.run_requested = False
        return flag


def consume_end_request() -> bool:
    """Return True once when the user clicked the 'End' button."""
    with _STATE.lock:
        flag = _STATE.end_requested
        _STATE.end_requested = False
        return flag


def consume_relocate_request() -> bool:
    """Return True once when the user clicked the 'Relocate' button."""
    with _STATE.lock:
        flag = _STATE.relocate_requested
        _STATE.relocate_requested = False
        return flag


# -----------------------------------------------------------------------------
# WebSocket (RFC 6455) helpers – stdlib only
# -----------------------------------------------------------------------------

_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _ws_accept_key(key: str) -> str:
    """Compute Sec-WebSocket-Accept from Sec-WebSocket-Key."""
    raw = (key + _WS_MAGIC).encode()
    return base64.b64encode(hashlib.sha1(raw).digest()).decode()


def _ws_send_text(sock: socket.socket, payload: bytes) -> None:
    """Send one WebSocket text frame (server → client, no mask)."""
    length = len(payload)
    if length < 126:
        header = struct.pack(">BB", 0x81, length)
    elif length < 65536:
        header = struct.pack(">BBH", 0x81, 126, length)
    else:
        header = struct.pack(">BBQ", 0x81, 127, length)
    sock.sendall(header + payload)


def _ws_recv_frame(sock: socket.socket) -> Optional[Tuple[int, bytes]]:
    """
    Read one WebSocket frame. Returns (opcode, payload) or None on close/error.
    Opcode 1 = text, 8 = close.
    """
    try:
        header = sock.recv(2)
        if len(header) < 2:
            return None
        opcode = header[0] & 0x0F
        masked = (header[1] & 0x80) != 0
        length = header[1] & 0x7F
        if length == 126:
            ext = sock.recv(2)
            if len(ext) < 2:
                return None
            length = struct.unpack(">H", ext)[0]
        elif length == 127:
            ext = sock.recv(8)
            if len(ext) < 8:
                return None
            length = struct.unpack(">Q", ext)[0]
        mask = sock.recv(4) if masked else b""
        if masked and len(mask) < 4:
            return None
        if length > 1024 * 1024:
            return None
        payload = b""
        while len(payload) < length:
            chunk = sock.recv(min(4096, length - len(payload)))
            if not chunk:
                return None
            payload += chunk
        if masked:
            payload = bytes(payload[i] ^ mask[i % 4] for i in range(len(payload)))
        return (opcode, payload)
    except (OSError, struct.error):
        return None


# -----------------------------------------------------------------------------
# Single-port server: HTTP + WebSocket
# -----------------------------------------------------------------------------

# Clients that have completed the WebSocket handshake (sockets).
_ws_clients: set[socket.socket] = set()
_ws_clients_lock = threading.Lock()
# Last state we sent over WebSocket; only broadcast when state actually changes.
_last_broadcast_json: Optional[str] = None
_broadcast_lock = threading.Lock()

# Path to dashboard HTML (same directory as this module).
_HTML_PATH = Path(__file__).resolve().parent / "supervisor_window.html"


def _handle_http_get(conn: socket.socket, path: str) -> None:
    """Serve dashboard for GET / or /index.html."""
    if path not in ("/", "/index.html"):
        _http_response(conn, 404, b"Not found", "text/plain")
        return
    try:
        body = _HTML_PATH.read_bytes()
    except OSError:
        _http_response(conn, 500, b"Internal Server Error", "text/plain")
        return
    _http_response(
        conn,
        200,
        body,
        "text/html; charset=utf-8",
    )


def _apply_upload(content_b64: str, filename: str) -> Optional[str]:
    """
    Decode base64 content, write to robot/robot.py, update state.
    Returns the saved filename on success, None on failure.
    """
    try:
        file_bytes = base64.b64decode(content_b64)
    except Exception:
        return None
    if len(file_bytes) > 2 * 1024 * 1024:
        return None
    controllers_dir = Path(__file__).resolve().parents[1]
    robot_dir = controllers_dir / "robot"
    robot_dir.mkdir(exist_ok=True)
    robot_controller_path = robot_dir / "robot.py"
    try:
        with open(robot_controller_path, "wb") as f:
            f.write(file_bytes)
    except OSError:
        return None
    saved_name = os.path.basename(filename) if filename else "robot.py"
    with _STATE.lock:
        _STATE.last_upload_filename = saved_name
        _STATE.new_code_available = True
    return saved_name


def _http_response(
    conn: socket.socket,
    status: int,
    body: bytes,
    content_type: str,
) -> None:
    """Send a simple HTTP response."""
    status_line = {
        200: "200 OK",
        303: "303 See Other",
        400: "400 Bad Request",
        404: "404 Not Found",
        500: "500 Internal Server Error",
    }.get(status, "500 Internal Server Error")
    headers = (
        f"HTTP/1.1 {status_line}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    conn.sendall(headers.encode("utf-8") + body)


def _read_headers(conn: socket.socket) -> Optional[Tuple[str, str, Dict[str, str], bytes]]:
    """
    Read request line and headers; return (method, path, headers, body_prefix)
    or None. body_prefix is any bytes already read after \\r\\n\\r\\n (for POST).
    """
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = conn.recv(4096)
        if not chunk:
            return None
        buf += chunk
        if len(buf) > 65536:
            return None
    head, body_prefix = buf.split(b"\r\n\r\n", 1)
    lines = head.decode("utf-8", errors="replace").split("\r\n")
    if not lines:
        return None
    parts = lines[0].split(None, 2)
    if len(parts) < 2:
        return None
    method = parts[0].upper()
    path = urlparse(parts[1]).path
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return (method, path, headers, body_prefix)


def _read_body(
    conn: socket.socket,
    content_length: int,
    prefix: bytes = b"",
) -> Optional[bytes]:
    """Read exactly content_length bytes from conn; prefix is already-read bytes."""
    if content_length < 0 or content_length > 10 * 1024 * 1024:
        return None
    body = bytes(prefix)
    while len(body) < content_length:
        chunk = conn.recv(min(4096, content_length - len(body)))
        if not chunk:
            return None
        body += chunk
    if len(body) > content_length:
        body = body[:content_length]
    return body


def _broadcast_state() -> None:
    """Send current state to all WebSocket clients only when it has changed."""
    global _last_broadcast_json
    payload = get_state_snapshot()
    new_json = json.dumps(payload, sort_keys=True)
    with _broadcast_lock:
        if new_json == _last_broadcast_json:
            return
        _last_broadcast_json = new_json
    data = json.dumps(payload).encode("utf-8")
    with _ws_clients_lock:
        dead = set()
        for sock in _ws_clients:
            try:
                _ws_send_text(sock, data)
            except OSError:
                dead.add(sock)
        for sock in dead:
            _ws_clients.discard(sock)
            try:
                sock.close()
            except OSError:
                pass


def _broadcaster_loop(interval: float) -> None:
    """Background thread: broadcast state every interval seconds."""
    while True:
        time.sleep(interval)
        _broadcast_state()


def _handle_websocket(conn: socket.socket) -> None:
    """
    After handshake, add conn to _ws_clients, send current state once, then
    handle incoming frames (run/relocate/upload) until disconnect.
    """
    with _ws_clients_lock:
        _ws_clients.add(conn)
    try:
        try:
            payload = get_state_snapshot()
            _ws_send_text(conn, json.dumps(payload).encode("utf-8"))
        except OSError:
            pass
        while True:
            frame = _ws_recv_frame(conn)
            if frame is None:
                break
            opcode, payload = frame
            if opcode == 8:
                break
            if opcode == 1 and payload:
                try:
                    msg = json.loads(payload.decode("utf-8"))
                    action = msg.get("action")
                    if action == "run":
                        with _STATE.lock:
                            _STATE.run_requested = True
                    elif action == "relocate":
                        with _STATE.lock:
                            _STATE.relocate_requested = True
                    elif action == "end":
                        with _STATE.lock:
                            _STATE.end_requested = True
                    elif action == "upload":
                        content = msg.get("content")
                        filename = msg.get("filename") or "robot.py"
                        if isinstance(content, str) and content:
                            _apply_upload(content, filename)
                            _broadcast_state()
                except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
                    pass
    finally:
        with _ws_clients_lock:
            _ws_clients.discard(conn)
        try:
            conn.close()
        except OSError:
            pass


def _handle_connection(conn: socket.socket) -> None:
    """
    First request on this connection: either WebSocket upgrade or HTTP.
    """
    info = _read_headers(conn)
    if info is None:
        conn.close()
        return
    method, path, headers, body_prefix = info

    if (
        method == "GET"
        and path == "/ws"
        and headers.get("upgrade", "").lower() == "websocket"
        and "sec-websocket-key" in headers
    ):
        key = headers["sec-websocket-key"]
        accept = _ws_accept_key(key)
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        try:
            conn.sendall(response.encode("utf-8"))
        except OSError:
            conn.close()
            return
        _handle_websocket(conn)
        return

    if method == "GET":
        _handle_http_get(conn, path)
        conn.close()
        return

    _http_response(conn, 404, b"Not found", "text/plain")
    conn.close()


class _DashboardHandler:
    """Request handler that dispatches to HTTP or WebSocket."""

    def __init__(
        self,
        request: socket.socket,
        client_address: tuple,
        server: ThreadingTCPServer,
    ) -> None:
        self.request = request
        self.client_address = client_address
        self.server = server
        self.handle()

    def handle(self) -> None:
        try:
            _handle_connection(self.request)
        except Exception:
            try:
                self.request.close()
            except OSError:
                pass


class _ReuseTCPServer(ThreadingTCPServer):
    """ThreadingTCPServer that allows address reuse on restart."""
    allow_reuse_address = True


def _server_factory(port: int) -> ThreadingTCPServer:
    """Build the TCP server; handler is set per request."""
    return _ReuseTCPServer(
        ("localhost", port),
        _DashboardHandler,
        bind_and_activate=True,
    )


def start_server(port: int = 8000) -> ThreadingTCPServer:
    """
    Start the dashboard server (HTTP + WebSocket on one port) in a background
    thread and return the server. Open http://localhost:<port>/ to load the
    dashboard; WebSocket connects to /ws on the same port.
    """
    server = _server_factory(port)
    # Broadcast state to WebSocket clients every second
    broadcaster = threading.Thread(
        target=_broadcaster_loop,
        args=(1.0,),
        daemon=True,
    )
    broadcaster.start()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
