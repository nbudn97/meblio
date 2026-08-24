"""Minimal WebSocket server for Meblio real-time chat (RFC 6455, stdlib only)."""

import hashlib
import base64
import struct
import socket
import threading
import json
import time
from http.cookies import SimpleCookie

from db import connect, row_to_dict, session_cutoff
from logger import get_logger

logger = get_logger("ws")

WS_MAGIC = "258EAFA5-E914-47DA-95CA-5AB9B0054F03"
OPCODE_TEXT = 0x1
OPCODE_CLOSE = 0x8
OPCODE_PING = 0x9
OPCODE_PONG = 0xA


class ConnectionManager:
    def __init__(self):
        self._lock = threading.Lock()
        self.connections = {}       # user_id -> WebSocketHandler
        self.subscriptions = {}     # thread_id -> set(user_ids)
        self.user_threads = {}      # user_id -> set(thread_ids)

    def register(self, user_id, handler):
        with self._lock:
            self.connections[user_id] = handler
            self.user_threads.setdefault(user_id, set())

    def unregister(self, user_id):
        with self._lock:
            self.connections.pop(user_id, None)
            tids = self.user_threads.pop(user_id, set())
            for tid in tids:
                subs = self.subscriptions.get(tid)
                if subs:
                    subs.discard(user_id)
                    if not subs:
                        del self.subscriptions[tid]

    def subscribe(self, user_id, thread_id):
        with self._lock:
            self.subscriptions.setdefault(thread_id, set()).add(user_id)
            self.user_threads.setdefault(user_id, set()).add(thread_id)

    def unsubscribe(self, user_id, thread_id):
        with self._lock:
            subs = self.subscriptions.get(thread_id)
            if subs:
                subs.discard(user_id)
                if not subs:
                    del self.subscriptions[thread_id]
            ut = self.user_threads.get(user_id)
            if ut:
                ut.discard(thread_id)

    def broadcast_to_thread(self, thread_id, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        with self._lock:
            user_ids = list(self.subscriptions.get(thread_id, set()))
        for uid in user_ids:
            handler = self.connections.get(uid)
            if handler:
                handler.send_frame(OPCODE_TEXT, data)

    def send_to_user(self, user_id, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        with self._lock:
            handler = self.connections.get(user_id)
        if handler:
            handler.send_frame(OPCODE_TEXT, data)

    def is_user_in_thread(self, user_id, thread_id):
        with self._lock:
            subs = self.subscriptions.get(thread_id)
            return subs is not None and user_id in subs


ws_manager = ConnectionManager()


def parse_cookie(header_lines, cookie_name):
    """Extract cookie value from raw HTTP headers."""
    for line in header_lines:
        lower = line.lower()
        if lower.startswith("cookie:"):
            raw = line.split(":", 1)[1].strip()
            cookie = SimpleCookie()
            cookie.load(raw)
            morsel = cookie.get(cookie_name)
            if morsel:
                return morsel.value
    return None


def validate_session(token):
    """Return user dict if session is valid, else None."""
    if not token:
        return None
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id WHERE sessions.token = ? AND sessions.created_at >= ?",
                (token, session_cutoff()),
            ).fetchone()
            return row_to_dict(row) if row else None
    except Exception:
        return None


def validate_thread_access(user_id, thread_id):
    """Return True only if user participates in the thread."""
    if not user_id or not thread_id:
        return False
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM threads WHERE id = ? AND (client_id = ? OR maker_id = ?)",
                (thread_id, user_id, user_id),
            ).fetchone()
            return row is not None
    except Exception:
        return False


