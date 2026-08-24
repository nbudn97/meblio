"""Shared constants and helpers for Meblio backend modules."""
import json
import time
from pathlib import Path

from db import now

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PAGE_SIZE = 20

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


def validate_upload_file(filename):
    ext = Path(filename).suffix.lower()
    if not ext or ext not in ALLOWED_UPLOAD_EXTS:
        raise ValueError(
            "Тип файла не разрешён. Разрешены: "
            + ", ".join(sorted(ALLOWED_UPLOAD_EXTS))
        )
    return ext


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
        print(f"[EMAIL NOTIFICATION] To user {user_id}: {title} — {body}")
