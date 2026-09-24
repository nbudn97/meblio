"""Auth helpers: TOTP, trusted devices, pending tokens, CSRF (shared by app and mixins)."""
import secrets
from http import cookies

from db import now

TRUST_DEVICE_COOKIE = "meblio_device"


TRUST_DEVICE_DAYS = 30


def verify_totp(secret, code):
    """Check a 6-digit TOTP code against a base32 secret (30s window, ±2 steps)."""
    import base64 as _b64
    import hashlib as _hashlib
    import hmac as _hmac
    import struct as _struct
    import time as _time
    try:
        secret_bytes = _b64.b32decode(secret)
        counter = int(_time.time()) // 30
        for offset in (-2, -1, 0, 1, 2):
            msg = _struct.pack(">Q", counter + offset)
            h = _hmac.new(secret_bytes, msg, _hashlib.sha1).digest()
            o = h[-1] & 0x0F
            num = _struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF
            if str(num % 1000000).zfill(6) == code:
                return True
    except Exception:
        return False
    return False


def get_tfa_trust_secret(conn):
    row = conn.execute("SELECT value FROM app_config WHERE key = 'tfa_trust_secret'").fetchone()
    if row:
        return row["value"]
    secret = secrets.token_hex(32)
    conn.execute("INSERT OR IGNORE INTO app_config (key, value) VALUES ('tfa_trust_secret', ?)", (secret,))
    return secret


def make_trust_cookie(secret, user_id, password_hash):
    import hashlib as _hashlib
    import hmac as _hmac
    import time as _time
    expires = int(_time.time()) + TRUST_DEVICE_DAYS * 86400
    basis = f"{user_id}:{expires}:{password_hash}"
    sig = _hmac.new(secret.encode(), basis.encode(), _hashlib.sha256).hexdigest()
    return f"{user_id}:{expires}:{sig}"


def verify_trust_cookie(secret, cookie_value, conn):
    import hashlib as _hashlib
    import hmac as _hmac
    import time as _time
    try:
        user_id, expires, sig = cookie_value.split(":")
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (int(user_id),)).fetchone()
        if not row:
            return None
        basis = f"{user_id}:{expires}:{row['password_hash']}"
        expected = _hmac.new(secret.encode(), basis.encode(), _hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(sig, expected):
            return None
        if int(expires) < _time.time():
            return None
        return int(user_id)
    except (ValueError, TypeError):
        return None


def read_trusted_user_id(self, conn):
    jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
    raw = jar.get(TRUST_DEVICE_COOKIE)
    if not raw:
        return None
    return verify_trust_cookie(get_tfa_trust_secret(conn), raw.value, conn)


def create_pending_token(conn, table, user_id, minutes):
    import datetime
    token = secrets.token_urlsafe(32)
    expires = (datetime.datetime.now() + datetime.timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        f"INSERT INTO {table} (token, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
        (token, user_id, expires, now()),
    )
    conn.execute(f"DELETE FROM {table} WHERE expires_at < ?", (now(),))
    return token


def generate_csrf_token(conn, session_token):
    import datetime
    existing = conn.execute(
        "SELECT token FROM csrf_tokens WHERE session_token = ? AND expires_at > ?",
        (session_token, now()),
    ).fetchone()
    if existing:
        return existing["token"]
    token = secrets.token_urlsafe(32)
    expires = (datetime.datetime.now() + datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO csrf_tokens (token, session_token, expires_at, created_at) VALUES (?, ?, ?, ?)",
                 (token, session_token, expires, now()))
    conn.execute("DELETE FROM csrf_tokens WHERE expires_at < ?", (now(),))
    return token


def validate_csrf_token(conn, token, session_token):
    if not token or not session_token:
        return False
    row = conn.execute("SELECT 1 FROM csrf_tokens WHERE token = ? AND session_token = ? AND expires_at > ?",
                       (token, session_token, now())).fetchone()
    return row is not None