class WebSocketHandler(threading.Thread):
    def __init__(self, sock, addr, server):
        super().__init__(daemon=True)
        self.sock = sock
        self.addr = addr
        self.server = server
        self.user_id = None
        self._closed = False
        self._send_lock = threading.Lock()
        self.last_activity = time.time()

    def run(self):
        try:
            if not self._do_handshake():
                return
            self._handle_messages()
        except Exception:
            pass
        finally:
            self._cleanup()

    def _do_handshake(self):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = self.sock.recv(4096)
            if not chunk:
                return False
            data += chunk
            if len(data) > 8192:
                return False

        header_text = data.decode("utf-8", errors="replace")
        lines = header_text.split("\r\n")
        if not lines[0].upper().startswith("GET"):
            return False

        key = None
        for line in lines[1:]:
            lower = line.lower()
            if lower.startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
                break
        if not key:
            return False

        accept = base64.b64encode(
            hashlib.sha1((key + WS_MAGIC).encode()).digest()
        ).decode()

        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        self.sock.sendall(response.encode())
        return True

    def _handle_messages(self):
        while not self._closed:
            try:
                opcode, payload = self._recv_frame()
                if opcode is None:
                    break
                self.last_activity = time.time()
                if opcode == OPCODE_TEXT:
                    self._on_message(payload.decode("utf-8", errors="replace"))
                elif opcode == OPCODE_PING:
                    self.send_frame(OPCODE_PONG, payload)
                elif opcode == OPCODE_CLOSE:
                    break
            except Exception:
                break

    def _recv_frame(self):
        header = self._recv_exact(2)
        if not header:
            return None, None

        b0, b1 = header[0], header[1]
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F

        if length == 126:
            ext = self._recv_exact(2)
            if not ext:
                return None, None
            length = struct.unpack("!H", ext)[0]
        elif length == 127:
            ext = self._recv_exact(8)
            if not ext:
                return None, None
            length = struct.unpack("!Q", ext)[0]

        mask_key = None
        if masked:
            mask_key = self._recv_exact(4)
            if not mask_key:
                return None, None

        payload = self._recv_exact(length) if length > 0 else b""
        if payload is None:
            return None, None

        if masked and mask_key:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

        return opcode, payload

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def send_frame(self, opcode, payload):
        if self._closed:
            return
        with self._send_lock:
            try:
                frame = bytearray()
                frame.append(0x80 | opcode)
                length = len(payload)
                if length < 126:
                    frame.append(length)
                elif length < 65536:
                    frame.append(126)
                    frame.extend(struct.pack("!H", length))
                else:
                    frame.append(127)
                    frame.extend(struct.pack("!Q", length))
                frame.extend(payload)
                self.sock.sendall(frame)
            except Exception:
                self._closed = True

    def send_json(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_frame(OPCODE_TEXT, data)

    def _on_message(self, text):
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            return

        msg_type = msg.get("type")

        if msg_type == "auth":
            token = msg.get("token", "")
            user = validate_session(token)
            if user:
                self.user_id = user["id"]
                ws_manager.register(user["id"], self)
                self.send_json({"type": "auth_ok", "user_id": user["id"]})
            else:
                self.send_json({"type": "auth_error", "error": "Invalid session"})

        elif msg_type == "subscribe" and self.user_id:
            thread_id = msg.get("thread_id")
            if thread_id:
                try:
                    thread_id = int(thread_id)
                except (TypeError, ValueError):
                    return
                if validate_thread_access(self.user_id, thread_id):
                    ws_manager.subscribe(self.user_id, thread_id)
                else:
                    self.send_json({"type": "subscribe_error", "thread_id": thread_id, "error": "Нет доступа к переписке"})

        elif msg_type == "unsubscribe" and self.user_id:
            thread_id = msg.get("thread_id")
            if thread_id:
                try:
                    thread_id = int(thread_id)
                except (TypeError, ValueError):
                    return
                ws_manager.unsubscribe(self.user_id, thread_id)

        elif msg_type == "ping":
            self.send_json({"type": "pong"})

    def _cleanup(self):
        self._closed = True
        if self.user_id:
            ws_manager.unregister(self.user_id)
        try:
            self.sock.close()
        except Exception:
            pass


class WebSocketServer(threading.Thread):
    def __init__(self, port=8001):
        super().__init__(daemon=True)
        self.port = port
        self._running = True
        self._server_sock = None

    def run(self):
        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.settimeout(1.0)
        self._server_sock.bind(("127.0.0.1", self.port))
        self._server_sock.listen(32)
        logger.info("WebSocket server: ws://127.0.0.1:%s", self.port)

        while self._running:
            try:
                client_sock, addr = self._server_sock.accept()
                handler = WebSocketHandler(client_sock, addr, self)
                handler.start()
            except socket.timeout:
                continue
            except OSError:
                break

    def stop(self):
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except Exception:
                pass
