"""Shared constants and helpers for Meblio backend modules."""
import json
import os
import time
from pathlib import Path

from db import now

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PAGE_SIZE = 20


def is_dev_mode():
    """MEBLIO_DEV=1 enables dev conveniences. Off by default (safe for production)."""
    return os.environ.get("MEBLIO_DEV", "0") == "1"


def request_host(headers=None):
    """Host of the current request (always reachable): Host header > MEBLIO_HOST > meblio.local."""
    if headers:
        host = (headers.get("Host") or "").strip()
        if host:
            return host
    return os.environ.get("MEBLIO_HOST", "").strip() or "meblio.local"


def public_host(headers=None):
    """Stable host for canonical/sitemap/robots: MEBLIO_HOST > Host > meblio.local."""
    return os.environ.get("MEBLIO_HOST", "").strip() or request_host(headers)


def public_base_url(headers=None, default_scheme="http"):
    """Origin for outbound links: X-Forwarded-Proto > MEBLIO_SCHEME > default_scheme."""
    proto = ""
    if headers:
        proto = (headers.get("X-Forwarded-Proto") or "").split(",")[0].strip()
    if proto not in ("http", "https"):
        proto = os.environ.get("MEBLIO_SCHEME", "").strip() or default_scheme
    return f"{proto}://{request_host(headers)}"


def canonical_base_url(headers=None):
    """Origin for canonical/OG links: stable MEBLIO_HOST, https by default."""
    proto = ""
    if headers:
        proto = (headers.get("X-Forwarded-Proto") or "").split(",")[0].strip()
    if proto not in ("http", "https"):
        proto = os.environ.get("MEBLIO_SCHEME", "").strip() or "https"
    return f"{proto}://{public_host(headers)}"

ALLOWED_UPLOAD_EXTS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
    ".pdf", ".txt", ".csv", ".xlsx", ".docx", ".zip",
    ".dwg", ".dxf",
}
INLINE_UPLOAD_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"}

rate_limits = {}
_rate_limits_last_purge = 0.0


def check_rate_limit(key, max_attempts=5, window=300):
    global _rate_limits_last_purge
    now_ts = time.time()
    if len(rate_limits) > 128 and now_ts - _rate_limits_last_purge > 60:
        for stale_key in list(rate_limits.keys()):
            rate_limits[stale_key] = [t for t in rate_limits[stale_key] if now_ts - t < window]
            if not rate_limits[stale_key]:
                del rate_limits[stale_key]
        _rate_limits_last_purge = now_ts
    if key not in rate_limits:
        rate_limits[key] = []
    rate_limits[key] = [t for t in rate_limits[key] if now_ts - t < window]
    if len(rate_limits[key]) >= max_attempts:
        return False
    rate_limits[key].append(now_ts)
    return True


def json_dumps(data):
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def safe_filename(name):
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in "._- ").strip()
    return cleaned or "file"


def validate_upload_file(filename, content=b""):
    ext = Path(filename).suffix.lower()
    if not ext or ext not in ALLOWED_UPLOAD_EXTS:
        raise ValueError(
            "Тип файла не разрешён. Разрешены: "
            + ", ".join(sorted(ALLOWED_UPLOAD_EXTS))
        )
    _validate_magic_bytes(ext, content)
    return ext


_MAGIC = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".gif": (b"GIF8",),
    ".pdf": (b"%PDF",),
    ".webp": (b"RIFF",),
    ".zip": (b"PK\x03\x04", b"PK\x05\x06"),
    ".docx": (b"PK\x03\x04",),
    ".xlsx": (b"PK\x03\x04",),
}


def _validate_magic_bytes(ext, content):
    signatures = _MAGIC.get(ext)
    if not signatures or not content:
        return
    if not any(content.startswith(sig) for sig in signatures):
        raise ValueError("Содержимое файла не соответствует расширению")


def store_upload(prefix, filename, content):
    """Save upload to uploads/YYYY/MM/<prefix>_<rand>_<safe>. Returns (stored_path, original_name)."""
    import secrets
    import time
    from db import UPLOAD_DIR
    original = safe_filename(filename)
    sub = time.strftime("%Y/%m")
    folder = UPLOAD_DIR / sub
    folder.mkdir(parents=True, exist_ok=True)
    stored = f"{sub}/{prefix}_{secrets.token_hex(8)}_{original}"
    (UPLOAD_DIR / stored).write_bytes(content)
    return stored, original


def csv_safe(value):
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        text = "'" + text
    return text.replace('"', '""')


def parse_deadline_days(deadline_str):
    import re as _re
    m = _re.search(r"(\d+)", deadline_str)
    return int(m.group(1)) if m else 0


def create_notification(conn, user_id, ntype, title, body="", link=""):
    # Check user preferences
    prefs = conn.execute("SELECT * FROM notification_preferences WHERE user_id = ?", (user_id,)).fetchone()
    if prefs:
        pref_map = {
            "new_order": "new_order", "response": "response", "message": "message",
            "chosen": "chosen", "review": "review", "order_status": "order_status", "system": "system",
        }
        col = pref_map.get(ntype)
        if col and not prefs[col]:
            return  # User disabled this notification type

    cur = conn.execute("INSERT INTO notifications (user_id, type, title, body, link, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (user_id, ntype, title, body, link, now()))
    notif_id = cur.lastrowid

    # Real-time delivery via WebSocket
    try:
        from ws_server import ws_manager
        ws_manager.send_to_user(user_id, {
            "type": "notification",
            "notification": {
                "id": notif_id, "user_id": user_id, "type": ntype,
                "title": title, "body": body, "link": link,
                "is_read": 0, "created_at": now(),
            },
        })
    except (ImportError, Exception):
        pass

    # Email simulation (log to console)
    if prefs and prefs["email_enabled"]:
        from logger import get_logger
        get_logger("notify").info("[EMAIL NOTIFICATION] To user %s: %s — %s", user_id, title, body)

    # External messengers (Telegram / MAX) — fire-and-forget
    try:
        row = conn.execute(
            "SELECT telegram_chat_id, max_chat_id FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if row:
            text = f"{title}\n{body}".strip()
            if row["telegram_chat_id"]:
                send_telegram_message(row["telegram_chat_id"], text)
            if row["max_chat_id"]:
                send_max_message(row["max_chat_id"], text)
    except Exception:
        pass


def send_telegram_message(chat_id, text):
    import os as _os
    token = (_os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token or not chat_id:
        return False
    import json as _json
    import urllib.request as _ur
    try:
        payload = _json.dumps({"chat_id": str(chat_id), "text": text[:4000]}).encode("utf-8")
        req = _ur.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with _ur.urlopen(req, timeout=5):
            return True
    except Exception:
        return False


def send_max_message(chat_id, text):
    import os as _os
    token = (_os.environ.get("MAX_API_TOKEN") or "").strip()
    if not token or not chat_id:
        return False
    import json as _json
    import urllib.request as _ur
    try:
        base = (_os.environ.get("MAX_API_BASE") or "https://botapi.max.ru").rstrip("/")
        payload = _json.dumps({"chat_id": str(chat_id), "text": text[:4000]}).encode("utf-8")
        req = _ur.Request(
            f"{base}/messages",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="POST",
        )
        with _ur.urlopen(req, timeout=5):
            return True
    except Exception:
        return False
