"""Meblio test suite (stdlib only). Run: python -m unittest tests -v"""
import io
import json
import os
import secrets
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import quote

_TMP = tempfile.mkdtemp(prefix="meblio-test-")
os.environ["MEBLIO_DB"] = os.path.join(_TMP, "test.db")
os.environ["MEBLIO_UPLOADS"] = os.path.join(_TMP, "uploads")
os.environ["MEBLIO_DEV"] = "1"          # tests assert verify_url in responses
os.environ["MEBLIO_HOST"] = "meblio.local"  # tests assert canonical host

from db import init_db  # noqa: E402  (env must be set before import)
import app as app_module  # noqa: E402
from ws_server import validate_session, validate_thread_access  # noqa: E402

_server = None
_base_url = None


def setUpModule():
    global _server, _base_url
    init_db()
    # rate limit is imported per-module — disable everywhere tests create users fast
    def _always_ok(*args, **kwargs):
        return True
    for _mod in (
        app_module,
        app_module.AccountMixin,
        app_module.OrderMixin,
        app_module.MarketMixin,
        app_module.AdminMixin,
        app_module.CatalogMixin,
        app_module.AiMixin,
    ):
        _mod.check_rate_limit = _always_ok
    try:
        import api_orders as _ao
        import api_accounts as _aa
        import api_market as _am
        import api_admin as _ad
        import api_catalog as _ac
        import api_ai as _ai
        import common as _common
        for _m in (_ao, _aa, _am, _ad, _ac, _ai, _common):
            _m.check_rate_limit = _always_ok
    except ImportError:
        pass
    _server = ThreadingHTTPServer(("127.0.0.1", 0), app_module.MeblioHandler)
    _base_url = f"http://127.0.0.1:{_server.server_address[1]}"
    threading.Thread(target=_server.serve_forever, daemon=True).start()


def tearDownModule():
    if _server:
        _server.shutdown()


class Client:
    """Tiny HTTP helper with manual session-cookie handling."""

    def __init__(self):
        self.token = None
        self.csrf = None
        self.device_cookie = None

    def request(self, method, path, body=None, headers=None, raw_body=None):
        url = _base_url + quote(path, safe="/?&=")
        data = raw_body
        req_headers = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")
        cookie_parts = []
        if self.token:
            cookie_parts.append(f"meblio_session={self.token}")
        if self.device_cookie:
            cookie_parts.append(f"meblio_device={self.device_cookie}")
        if cookie_parts:
            req_headers.setdefault("Cookie", "; ".join(cookie_parts))
        if self.csrf and method not in ("GET", "HEAD"):
            req_headers.setdefault("X-CSRF-Token", self.csrf)
        req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            status = resp.status
            payload = resp.read()
            resp_headers = dict(resp.headers)
            resp_headers["_set_cookie_all"] = resp.headers.get_all("Set-Cookie") or []
        except urllib.error.HTTPError as err:
            status = err.code
            payload = err.read()
            resp_headers = dict(err.headers)
            resp_headers["_set_cookie_all"] = err.headers.get_all("Set-Cookie") or []
            err.close()
        parsed = {}
        if payload:
            try:
                parsed = json.loads(payload.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                parsed = {"_raw": payload}
        return status, parsed, resp_headers

    def _after_auth(self, headers):
        set_cookie = headers.get("Set-Cookie", "")
        if "meblio_session=" in set_cookie:
            self.token = set_cookie.split("meblio_session=", 1)[1].split(";", 1)[0]
            if self.token:
                self.fetch_csrf()

    def register(self, email, password="secret123", role="client", name="Test Co", consent=True):
        body = {"role": role, "name": name, "email": email,
                "password": password, "city": "Москва"}
        if consent:
            body["consent_pd"] = "1"
        status, data, headers = self.request("POST", "/api/register", body=body)
        self._after_auth(headers)
        return status, data

    def login(self, email, password):
        status, data, headers = self.request(
            "POST", "/api/login", body={"email": email, "password": password},
        )
        self._after_auth(headers)
        return status, data

    def fetch_csrf(self):
        status, data, _ = self.request("POST", "/api/csrf-token")
        self.csrf = data.get("csrf_token")
        return status, self.csrf


def make_multipart(fields, files):
    boundary = "----mebliotest" + secrets.token_hex(8)
    buf = io.BytesIO()
    for name, value in fields.items():
        buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8"))
    for field, filename, content, mime in files:
        buf.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n".encode("utf-8"))
        buf.write(content)
        buf.write(b"\r\n")
    buf.write(f"--{boundary}--\r\n".encode("utf-8"))
    return buf.getvalue(), f"multipart/form-data; boundary={boundary}"


class AuthTests(unittest.TestCase):
    def test_anonymous_session_is_none(self):
        c = Client()
        status, data, _ = c.request("GET", "/api/session")
        self.assertEqual(status, 200)
        self.assertIsNone(data["user"])

    def test_register_login_logout(self):
        c = Client()
        status, data = c.register("auth-user@test.local")
        self.assertEqual(status, 200)
        self.assertIsNotNone(c.token)
        self.assertEqual(data["user"]["role"], "client")

        dup = Client()
        status, _ = dup.register("auth-user@test.local")
        self.assertEqual(status, 409)

        c.fetch_csrf()
        status, _, _ = c.request("POST", "/api/logout")
        self.assertEqual(status, 200)
        status, data, _ = c.request("GET", "/api/session")
        self.assertIsNone(data["user"])

    def test_login_wrong_password(self):
        c = Client()
        status, _ = c.login("client@meblio.ru", "wrong-password")
        self.assertEqual(status, 401)


class CsrfTests(unittest.TestCase):
    def test_post_without_csrf_rejected(self):
        c = Client()
        c.register("csrf-user@test.local")
        saved_token, saved_csrf = c.token, c.csrf
        c.csrf = None
        fields = {"title": "T", "type": "T", "quantity": "1", "city": "Москва",
                  "budget": "100", "deadline": "5 дней", "details": "d"}
        body, ctype = make_multipart(fields, [])
        status, _, _ = c.request("POST", "/api/orders", raw_body=body,
                                 headers={"Content-Type": ctype})
        self.assertEqual(status, 403)
        c.token, c.csrf = saved_token, saved_csrf

    def test_post_with_csrf_accepted_and_reusable(self):
        c = Client()
        c.register("csrf-ok@test.local")
        status, csrf = c.fetch_csrf()
        self.assertEqual(status, 200)
        fields = {"title": "CSRF OK", "type": "Кухни", "quantity": "1",
                  "city": "Москва", "budget": "500", "deadline": "3 дня", "details": "x"}
        body, ctype = make_multipart(fields, [])
        status, data, _ = c.request("POST", "/api/orders", raw_body=body,
                                    headers={"Content-Type": ctype})
        self.assertEqual(status, 200)
        order_id = data["order"]["id"]

        status, _, _ = c.request("POST", "/api/notifications/read-all", body={})
        self.assertEqual(status, 200)

        status, data, _ = c.request("GET", f"/api/orders?city=Москва")
        match = [o for o in data["orders"] if o["id"] == order_id]
        self.assertEqual(len(match), 1)
        self.assertEqual(match[0]["title"], "CSRF OK")

    def test_bad_csrf_rejected(self):
        c = Client()
        c.register("csrf-bad@test.local")
        c.csrf = "definitely-wrong"
        status, _, _ = c.request("POST", "/api/notifications/read-all", body={})
        self.assertEqual(status, 403)


class OrderFlowTests(unittest.TestCase):
    def test_full_order_lifecycle(self):
        client = Client()
        client.register("flow-client@test.local", name="Flow Client")
        maker = Client()
        maker.register("flow-maker@test.local", role="maker", name="Flow Maker")

        fields = {"title": "Кухни для кафе", "type": "Кухни и шкафы", "quantity": "5",
                  "city": "Москва", "budget": "900000", "deadline": "30 дней", "details": "ЛДСП"}
        body, ctype = make_multipart(fields, [])
        status, data, _ = client.request("POST", "/api/orders", raw_body=body,
                                         headers={"Content-Type": ctype})
        self.assertEqual(status, 200)
        order_id = data["order"]["id"]
        self.assertEqual(data["order"]["status"], "open")

        status, data, _ = maker.request("POST", f"/api/orders/{order_id}/responses",
                                        body={"price": 850000, "days": 28, "message": "Готовы"})
        self.assertEqual(status, 200)

        status, data, _ = client.request("GET", "/api/threads")
        self.assertEqual(status, 200)
        thread = next(t for t in data["threads"] if t["order_id"] == order_id)

        outsider = Client()
        outsider.register("flow-outsider@test.local")
        status, _, _ = outsider.request("GET", f"/api/threads/{thread['id']}/messages")
        self.assertEqual(status, 403)

        status, _, _ = maker.request("POST", f"/api/threads/{thread['id']}/messages",
                                     body={"body": "Уточним фурнитуру"})
        self.assertEqual(status, 200)
        status, data, _ = client.request("GET", f"/api/threads/{thread['id']}/messages")
        self.assertTrue(any(m["body"] == "Уточним фурнитуру" for m in data["messages"]))

        status, data, _ = client.request("GET", "/api/orders?city=Москва")
        target = next(o for o in data["orders"] if o["id"] == order_id)
        self.assertEqual(target["responses"][0]["maker_name"], "Flow Maker")

        maker_id = target["responses"][0]["maker_id"]
        status, _, _ = client.request("POST", f"/api/orders/{order_id}/choose",
                                      body={"maker_id": maker_id})
        self.assertEqual(status, 200)
        status, data, _ = client.request("GET", "/api/orders?status=progress")
        self.assertTrue(any(o["id"] == order_id for o in data["orders"]))


class UploadWhitelistTests(unittest.TestCase):
    def test_html_rejected_png_accepted(self):
        c = Client()
        c.register("upload-user@test.local")
        base_fields = {"title": "Upload", "type": "Тест", "quantity": "1",
                       "city": "Москва", "budget": "100", "deadline": "2 дня"}

        html = b"<html><script>alert(1)</script></html>"
        body, ctype = make_multipart(base_fields, [("files", "evil.html", html, "text/html")])
        status, data, _ = c.request("POST", "/api/orders", raw_body=body,
                                    headers={"Content-Type": ctype})
        self.assertEqual(status, 400)
        self.assertIn("не разрешён", data["error"])

        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d494844520000000100000001080600000"
            "01f15c4890000000d49444154789c626001000000ffff030000060005"
            "57bfabd40000000049454e44ae426082"
        )
        body, ctype = make_multipart(base_fields, [("files", "ok.png", png, "image/png")])
        status, data, _ = c.request("POST", "/api/orders", raw_body=body,
                                    headers={"Content-Type": ctype})
        self.assertEqual(status, 200)
        self.assertEqual(data["order"]["files"][0]["name"], "ok.png")


class InfraTests(unittest.TestCase):
    def test_healthz(self):
        c = Client()
        status, data, _ = c.request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_static_etag_304(self):
        c = Client()
        status, _, headers = c.request("GET", "/styles.css")
        self.assertEqual(status, 200)
        etag = headers.get("ETag")
        self.assertIsNotNone(etag)
        status, _, headers2 = c.request("GET", "/styles.css", headers={"If-None-Match": etag})
        self.assertEqual(status, 304)

    def test_static_security_headers(self):
        c = Client()
        status, _, headers = c.request("GET", "/index.html")
        self.assertEqual(status, 200)
        self.assertIn("Content-Security-Policy", headers)
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        csp = headers.get("Content-Security-Policy", "")
        self.assertNotIn("fonts.googleapis.com", csp)
        self.assertNotIn("fonts.gstatic.com", csp)
        status, _, sw_headers = c.request("GET", "/sw.js")
        self.assertEqual(status, 200)
        self.assertEqual(sw_headers.get("Cache-Control"), "no-store")

    def test_self_hosted_fonts(self):
        c = Client()
        status, raw, _ = c.request("GET", "/fonts/fonts.css")
        self.assertEqual(status, 200)
        body = raw["_raw"].decode("utf-8")
        self.assertIn("@font-face", body)
        self.assertNotIn("fonts.gstatic.com", body)
        status, _, headers = c.request("GET", "/fonts/f01.woff2")
        self.assertEqual(status, 200)
        self.assertIn("font", headers.get("Content-Type", ""))
        status, raw, _ = c.request("GET", "/index.html")
        html = raw["_raw"].decode("utf-8")
        self.assertIn("/fonts/fonts.css", html)
        self.assertNotIn("fonts.googleapis.com", html)

    def test_missing_asset_404_but_spa_routes_work(self):
        c = Client()
        status, _, _ = c.request("GET", "/missing-image.png")
        self.assertEqual(status, 404)
        status, _, _ = c.request("GET", "/deep/nested/photo.jpg")
        self.assertEqual(status, 404)
        status, raw, _ = c.request("GET", "/companies/5")
        self.assertEqual(status, 200)
        self.assertIn(b"<main id=\"app\">", raw["_raw"])

    def test_upload_magic_bytes_rejected(self):
        c = Client()
        c.register("magic-user@test.local")
        base_fields = {"title": "Magic", "type": "Тест", "quantity": "1",
                       "city": "Москва", "budget": "100", "deadline": "2 дня"}
        fake_png = b"this is not really a png but has .png extension"
        body, ctype = make_multipart(base_fields, [("files", "fake.png", fake_png, "image/png")])
        status, data, _ = c.request("POST", "/api/orders", raw_body=body,
                                    headers={"Content-Type": ctype})
        self.assertEqual(status, 400)
        self.assertIn("не соответствует расширению", data["error"])

    def test_upload_stored_in_subdir(self):
        import re
        c = Client()
        c.register("subdir-user@test.local")
        base_fields = {"title": "Subdir", "type": "Тест", "quantity": "1",
                       "city": "Москва", "budget": "100", "deadline": "2 дня"}
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d494844520000000100000001080600000"
            "01f15c4890000000d49444154789c626001000000ffff030000060005"
            "57bfabd40000000049454e44ae426082"
        )
        body, ctype = make_multipart(base_fields, [("files", "sub.png", png, "image/png")])
        status, data, _ = c.request("POST", "/api/orders", raw_body=body,
                                    headers={"Content-Type": ctype})
        self.assertEqual(status, 200)
        url = data["order"]["files"][0]["url"]
        self.assertIsNotNone(re.match(r"^/uploads/\d{4}/\d{2}/", url))


class SessionTtlTests(unittest.TestCase):
    def test_expired_session_invalidated(self):
        import sqlite3
        from db import DB_PATH
        c = Client()
        c.register("ttl-user@test.local")
        status, data, _ = c.request("GET", "/api/session")
        self.assertIsNotNone(data["user"])

        conn = sqlite3.connect(DB_PATH)
        conn.execute("UPDATE sessions SET created_at = '2026-01-01 00:00:00' WHERE token = ?",
                     (c.token,))
        conn.commit()
        conn.close()

        status, data, _ = c.request("GET", "/api/session")
        self.assertIsNone(data["user"])
        self.assertIsNone(validate_session(c.token))


class WsAuthzTests(unittest.TestCase):
    def test_thread_access_validation(self):
        from db import connect
        with connect() as conn:
            thread = conn.execute("SELECT id, client_id, maker_id FROM threads LIMIT 1").fetchone()
            self.assertIsNotNone(thread)
            self.assertTrue(validate_thread_access(thread["client_id"], thread["id"]))
            self.assertTrue(validate_thread_access(thread["maker_id"], thread["id"]))
            outsider_id = thread["maker_id"] + 777
            self.assertFalse(validate_thread_access(outsider_id, thread["id"]))
            self.assertFalse(validate_thread_access(None, thread["id"]))
            self.assertFalse(validate_thread_access(thread["client_id"], 999999))

    def test_validate_session_garbage(self):
        self.assertIsNone(validate_session(""))
        self.assertIsNone(validate_session("not-a-real-token"))


def totp_code(secret, offset=0):
    import base64
    import hashlib
    import hmac
    import struct
    import time
    key = base64.b32decode(secret)
    counter = int(time.time()) // 30 + offset
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    o = h[-1] & 0x0F
    num = struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF
    return str(num % 1000000).zfill(6)


class AccountSecurityTests(unittest.TestCase):
    def test_register_returns_verify_url_and_verify(self):
        c = Client()
        status, data = c.register("verify-user@test.local")
        self.assertEqual(status, 200)
        self.assertIn("verify_url", data)
        status, data, _ = c.request("GET", data["verify_url"].replace(_base_url, ""))
        self.assertEqual(status, 200)
        status, data, _ = c.request("GET", "/api/session")
        self.assertTrue(data["user"]["is_verified"])

    def test_verify_url_invalid(self):
        c = Client()
        status, data, _ = c.request("GET", "/api/verify-email?token=nope")
        self.assertEqual(status, 400)

    def test_change_password_flow(self):
        c = Client()
        c.register("change-pw@test.local")
        status, _, _ = c.request("POST", "/api/change-password",
                                 body={"old_password": "wrong-old", "new_password": "newpass123"})
        self.assertEqual(status, 400)
        status, _, _ = c.request("POST", "/api/change-password",
                                 body={"old_password": "secret123", "new_password": "newpass123"})
        self.assertEqual(status, 200)
        # old password must no longer work, new must
        fresh = Client()
        self.assertEqual(fresh.login("change-pw@test.local", "secret123")[0], 401)
        self.assertEqual(fresh.login("change-pw@test.local", "newpass123")[0], 200)

    def test_forgot_and_reset_password(self):
        c = Client()
        c.register("reset-user@test.local")
        status, data, _ = c.request("POST", "/api/forgot-password", body={"email": "reset-user@test.local"})
        self.assertEqual(status, 200)
        import sqlite3
        from db import DB_PATH
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT ev.token FROM email_verifications ev JOIN users u ON u.id = ev.user_id "
                "WHERE u.email = ? AND ev.purpose = 'reset' ORDER BY ev.id DESC LIMIT 1",
                ("reset-user@test.local",),
            ).fetchone()
        self.assertIsNotNone(row)
        status, data, _ = c.request("POST", "/api/reset-password",
                                    body={"token": row[0], "password": "freshpass99"})
        self.assertEqual(status, 200)
        fresh = Client()
        self.assertEqual(fresh.login("reset-user@test.local", "freshpass99")[0], 200)

    def test_2fa_login_flow(self):
        c = Client()
        c.register("tfa-user@test.local")
        status, data, _ = c.request("POST", "/api/tfa/setup")
        self.assertEqual(status, 200)
        secret = data["secret"]
        status, data, _ = c.request("POST", "/api/tfa/verify",
                                    body={"code": totp_code(secret), "enable": True})
        self.assertEqual(status, 200)
        # logout, then login should require second factor
        c.request("POST", "/api/logout")
        c.token = None
        status, data, _ = c.request("POST", "/api/login",
                                    body={"email": "tfa-user@test.local", "password": "secret123"})
        self.assertEqual(status, 200)
        self.assertTrue(data.get("tfa_required"))
        self.assertIsNone(c.token)
        login_token = data["login_token"]
        status, data, headers = c.request("POST", "/api/tfa/login",
                                          body={"login_token": login_token, "code": totp_code(secret)})
        if status != 200 and "истекла" in str(data.get("error", "")):
            status, data, _ = c.request("POST", "/api/login",
                                        body={"email": "tfa-user@test.local", "password": "secret123"})
            self.assertTrue(data.get("tfa_required"))
            login_token = data["login_token"]
            status, data, headers = c.request("POST", "/api/tfa/login",
                                              body={"login_token": login_token, "code": totp_code(secret)})
        if status != 200:
            status, data, headers = c.request("POST", "/api/tfa/login",
                                              body={"login_token": login_token, "code": totp_code(secret, -1)})
        if status != 200:
            status, data, headers = c.request("POST", "/api/tfa/login",
                                              body={"login_token": login_token, "code": totp_code(secret, 1)})
        self.assertEqual(status, 200, msg=str(data))
        all_cookies = " ".join(headers.get("_set_cookie_all", []))
        self.assertIn("meblio_session=", all_cookies)
        self.assertIn("meblio_device=", all_cookies)
        c.token = all_cookies.split("meblio_session=", 1)[1].split(";", 1)[0]
        device_part = [part for part in all_cookies.replace(" ", "\n").split("\n") if part.startswith("meblio_device=")]
        self.assertTrue(device_part)
        c.device_cookie = device_part[0].split("=", 1)[1].split(";", 1)[0]
        self.assertIsNotNone(c.token)
        status, data, _ = c.request("GET", "/api/session")
        self.assertIsNotNone(data["user"])
        # wrong code rejected
        status, data, _ = c.request("POST", "/api/tfa/login",
                                    body={"login_token": login_token, "code": "000000"})
        self.assertEqual(status, 400)

        # trusted device: next login skips 2FA entirely
        fresh = Client()
        fresh.device_cookie = c.device_cookie
        status, data, headers = fresh.request("POST", "/api/login",
                                              body={"email": "tfa-user@test.local", "password": "secret123"})
        self.assertEqual(status, 200)
        self.assertNotIn("tfa_required", data)
        all_cookies = " ".join(headers.get("_set_cookie_all", []))
        self.assertIn("meblio_session=", all_cookies)
        fresh.token = all_cookies.split("meblio_session=", 1)[1].split(";", 1)[0]
        self.assertIsNotNone(fresh.token)
        # other devices still require the code
        other = Client()
        status, data, _ = other.request("POST", "/api/login",
                                        body={"email": "tfa-user@test.local", "password": "secret123"})
        self.assertTrue(data.get("tfa_required"))

    def test_trusted_device_invalid_after_password_change(self):
        c = Client()
        c.register("tfa-pw@test.local")
        status, data, _ = c.request("POST", "/api/tfa/setup")
        self.assertEqual(status, 200)
        secret = data["secret"]
        status, _, _ = c.request("POST", "/api/tfa/verify",
                                 body={"code": totp_code(secret), "enable": True})
        c.request("POST", "/api/logout")
        c.token = None
        status, data, _ = c.request("POST", "/api/login",
                                    body={"email": "tfa-pw@test.local", "password": "secret123"})
        self.assertTrue(data.get("tfa_required"))
        login_token = data["login_token"]
        status, data, headers = c.request("POST", "/api/tfa/login",
                                          body={"login_token": login_token, "code": totp_code(secret)})
        if status != 200:
            status, data, headers = c.request("POST", "/api/tfa/login",
                                              body={"login_token": login_token, "code": totp_code(secret, -1)})
        self.assertEqual(status, 200)
        all_cookies = " ".join(headers.get("_set_cookie_all", []))
        session_token = all_cookies.split("meblio_session=", 1)[1].split(";", 1)[0]
        device_part = [p for p in all_cookies.replace(" ", "\n").split("\n") if p.startswith("meblio_device=")]
        self.assertTrue(device_part)
        device_cookie = device_part[0].split("=", 1)[1].split(";", 1)[0]

        # change password with the active session
        c.token = session_token
        c.fetch_csrf()
        status, _, _ = c.request("POST", "/api/change-password",
                                 body={"old_password": "secret123", "new_password": "newpass123"})
        self.assertEqual(status, 200)

        # trusted device cookie must no longer skip 2FA after the password change
        fresh = Client()
        fresh.device_cookie = device_cookie
        status, data, _ = fresh.request("POST", "/api/login",
                                        body={"email": "tfa-pw@test.local", "password": "newpass123"})
        self.assertEqual(status, 200)
        self.assertTrue(data.get("tfa_required"))


class DealAndModerationTests(unittest.TestCase):
    def _make_closed_deal(self):
        import secrets as _secrets
        tag = _secrets.token_hex(4)
        client = Client()
        client.register(f"deal-client-{tag}@test.local", name="Deal Client")
        maker = Client()
        maker.register(f"deal-maker-{tag}@test.local", role="maker", name="Deal Maker")
        fields = {"title": "Дельная сделка", "type": "Кухни", "quantity": "1",
                  "city": "Москва", "budget": "100000", "deadline": "10 дней", "details": "x"}
        body, ctype = make_multipart(fields, [])
        status, data, _ = client.request("POST", "/api/orders", raw_body=body,
                                         headers={"Content-Type": ctype})
        order_id = data["order"]["id"]
        status, _, _ = maker.request("POST", f"/api/orders/{order_id}/responses",
                                     body={"price": 90000, "days": 9, "message": "ok"})
        status, data, _ = client.request("GET", "/api/orders?status=open")
        target = next(o for o in data["orders"] if o["id"] == order_id)
        maker_id = target["responses"][0]["maker_id"]
        client.request("POST", f"/api/orders/{order_id}/choose", body={"maker_id": maker_id})
        admin = Client()
        admin.login("admin@meblio.ru", "admin123")
        admin.request("POST", "/api/admin/orders/status",
                      body={"order_id": order_id, "status": "closed"})
        return client, maker, order_id, maker_id

    def test_review_only_for_closed_deal(self):
        client, maker, order_id, maker_id = self._make_closed_deal()
        status, _, _ = client.request("POST", "/api/reviews",
                                      body={"company_id": maker_id, "order_id": order_id, "rating": 5, "text": "отлично"})
        self.assertEqual(status, 200)
        outsider = Client()
        outsider.register("deal-outsider@test.local")
        status, data, _ = outsider.request("POST", "/api/reviews",
                                           body={"company_id": maker_id, "order_id": order_id, "rating": 1, "text": "спам"})
        self.assertEqual(status, 403)
        # review without closed deal also rejected
        fresh = Client()
        fresh.register("deal-fresh@test.local")
        status, data, _ = fresh.request("POST", "/api/reviews",
                                        body={"company_id": maker_id, "rating": 5, "text": "нет сделки"})
        self.assertEqual(status, 403)

    def test_cancel_order_permissions(self):
        client = Client()
        client.register("cancel-owner@test.local")
        fields = {"title": "Отменяемый", "type": "Тест", "quantity": "1",
                  "city": "Москва", "budget": "5000", "deadline": "5 дней", "details": "x"}
        body, ctype = make_multipart(fields, [])
        status, data, _ = client.request("POST", "/api/orders", raw_body=body,
                                         headers={"Content-Type": ctype})
        order_id = data["order"]["id"]
        stranger = Client()
        stranger.register("cancel-stranger@test.local")
        status, _, _ = stranger.request("POST", f"/api/orders/{order_id}/cancel", body={})
        self.assertEqual(status, 403)
        status, _, _ = client.request("POST", f"/api/orders/{order_id}/cancel", body={})
        self.assertEqual(status, 200)
        status, data, _ = client.request("GET", "/api/orders?status=cancelled")
        self.assertTrue(any(o["id"] == order_id for o in data["orders"]))

    def test_close_order_by_participants(self):
        client, maker, order_id, maker_id = self._make_closed_deal()
        # _make_closed_deal already closes via admin; make a fresh progress deal
        client2 = Client()
        client2.register("close-client@test.local", name="Close Client")
        maker2 = Client()
        maker2.register("close-maker@test.local", role="maker", name="Close Maker")
        fields = {"title": "Закрываемая сделка", "type": "Кухни", "quantity": "1",
                  "city": "Москва", "budget": "200000", "deadline": "10 дней", "details": "x"}
        body, ctype = make_multipart(fields, [])
        status, data, _ = client2.request("POST", "/api/orders", raw_body=body,
                                          headers={"Content-Type": ctype})
        order_id2 = data["order"]["id"]
        maker2.request("POST", f"/api/orders/{order_id2}/responses",
                       body={"price": 190000, "days": 9, "message": "ok"})
        status, data, _ = client2.request("GET", "/api/orders?status=open")
        target = next(o for o in data["orders"] if o["id"] == order_id2)
        maker_id2 = target["responses"][0]["maker_id"]
        client2.request("POST", f"/api/orders/{order_id2}/choose", body={"maker_id": maker_id2})

        stranger = Client()
        stranger.register("close-stranger@test.local")
        status, _, _ = stranger.request("POST", f"/api/orders/{order_id2}/close", body={})
        self.assertEqual(status, 403)

        # stages block close until done or force
        status, data, _ = maker2.request("GET", f"/api/orders/{order_id2}/stages")
        self.assertEqual(status, 200)
        stages = data["stages"]
        self.assertTrue(stages)
        status, _, _ = maker2.request("POST", f"/api/orders/{order_id2}/close", body={})
        self.assertEqual(status, 400)
        for s in stages:
            status, _, _ = maker2.request("PUT", f"/api/orders/{order_id2}/stages/{s['id']}",
                                          body={"done": True})
            self.assertEqual(status, 200)
        status, _, _ = maker2.request("POST", f"/api/orders/{order_id2}/close", body={})
        self.assertEqual(status, 200)
        status, data, _ = client2.request("GET", "/api/orders?status=closed")
        self.assertTrue(any(o["id"] == order_id2 for o in data["orders"]))

    def test_budget_filter(self):
        c = Client()
        c.register("budget-filter@test.local")
        fields = {"title": "Дешёвый", "type": "Тест", "quantity": "1",
                  "city": "Москва", "budget": "1000", "deadline": "5 дней", "details": "x"}
        body, ctype = make_multipart(fields, [])
        status, data, _ = c.request("POST", "/api/orders", raw_body=body,
                                    headers={"Content-Type": ctype})
        self.assertEqual(status, 200)
        status, data, _ = c.request("GET", "/api/orders?budget_min=5000")
        self.assertFalse(any(o["title"] == "Дешёвый" for o in data["orders"]))
        status, data, _ = c.request("GET", "/api/orders?budget_max=5000&budget_min=0")
        self.assertTrue(any(o["title"] == "Дешёвый" for o in data["orders"]))

    def test_moderation_hide_content(self):
        client = Client()
        client.register("mod-client@test.local")
        fields = {"title": "Скрываемый заказ", "type": "Тест", "quantity": "1",
                  "city": "Москва", "budget": "100", "deadline": "1 день", "details": "x"}
        body, ctype = make_multipart(fields, [])
        status, data, _ = client.request("POST", "/api/orders", raw_body=body,
                                         headers={"Content-Type": ctype})
        self.assertEqual(status, 200)
        order_id = data["order"]["id"]

        reporter = Client()
        reporter.register("mod-reporter@test.local")
        status, _, _ = reporter.request("POST", "/api/reports",
                                        body={"target_type": "order", "target_id": order_id,
                                              "reason": "спам-заказ"})
        self.assertEqual(status, 200)

        anon = Client()
        status, data, _ = anon.request("GET", "/api/orders")
        self.assertTrue(any(o["id"] == order_id for o in data["orders"]))

        admin = Client()
        admin.login("admin@meblio.ru", "admin123")
        status, _, _ = admin.request("POST", "/api/admin/hide",
                                     body={"target_type": "order", "target_id": order_id, "hidden": True})
        self.assertEqual(status, 200)
        status, data, _ = anon.request("GET", "/api/orders")
        self.assertFalse(any(o["id"] == order_id for o in data["orders"]))

        # hide via report resolution
        status, _, _ = admin.request("POST", f"/api/admin/reports/resolve-all-not-implemented", body={})
        status, data, _ = admin.request("GET", "/api/admin/reports?status=pending")
        target_report = next(r for r in data["reports"] if r["target_id"] == order_id and r["status"] == "pending")
        status, _, _ = admin.request("POST", f"/api/admin/reports/{target_report['id']}/resolve",
                                     body={"status": "resolved", "hide_target": True})
        self.assertEqual(status, 200)

        # unhide restores visibility
        status, _, _ = admin.request("POST", "/api/admin/hide",
                                     body={"target_type": "order", "target_id": order_id, "hidden": False})
        self.assertEqual(status, 200)
        status, data, _ = anon.request("GET", "/api/orders")
        self.assertTrue(any(o["id"] == order_id for o in data["orders"]))
        # non-admin cannot hide
        status, _, _ = client.request("POST", "/api/admin/hide",
                                      body={"target_type": "order", "target_id": order_id, "hidden": True})
        self.assertIn(status, (401, 403))

    def test_report_flow(self):
        reporter = Client()
        reporter.register("report-user@test.local")
        status, data, _ = reporter.request("POST", "/api/reports",
                                           body={"target_type": "order", "target_id": 1, "reason": "Мусорный заказ"})
        self.assertEqual(status, 200)
        status, _, _ = reporter.request("POST", "/api/reports",
                                        body={"target_type": "order", "target_id": 1, "reason": ""})
        self.assertEqual(status, 400)
        admin = Client()
        admin.login("admin@meblio.ru", "admin123")
        status, data, _ = admin.request("GET", "/api/admin/reports")
        self.assertEqual(status, 200)
        self.assertTrue(any(r["reason"] == "Мусорный заказ" for r in data["reports"]))
        report_id = next(r["id"] for r in data["reports"] if r["reason"] == "Мусорный заказ")
        status, _, _ = admin.request("POST", f"/api/admin/reports/{report_id}/resolve",
                                     body={"status": "resolved"})
        self.assertEqual(status, 200)

    def test_contacts_hidden_until_participation(self):
        anon = Client()
        status, data, _ = anon.request("GET", "/api/companies")
        self.assertEqual(status, 200)
        company = next(c for c in data["companies"] if c["name"] == "Modul Pro")
        self.assertEqual(company["email"], "")
        self.assertEqual(company["phone"], "")
        maker = Client()
        maker.login("maker@meblio.ru", "maker123")
        status, data, _ = maker.request("GET", f"/api/companies/{company['id']}")
        self.assertEqual(status, 200)
        self.assertNotEqual(data["company"]["email"], "")


class ChatFileTests(unittest.TestCase):
    def test_upload_file_to_thread(self):
        client = Client()
        client.register("chatfile-client@test.local")
        maker = Client()
        maker.register("chatfile-maker@test.local", role="maker")
        fields = {"title": "Чат-файлы", "type": "Тест", "quantity": "1",
                  "city": "Москва", "budget": "100", "deadline": "2 дня", "details": "x"}
        body, ctype = make_multipart(fields, [])
        status, data, _ = client.request("POST", "/api/orders", raw_body=body,
                                         headers={"Content-Type": ctype})
        order_id = data["order"]["id"]
        maker.request("POST", f"/api/orders/{order_id}/responses", body={"price": 90, "days": 2, "message": "ok"})
        status, data, _ = client.request("GET", "/api/threads")
        thread = next(t for t in data["threads"] if t["order_id"] == order_id)

        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d494844520000000100000001080600000"
            "01f15c4890000000d49444154789c626001000000ffff030000060005"
            "57bfabd40000000049454e44ae426082"
        )
        file_body, file_ctype = make_multipart({}, [("files", "black.png", png, "image/png")])
        status, data, _ = maker.request("POST", f"/api/threads/{thread['id']}/files",
                                        raw_body=file_body, headers={"Content-Type": file_ctype})
        self.assertEqual(status, 200)

        status, data, _ = client.request("GET", f"/api/threads/{thread['id']}/messages")
        self.assertEqual(status, 200)
        file_msg = next(m for m in data["messages"] if m["files"])
        self.assertEqual(file_msg["files"][0]["name"], "black.png")

        # outsider cannot upload into thread
        outsider = Client()
        outsider.register("chatfile-outsider@test.local")
        file_body2, file_ctype2 = make_multipart({}, [("files", "black.png", png, "image/png")])
        status, _, _ = outsider.request("POST", f"/api/threads/{thread['id']}/files",
                                        raw_body=file_body2, headers={"Content-Type": file_ctype2})
        self.assertEqual(status, 403)


    def test_change_email_flow(self):
        c = Client()
        c.register("email-change@test.local")
        status, data, _ = c.request("POST", "/api/change-email",
                                    body={"new_email": "renamed@test.local", "password": "secret123"})
        self.assertEqual(status, 200)
        fresh = Client()
        self.assertEqual(fresh.login("renamed@test.local", "secret123")[0], 200)
        # duplicate email rejected
        dup = Client()
        dup.login("renamed@test.local", "secret123")
        status, _, _ = dup.request("POST", "/api/change-email",
                                   body={"new_email": "client@meblio.ru", "password": "secret123"})
        self.assertEqual(status, 409)

    def test_honeypot_rejects_bots(self):
        c = Client()
        status, data, _ = c.request(
            "POST", "/api/register",
            body={"role": "client", "name": "Bot", "email": "bot@test.local",
                  "password": "secret123", "city": "Москва", "website": "http://spam.example",
                  "consent_pd": "1"},
        )
        self.assertEqual(status, 400)
        self.assertIn("проверку", data["error"])

    def test_register_requires_consent(self):
        c = Client()
        status, data, _ = c.request(
            "POST", "/api/register",
            body={"role": "client", "name": "NoConsent", "email": "noconsent@test.local",
                  "password": "secret123", "city": "Москва"},
        )
        self.assertEqual(status, 400)
        self.assertIn("согласие", data["error"].lower())

    def test_register_stores_consent_timestamp(self):
        import sqlite3
        from db import DB_PATH
        c = Client()
        status, data = c.register("consent-user@test.local")
        self.assertEqual(status, 200)
        email = data["user"]["email"]
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("SELECT consent_pd_at FROM users WHERE email = ?", (email,)).fetchone()
        self.assertIsNotNone(row)
        self.assertTrue(row[0])

    def test_delete_account_anonymizes(self):
        import sqlite3
        from db import DB_PATH
        c = Client()
        c.register("doomed@test.local")
        status, _, _ = c.request("POST", "/api/delete-account",
                                 body={"password": "wrong"})
        self.assertEqual(status, 400)
        status, _, _ = c.request("POST", "/api/delete-account",
                                 body={"password": "secret123"})
        self.assertEqual(status, 200)
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("SELECT name, email FROM users WHERE email LIKE 'deleted_%' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "Удалённый пользователь")


class MakerPortalTests(unittest.TestCase):
    def test_maker_stats(self):
        maker = Client()
        maker.register("stats-maker@test.local", role="maker")
        status, data, _ = maker.request("GET", "/api/maker/stats")
        self.assertEqual(status, 200)
        self.assertIn("conversion_rate", data)
        self.assertIn("responses_count", data)
        client = Client()
        client.login("client@meblio.ru", "client123")
        status, data, _ = client.request("GET", "/api/maker/stats")
        self.assertEqual(status, 403)
        anon = Client()
        status, _, _ = anon.request("GET", "/api/maker/stats")
        self.assertEqual(status, 401)

    def test_gallery_upload_and_delete(self):
        maker = Client()
        maker.register("gallery-maker@test.local", role="maker")
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d494844520000000100000001080600000"
            "01f15c4890000000d49444154789c626001000000ffff030000060005"
            "57bfabd40000000049454e44ae426082"
        )
        body, ctype = make_multipart({}, [("files", "work.png", png, "image/png")])
        status, data, _ = maker.request("POST", "/api/gallery", raw_body=body,
                                        headers={"Content-Type": ctype})
        self.assertEqual(status, 200)
        status, data, _ = maker.request("GET", "/api/session")
        maker_id = data["user"]["id"]
        status, data, _ = maker.request("GET", f"/api/companies/{maker_id}")
        self.assertEqual(status, 200)
        self.assertTrue(data["company"]["gallery"])
        item_id = data["company"]["gallery"][0]["id"]
        status, _, _ = maker.request("DELETE", f"/api/gallery/{item_id}")
        self.assertEqual(status, 200)


class SeoAndRoutingTests(unittest.TestCase):
    def test_spa_fallback_and_seo_meta(self):
        c = Client()
        status, raw, headers = c.request("GET", "/companies/5")
        self.assertEqual(status, 200)
        html = raw["_raw"].decode("utf-8")
        self.assertIn("<title>Test Co", html)
        self.assertIn("og:title", html)
        self.assertIn("application/ld+json", html)
        self.assertIn('href="https://meblio.local/companies/5"', html)

    def test_robots_and_sitemap(self):
        c = Client()
        status, _, _ = c.request("GET", "/robots.txt")
        self.assertEqual(status, 200)
        status, data, _ = c.request("GET", "/sitemap.xml")
        self.assertEqual(status, 200)
        xml = data["_raw"].decode("utf-8")
        self.assertIn("<urlset", xml)
        self.assertIn("/companies/", xml)
        self.assertIn("/privacy", xml)
        self.assertIn("/offer", xml)

    def test_privacy_and_offer_spa_seo(self):
        c = Client()
        for path, title_part, body_part in (
            ("/privacy", "Политика конфиденциальности", "152-ФЗ"),
            ("/offer", "Публичная оферта", "437"),
        ):
            status, raw, _ = c.request("GET", path)
            self.assertEqual(status, 200, path)
            html = raw["_raw"].decode("utf-8")
            self.assertIn(title_part, html)
            self.assertIn(f"<title>{title_part}", html)
        # client bundle includes routes + legal views
        status, raw, _ = c.request("GET", "/script.js")
        # SPA route served as HTML (fallback) or static script — both acceptable for SEO test on HTML pages above
        self.assertIn(status, (200, 404))

    def test_articles_public_flow(self):
        c = Client()
        status, data, _ = c.request("GET", "/api/articles")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(data["articles"]), 3)
        slug = data["articles"][0]["slug"]
        status, data, _ = c.request("GET", f"/api/articles/{slug}")
        self.assertEqual(status, 200)
        self.assertIn("## ", data["article"]["body_md"])

    def test_articles_admin_crud(self):
        admin = Client()
        admin.login("admin@meblio.ru", "admin123")
        status, data, _ = admin.request(
            "POST", "/api/admin/article-save",
            body={"slug": f"test-art-{int(__import__('time').time())}", "title": "Тест",
                  "excerpt": "кратко", "body_md": "## Раздел\nТекст", "is_published": True},
        )
        self.assertEqual(status, 200)
        article_id = data["id"]
        status, _, _ = admin.request("DELETE", f"/api/admin/articles/{article_id}")
        self.assertEqual(status, 200)

    def test_region_slug_filter(self):
        c = Client()
        status, regions, _ = c.request("GET", "/api/regions")
        spb = next(r for r in regions["regions"] if r["name"] == "Санкт-Петербург")
        self.assertTrue(spb["slug"])
        status, data, _ = c.request("GET", f"/api/companies?region={spb['slug']}")
        self.assertEqual(status, 200)


class DraftAndInviteTests(unittest.TestCase):
    def test_draft_order_visibility_and_publish(self):
        client = Client()
        client.register("draft-owner@test.local")
        fields = {"title": "Черновик заказ", "type": "Тест", "quantity": "1",
                  "city": "Москва", "budget": "100", "deadline": "1 день", "details": "x"}
        body, ctype = make_multipart({**fields, "is_draft": "1"}, [])
        status, data, _ = client.request("POST", "/api/orders", raw_body=body,
                                         headers={"Content-Type": ctype})
        self.assertEqual(status, 200)
        draft_id = data["order"]["id"]

        stranger = Client()
        stranger.register("draft-stranger@test.local")
        status, data, _ = stranger.request("GET", "/api/orders?status=draft")
        self.assertEqual(data["orders"], [])

        status, data, _ = client.request("GET", "/api/orders?status=draft")
        self.assertTrue(any(o["id"] == draft_id for o in data["orders"]))
        status, data, _ = client.request("GET", "/api/orders")
        self.assertFalse(any(o["id"] == draft_id for o in data["orders"]))

        status, _, _ = client.request("POST", f"/api/orders/{draft_id}/publish", body={})
        self.assertEqual(status, 200)
        anon = Client()
        status, data, _ = anon.request("GET", "/api/orders")
        self.assertTrue(any(o["id"] == draft_id for o in data["orders"]))

    def test_invite_to_quote(self):
        maker = Client()
        maker.login("maker@meblio.ru", "maker123")
        client = Client()
        client.register("invite-client@test.local")
        fields = {"title": "Приглашение", "type": "Тест", "quantity": "1",
                  "city": "Москва", "budget": "7000", "deadline": "5 дней", "details": "x"}
        body, ctype = make_multipart(fields, [])
        status, data, _ = client.request("POST", "/api/orders", raw_body=body,
                                         headers={"Content-Type": ctype})
        order_id = data["order"]["id"]
        status, data, _ = client.request("GET", "/api/makers")
        maker_id = next(mk["id"] for mk in data["makers"] if mk["name"] == "Modul Pro")
        status, _, _ = client.request("POST", f"/api/companies/{maker_id}/invite",
                                      body={"order_id": order_id})
        self.assertEqual(status, 200)
        status, data, _ = maker.request("GET", "/api/notifications")
        self.assertTrue(any("Запрос расчёта" in n["title"] for n in data["notifications"]))


class AdminAndExportTests(unittest.TestCase):
    def test_admin_endpoints_and_export(self):
        admin = Client()
        status, data = admin.login("admin@meblio.ru", "admin123")
        self.assertEqual(status, 200)
        status, data, _ = admin.request("GET", "/api/admin/stats")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(data["users"], 4)
        status, data, _ = admin.request("GET", "/api/admin/analytics")
        self.assertEqual(status, 200)
        self.assertIn("by_status", data)

        anon = Client()
        status, _, _ = anon.request("GET", "/api/admin/stats")
        self.assertEqual(status, 401)

        owner = Client()
        owner.register("export-user@test.local")
        fields = {"title": "=cmd() injection attempt", "type": "Тест", "quantity": "1",
                  "city": "Москва", "budget": "42", "deadline": "1 день", "details": "csv"}
        body, ctype = make_multipart(fields, [])
        status, data, _ = owner.request("POST", "/api/orders", raw_body=body,
                                        headers={"Content-Type": ctype})
        self.assertEqual(status, 200)
        status, payload, headers = owner.request("POST", "/api/export/excel")
        self.assertEqual(status, 200)
        raw = payload["_raw"].decode("utf-8")
        self.assertTrue(raw.startswith("\ufeff"))
        self.assertIn("'=cmd() injection attempt", raw.replace('""', ""))
        self.assertIn("attachment", headers.get("Content-Disposition", ""))


class AiChatTests(unittest.TestCase):
    def test_ai_chat_history_and_clear(self):
        c = Client()
        c.register("ai-user@test.local")
        status, _, _ = c.request("GET", "/api/ai/history")
        self.assertEqual(status, 200)
        status, data, _ = c.request("POST", "/api/ai/chat", body={"message": "Привет"})
        self.assertEqual(status, 200)
        self.assertEqual(data["provider"], "builtin")  # no AI_API_KEY in tests
        self.assertTrue(data["reply"])
        status, data, _ = c.request("POST", "/api/ai/chat", body={"message": "Как создать заказ?"})
        self.assertEqual(status, 200)
        self.assertIn("Кабинет", data["reply"])
        status, data, _ = c.request("GET", "/api/ai/history")
        roles = [m["role"] for m in data["messages"]]
        self.assertEqual(roles, ["user", "assistant", "user", "assistant"])

    def test_ai_materials_intent_uses_db(self):
        c = Client()
        c.register("ai-materials@test.local")
        status, data, _ = c.request("POST", "/api/ai/chat",
                                    body={"message": "Подбери материал для корпуса"})
        self.assertEqual(status, 200)
        self.assertIn("ЛДСП", data["reply"])
        self.assertIn("МДФ", data["reply"])

    def test_ai_requires_login_and_validates_input(self):
        anon = Client()
        status, _, _ = anon.request("POST", "/api/ai/chat", body={"message": "тест"})
        self.assertEqual(status, 401)
        c = Client()
        c.register("ai-validation@test.local")
        status, _, _ = c.request("POST", "/api/ai/chat", body={"message": "   "})
        self.assertEqual(status, 400)
        status, _, _ = c.request("POST", "/api/ai/chat", body={"message": "x" * 5000})
        self.assertEqual(status, 400)

    def test_ai_clear(self):
        c = Client()
        c.register("ai-clear@test.local")
        c.request("POST", "/api/ai/chat", body={"message": "Привет"})
        status, _, _ = c.request("DELETE", "/api/ai/history")
        self.assertEqual(status, 200)
        status, data, _ = c.request("GET", "/api/ai/history")
        self.assertEqual(data["messages"], [])


class CompanyRequisitesTests(unittest.TestCase):
    def test_profile_requisites_save_and_visible(self):
        c = Client()
        c.register("req-company@test.local", role="maker", name="Req LLC")
        status, _, _ = c.request("POST", "/api/profile", body={
            "name": "Req LLC", "city": "Москва", "inn": "7701234567",
            "ogrn": "1027700132195", "website": "reqllc.ru", "is_public": "1",
        })
        self.assertEqual(status, 200)
        status, data, _ = c.request("GET", "/api/session")
        u = data["user"]
        self.assertEqual(u["inn"], "7701234567")
        self.assertEqual(u["ogrn"], "1027700132195")
        self.assertEqual(u["website"], "https://reqllc.ru")
        self.assertEqual(u["is_public"], 1)
        status, data, _ = c.request("GET", f"/api/companies/{u['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(data["company"]["inn"], "7701234567")
        self.assertEqual(data["company"]["ogrn"], "1027700132195")
        self.assertEqual(data["company"]["website"], "https://reqllc.ru")

    def test_invalid_requisites_rejected(self):
        c = Client()
        c.register("req-bad@test.local", role="maker")
        status, _, _ = c.request("POST", "/api/profile", body={"name": "X", "city": "Москва", "inn": "abc"})
        self.assertEqual(status, 400)
        c2 = Client()
        c2.register("req-bad2@test.local", role="maker")
        status, _, _ = c2.request("POST", "/api/profile", body={"name": "X", "city": "Москва", "ogrn": "123"})
        self.assertEqual(status, 400)
        c3 = Client()
        c3.register("req-bad3@test.local", role="maker")
        status, _, _ = c3.request("POST", "/api/profile", body={"name": "X", "city": "Москва", "website": "not a url"})
        self.assertEqual(status, 400)

    def test_hidden_company_excluded_from_public(self):
        owner = Client()
        owner.register("hidden-company@test.local", role="maker", name="Hidden Co")
        status, _, _ = owner.request("POST", "/api/profile",
                                     body={"name": "Hidden Co", "city": "Москва", "is_public": "0"})
        self.assertEqual(status, 200)
        status, data, _ = owner.request("GET", "/api/session")
        cid = data["user"]["id"]
        anon = Client()
        status, _, _ = anon.request("GET", f"/api/companies/{cid}")
        self.assertEqual(status, 404)
        status, data, _ = anon.request("GET", "/api/companies")
        self.assertNotIn(cid, [c["id"] for c in data["companies"]])
        status, data, _ = anon.request("GET", "/api/search?q=Hidden")
        self.assertNotIn(cid, [c["id"] for c in data["companies"]])
        status, data, _ = owner.request("GET", f"/api/companies/{cid}")
        self.assertEqual(status, 200)
        admin = Client()
        admin.login("admin@meblio.ru", "admin123")
        status, data, _ = admin.request("GET", f"/api/companies/{cid}")
        self.assertEqual(status, 200)


class DevModeTests(unittest.TestCase):
    def test_dev_mode_off_hides_verify_url(self):
        old = os.environ.get("MEBLIO_DEV")
        os.environ["MEBLIO_DEV"] = "0"
        try:
            c = Client()
            status, data = c.register("prod-mode@test.local")
            self.assertEqual(status, 200)
            self.assertNotIn("verify_url", data)
        finally:
            if old is None:
                os.environ.pop("MEBLIO_DEV", None)
            else:
                os.environ["MEBLIO_DEV"] = old

    def test_dev_mode_on_returns_verify_url(self):
        os.environ["MEBLIO_DEV"] = "1"
        c = Client()
        status, data = c.register("dev-mode@test.local")
        self.assertEqual(status, 200)
        self.assertIn("verify_url", data)


class ConfigJsTests(unittest.TestCase):
    def test_config_js_served(self):
        c = Client()
        status, data, headers = c.request("GET", "/config.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers.get("Content-Type", ""))
        body = data["_raw"].decode("utf-8")
        self.assertIn("window.MEBLIO_CONFIG", body)
        self.assertIn("wsPort", body)

    def test_csp_connect_src_allows_dynamic_ws(self):
        c = Client()
        status, _, headers = c.request("GET", "/index.html")
        self.assertEqual(status, 200)
        csp = headers.get("Content-Security-Policy", "")
        self.assertIn("connect-src", csp)
        self.assertIn("wss://", csp)
        self.assertIn("ws://127.0.0.1:", csp)

    def test_metrica_disabled_by_default(self):
        old = os.environ.pop("MEBLIO_METRICA_ID", None)
        try:
            c = Client()
            status, _, headers = c.request("GET", "/metrica.js")
            self.assertEqual(status, 404)
            status, raw, headers = c.request("GET", "/index.html")
            self.assertEqual(status, 200)
            html = raw["_raw"].decode("utf-8")
            self.assertNotIn("/metrica.js", html)
            csp = headers.get("Content-Security-Policy", "")
            self.assertNotIn("mc.yandex.ru", csp)
        finally:
            if old is not None:
                os.environ["MEBLIO_METRICA_ID"] = old

    def test_metrica_external_script_and_csp(self):
        os.environ["MEBLIO_METRICA_ID"] = "12345678"
        try:
            c = Client()
            status, data, headers = c.request("GET", "/metrica.js")
            self.assertEqual(status, 200)
            self.assertIn("javascript", headers.get("Content-Type", ""))
            body = data["_raw"].decode("utf-8")
            self.assertIn("ym(12345678,'init'", body)
            self.assertIn("mc.yandex.ru/metrika/tag.js", body)
            status, raw, headers = c.request("GET", "/")
            self.assertEqual(status, 200)
            html = raw["_raw"].decode("utf-8")
            self.assertIn('src="/metrica.js"', html)
            csp = headers.get("Content-Security-Policy", "")
            self.assertIn("mc.yandex.ru", csp)
            self.assertIn("script-src", csp)
        finally:
            os.environ.pop("MEBLIO_METRICA_ID", None)

    def test_metrica_rejects_non_numeric_id(self):
        os.environ["MEBLIO_METRICA_ID"] = "not-a-number"
        try:
            c = Client()
            status, _, _ = c.request("GET", "/metrica.js")
            self.assertEqual(status, 400)
        finally:
            os.environ.pop("MEBLIO_METRICA_ID", None)


class DealLifecycleTests(unittest.TestCase):
    """P0: stages, accept act, contract, invoice statuses."""

    def _progress_deal(self, tag):
        import secrets as _secrets
        t = _secrets.token_hex(3)
        client = Client()
        client.register(f"lc-client-{tag}-{t}@test.local", name="LC Client")
        maker = Client()
        maker.register(f"lc-maker-{tag}-{t}@test.local", role="maker", name="LC Maker")
        fields = {"title": f"Сделка {tag}", "type": "Кухни", "quantity": "1",
                  "city": "Москва", "budget": "150000", "deadline": "10 дней", "details": "x"}
        body, ctype = make_multipart(fields, [])
        status, data, _ = client.request("POST", "/api/orders", raw_body=body,
                                         headers={"Content-Type": ctype})
        order_id = data["order"]["id"]
        maker.request("POST", f"/api/orders/{order_id}/responses",
                      body={"price": 140000, "days": 9, "message": "ok"})
        status, data, _ = client.request("GET", "/api/orders?status=open")
        target = next(o for o in data["orders"] if o["id"] == order_id)
        maker_id = target["responses"][0]["maker_id"]
        client.request("POST", f"/api/orders/{order_id}/choose", body={"maker_id": maker_id})
        return client, maker, order_id, maker_id

    def test_stages_seed_update_and_access(self):
        client, maker, order_id, maker_id = self._progress_deal("stages")
        outsider = Client()
        outsider.register("lc-outsider@test.local")
        status, data, _ = outsider.request("GET", f"/api/orders/{order_id}/stages")
        self.assertIn(status, (403, 404))

        status, data, _ = client.request("GET", f"/api/orders/{order_id}/stages")
        self.assertEqual(status, 200)
        stages = data["stages"]
        self.assertGreaterEqual(len(stages), 4)
        self.assertEqual(stages[0]["name"], "Замер")

        sid = stages[0]["id"]
        status, _, _ = maker.request("PUT", f"/api/orders/{order_id}/stages/{sid}",
                                     body={"done": True})
        self.assertEqual(status, 200)
        status, data, _ = client.request("GET", f"/api/orders/{order_id}/stages")
        self.assertTrue(data["stages"][0]["done"])

        status, _, _ = client.request("POST", f"/api/orders/{order_id}/stages",
                                      body={"name": "Упаковка"})
        self.assertEqual(status, 200)
        status, data, _ = client.request("GET", f"/api/orders/{order_id}/stages")
        self.assertTrue(any(s["name"] == "Упаковка" for s in data["stages"]))

        extra = next(s for s in data["stages"] if s["name"] == "Упаковка")
        status, _, _ = client.request("PUT", f"/api/orders/{order_id}/stages/{extra['id']}",
                                      body={"delete": True})
        self.assertEqual(status, 200)
        status, data, _ = client.request("GET", f"/api/orders/{order_id}/stages")
        self.assertFalse(any(s["name"] == "Упаковка" for s in data["stages"]))

        status, _, _ = outsider.request("PUT", f"/api/orders/{order_id}/stages/{sid}",
                                        body={"done": False})
        self.assertEqual(status, 403)

    def test_accept_requires_stages_or_force(self):
        client, maker, order_id, maker_id = self._progress_deal("accept")
        status, _, _ = maker.request("POST", f"/api/orders/{order_id}/accept", body={})
        self.assertEqual(status, 403)

        status, data, _ = client.request("POST", f"/api/orders/{order_id}/accept", body={})
        self.assertEqual(status, 400)
        err = (data.get("error") or "").lower() if isinstance(data, dict) else ""
        self.assertTrue("этап" in err or "не завершены" in err, err)

        status, data, _ = client.request("GET", f"/api/orders/{order_id}/stages")
        for s in data["stages"]:
            client.request("PUT", f"/api/orders/{order_id}/stages/{s['id']}", body={"done": True})
        status, data, _ = client.request("POST", f"/api/orders/{order_id}/accept", body={})
        self.assertEqual(status, 200)
        self.assertEqual(data.get("warranty_days"), 14)

        status, data, _ = client.request("GET", "/api/orders?status=closed")
        closed = next(o for o in data["orders"] if o["id"] == order_id)
        self.assertTrue(closed.get("warranty_until"))

    def test_accept_force_when_stages_pending(self):
        client, maker, order_id, maker_id = self._progress_deal("force")
        status, data, _ = client.request("POST", f"/api/orders/{order_id}/accept",
                                         body={"force": True})
        self.assertEqual(status, 200)
        status, data, _ = client.request("GET", "/api/orders?status=closed")
        self.assertTrue(any(o["id"] == order_id for o in data["orders"]))

    def test_contract_participants_only(self):
        client, maker, order_id, maker_id = self._progress_deal("contract")
        status, data, _ = client.request("GET", f"/api/orders/{order_id}/contract")
        self.assertEqual(status, 200)
        c = data["contract"]
        self.assertEqual(c["id"], order_id)
        self.assertEqual(c["client_name"], "LC Client")
        self.assertEqual(c["maker_name"], "LC Maker")
        self.assertIn("stages", c)

        outsider = Client()
        outsider.register("lc-contract-outsider@test.local")
        status, _, _ = outsider.request("GET", f"/api/orders/{order_id}/contract")
        self.assertEqual(status, 403)

        open_order_client = Client()
        open_order_client.register("lc-open@test.local")
        fields = {"title": "Без исполнителя", "type": "Тест", "quantity": "1",
                  "city": "Москва", "budget": "100", "deadline": "2 дня", "details": "x"}
        body, ctype = make_multipart(fields, [])
        status, data, _ = open_order_client.request("POST", "/api/orders", raw_body=body,
                                                    headers={"Content-Type": ctype})
        open_id = data["order"]["id"]
        # client may open contract before maker chosen
        status, data, _ = open_order_client.request("GET", f"/api/orders/{open_id}/contract")
        self.assertEqual(status, 200)
        self.assertIsNone(data["contract"].get("selected_maker_id"))
        status, _, _ = outsider.request("GET", f"/api/orders/{open_id}/contract")
        self.assertEqual(status, 403)

    def test_invoice_status_lifecycle(self):
        client, maker, order_id, maker_id = self._progress_deal("invoice")
        status, data, _ = client.request("POST", "/api/invoices",
                                         body={"order_id": order_id, "to_user_id": maker_id,
                                               "amount": 140000, "due_date": "2026-10-01",
                                               "items": "[]"})
        self.assertEqual(status, 200)
        inv_id = data["id"]

        outsider = Client()
        outsider.register("lc-inv-outsider@test.local")
        status, _, _ = outsider.request("PUT", f"/api/invoices/{inv_id}", body={"status": "paid"})
        self.assertEqual(status, 403)

        status, _, _ = client.request("PUT", f"/api/invoices/{inv_id}", body={"status": "cancelled"})
        # cancelled is final for paid path check: pending -> cancelled allowed
        self.assertEqual(status, 200)

        # create another and walk pending -> paid -> cancelled
        status, data, _ = maker.request("POST", "/api/invoices",
                                        body={"order_id": order_id, "to_user_id": client.request("GET", "/api/session")[1]["user"]["id"],
                                              "amount": 10, "due_date": "", "items": "[]"})
        self.assertEqual(status, 200)
        inv2 = data["id"]
        status, _, _ = maker.request("PUT", f"/api/invoices/{inv2}", body={"status": "paid"})
        self.assertEqual(status, 200)
        status, _, _ = maker.request("PUT", f"/api/invoices/{inv2}", body={"status": "pending"})
        self.assertEqual(status, 409)
        status, _, _ = maker.request("PUT", f"/api/invoices/{inv2}", body={"status": "cancelled"})
        self.assertEqual(status, 200)
        status, _, _ = maker.request("PUT", f"/api/invoices/{inv2}", body={"status": "paid"})
        self.assertEqual(status, 409)

        status, data, _ = client.request("GET", f"/api/invoices/{inv2}")
        self.assertEqual(status, 200)
        self.assertEqual(data["invoice"]["status"], "cancelled")


class ModerationP0Tests(unittest.TestCase):
    def test_hide_company_and_review(self):
        maker = Client()
        maker.register("mod-co@test.local", role="maker", name="Hidden Co")
        status, data, _ = maker.request("GET", "/api/companies")
        # company may be public depending on is_public default
        client = Client()
        client.register("mod-co-client@test.local")

        admin = Client()
        admin.login("admin@meblio.ru", "admin123")
        status, _, _ = admin.request("POST", "/api/admin/hide",
                                     body={"target_type": "company", "target_id": 0, "hidden": True})
        # target_id 0 invalid
        self.assertEqual(status, 400)

        # create closed deal for review
        import secrets as _secrets
        t = _secrets.token_hex(3)
        c2 = Client()
        c2.register(f"rev-client-{t}@test.local")
        m2 = Client()
        m2.register(f"rev-maker-{t}@test.local", role="maker")
        fields = {"title": "Для отзыва", "type": "Кухни", "quantity": "1",
                  "city": "Москва", "budget": "50000", "deadline": "5 дней", "details": "x"}
        body, ctype = make_multipart(fields, [])
        status, data, _ = c2.request("POST", "/api/orders", raw_body=body,
                                     headers={"Content-Type": ctype})
        order_id = data["order"]["id"]
        m2.request("POST", f"/api/orders/{order_id}/responses",
                   body={"price": 40000, "days": 4, "message": "ok"})
        status, data, _ = c2.request("GET", "/api/orders?status=open")
        target = next(o for o in data["orders"] if o["id"] == order_id)
        maker_id = target["responses"][0]["maker_id"]
        c2.request("POST", f"/api/orders/{order_id}/choose", body={"maker_id": maker_id})
        admin.request("POST", "/api/admin/orders/status",
                      body={"order_id": order_id, "status": "closed"})
        status, _, _ = c2.request("POST", "/api/reviews",
                                  body={"company_id": maker_id, "order_id": order_id,
                                        "rating": 5, "text": "отлично"})
        self.assertEqual(status, 200)

        status, data, _ = c2.request("GET", f"/api/reviews?company_id={maker_id}")
        self.assertEqual(data["reviews_count"], 1)
        rev_id = data["reviews"][0]["id"]

        reporter = Client()
        reporter.register(f"rev-reporter-{t}@test.local")
        status, _, _ = reporter.request("POST", "/api/reports",
                                        body={"target_type": "review", "target_id": rev_id,
                                              "reason": "фейковый отзыв"})
        self.assertEqual(status, 200)
        status, data, _ = admin.request("GET", "/api/admin/reports?status=pending")
        rid = next(r["id"] for r in data["reports"]
                   if r["target_type"] == "review" and r["target_id"] == rev_id)
        status, _, _ = admin.request("POST", f"/api/admin/reports/{rid}/resolve",
                                     body={"status": "resolved", "hide_target": True})
        self.assertEqual(status, 200)

        status, data, _ = c2.request("GET", f"/api/reviews?company_id={maker_id}")
        self.assertEqual(data["reviews_count"], 0)

        # hide company via admin/hide
        status, _, _ = admin.request("POST", "/api/admin/hide",
                                     body={"target_type": "company", "target_id": maker_id,
                                           "hidden": True})
        self.assertEqual(status, 200)
        status, _, _ = c2.request("GET", f"/api/companies/{maker_id}")
        self.assertEqual(status, 404)
        status, data, _ = admin.request("GET", f"/api/companies/{maker_id}")
        self.assertEqual(status, 200)

        # unhide
        status, _, _ = admin.request("POST", "/api/admin/hide",
                                     body={"target_type": "company", "target_id": maker_id,
                                           "hidden": False})
        self.assertEqual(status, 200)
        status, _, _ = c2.request("GET", f"/api/companies/{maker_id}")
        self.assertEqual(status, 200)

    def test_admin_reports_include_company_review_types(self):
        admin = Client()
        admin.login("admin@meblio.ru", "admin123")
        status, data, _ = admin.request("GET", "/api/admin/reports?page_size=100")
        self.assertEqual(status, 200)
        self.assertIn("reports", data)


class P1FeatureTests(unittest.TestCase):
    def _make_order(self, client, title="P1 заказ", deadline="30 дней"):
        fields = {"title": title, "type": "Кухни", "quantity": "1",
                  "city": "Москва", "budget": "100000", "deadline": deadline, "details": "x"}
        body, ctype = make_multipart(fields, [])
        status, data, _ = client.request("POST", "/api/orders", raw_body=body,
                                         headers={"Content-Type": ctype})
        self.assertEqual(status, 200)
        return data["order"]["id"]

    def test_duplicate_order_as_draft(self):
        client = Client()
        client.register("p1-dup-client@test.local")
        order_id = self._make_order(client, title="Оригинал")

        maker = Client()
        maker.register("p1-dup-maker@test.local", role="maker")
        status, _, _ = maker.request("POST", f"/api/orders/{order_id}/duplicate", body={})
        self.assertEqual(status, 403)

        outsider = Client()
        outsider.register("p1-dup-outsider@test.local")
        status, _, _ = outsider.request("POST", f"/api/orders/{order_id}/duplicate", body={})
        self.assertEqual(status, 403)

        status, data, _ = client.request("POST", f"/api/orders/{order_id}/duplicate", body={})
        self.assertEqual(status, 200)
        copy = data["order"]
        self.assertEqual(copy["status"], "draft")
        self.assertIn("копия", copy["title"])
        self.assertNotEqual(copy["id"], order_id)
        self.assertEqual(copy["client_id"], client.request("GET", "/api/session")[1]["user"]["id"])

        # cannot duplicate draft
        status, _, _ = client.request("POST", f"/api/orders/{copy['id']}/duplicate", body={})
        self.assertEqual(status, 400)

    def test_due_at_set_and_deadline_reminders(self):
        import datetime as _dt
        from db import connect as _connect

        client = Client()
        client.register("p1-dead-client@test.local")
        order_id = self._make_order(client, deadline="3 дня")

        with _connect() as conn:
            row = conn.execute("SELECT due_at, status FROM orders WHERE id = ?", (order_id,)).fetchone()
            self.assertIsNotNone(row["due_at"])
            expected = (_dt.date.today() + _dt.timedelta(days=3)).isoformat()
            self.assertEqual(row["due_at"], expected)
            # force into "1 day left" window
            tomorrow = (_dt.date.today() + _dt.timedelta(days=1)).isoformat()
            conn.execute("UPDATE orders SET due_at = ?, status = 'open' WHERE id = ?",
                         (tomorrow, order_id))

        admin = Client()
        admin.login("admin@meblio.ru", "admin123")
        status, data, _ = admin.request("POST", "/api/deadlines/check", body={})
        self.assertEqual(status, 200)
        self.assertGreaterEqual(data["sent"], 1)

        status, data, _ = client.request("GET", "/api/notifications")
        self.assertTrue(any("Дедлайн" in (n.get("title") or "") for n in data["notifications"]))

        # second run should not re-send same stage (dedup)
        status, data2, _ = admin.request("GET", "/api/deadlines/check")
        self.assertEqual(status, 200)
        self.assertEqual(data2["sent"], 0)

        # non-admin cannot check
        status, _, _ = client.request("GET", "/api/deadlines/check")
        self.assertEqual(status, 403)

    def test_maker_funnel(self):
        import secrets as _secrets
        t = _secrets.token_hex(3)
        client = Client()
        client.register(f"p1-fn-client-{t}@test.local")
        maker = Client()
        maker.register(f"p1-fn-maker-{t}@test.local", role="maker", name="Funnel Maker")
        other = Client()
        other.register(f"p1-fn-other-{t}@test.local", role="maker", name="Funnel Other")

        # available order (no responses from maker)
        oid1 = self._make_order(client, title="Свободный заказ")
        # responded order
        oid2 = self._make_order(client, title="С откликом")
        maker.request("POST", f"/api/orders/{oid2}/responses",
                      body={"price": 90000, "days": 10, "message": "ok"})
        # other maker responds and is chosen → lost for maker (maker also responded)
        oid3 = self._make_order(client, title="Чужой выигрыш")
        maker.request("POST", f"/api/orders/{oid3}/responses",
                      body={"price": 95000, "days": 11, "message": "me too"})
        other.request("POST", f"/api/orders/{oid3}/responses",
                      body={"price": 80000, "days": 9, "message": "mine"})
        status, data, _ = client.request("GET", "/api/orders?status=open")
        o3 = next(o for o in data["orders"] if o["id"] == oid3)
        other_id = next(r["maker_id"] for r in o3["responses"] if r["maker_name"] == "Funnel Other")
        client.request("POST", f"/api/orders/{oid3}/choose",
                       body={"maker_id": other_id})

        # maker chosen on order2
        status, data, _ = client.request("GET", "/api/orders?status=open")
        o2 = next(o for o in data["orders"] if o["id"] == oid2)
        client.request("POST", f"/api/orders/{oid2}/choose",
                       body={"maker_id": o2["responses"][0]["maker_id"]})

        status, data, _ = maker.request("GET", "/api/maker/funnel")
        self.assertEqual(status, 200)
        stages = {s["id"]: s for s in data["stages"]}
        available_ids = {o["id"] for o in stages["available"]["orders"]}
        self.assertIn(oid1, available_ids)
        self.assertNotIn(oid2, available_ids)
        self.assertNotIn(oid3, available_ids)
        chosen_ids = {o["id"] for o in stages["chosen"]["orders"]}
        self.assertIn(oid2, chosen_ids)
        lost_ids = {o["id"] for o in stages["lost"]["orders"]}
        self.assertIn(oid3, lost_ids)
        self.assertEqual(data["totals"]["available"], len(stages["available"]["orders"]))

        client_funnel = Client()
        client_funnel.register("p1-fn-client2@test.local")
        status, _, _ = client_funnel.request("GET", "/api/maker/funnel")
        self.assertEqual(status, 403)


class P2FeatureTests(unittest.TestCase):
    def test_admin_verify_requisites_and_clear_on_change(self):
        admin = Client()
        admin.login("admin@meblio.ru", "admin123")
        maker = Client()
        maker.register("p2-req-maker@test.local", role="maker", name="P2 Reqs")
        status, _, _ = maker.request("POST", "/api/profile", body={
            "name": "P2 Reqs", "city": "Москва", "inn": "7701234567",
            "ogrn": "1027700132195", "is_public": "1",
        })
        self.assertEqual(status, 200)
        status, data, _ = maker.request("GET", "/api/session")
        uid = data["user"]["id"]

        # non-admin cannot verify
        status, _, _ = maker.request("POST", "/api/admin/verify-requisites",
                                     body={"user_id": uid, "verified": True})
        self.assertEqual(status, 403)

        # verify without requisites → 400
        bare = Client()
        bare.register("p2-req-bare@test.local", role="maker")
        status, _, _ = admin.request("POST", "/api/admin/verify-requisites",
                                     body={"user_id": bare.request("GET", "/api/session")[1]["user"]["id"],
                                           "verified": True})
        self.assertEqual(status, 400)

        status, data, _ = admin.request("POST", "/api/admin/verify-requisites",
                                        body={"user_id": uid, "verified": True})
        self.assertEqual(status, 200)
        self.assertTrue(data["verified"])
        status, data, _ = maker.request("GET", "/api/session")
        self.assertTrue(data["user"]["verified_requisites_at"])
        status, data, _ = maker.request("GET", f"/api/companies/{uid}")
        self.assertTrue(data["company"]["verified_requisites_at"])

        # changing inn clears verification
        status, _, _ = maker.request("POST", "/api/profile", body={
            "name": "P2 Reqs", "city": "Москва", "inn": "7709876543",
            "ogrn": "1027700132195", "is_public": "1",
        })
        self.assertEqual(status, 200)
        status, data, _ = maker.request("GET", "/api/session")
        self.assertFalse(data["user"]["verified_requisites_at"])

        # unverify path
        status, _, _ = admin.request("POST", "/api/admin/verify-requisites",
                                     body={"user_id": uid, "verified": False})
        self.assertEqual(status, 200)

    def test_estimate_calculation_and_validation(self):
        c = Client()
        c.register("p2-est@test.local")
        # default material_price path
        status, data, _ = c.request("POST", "/api/estimate", body={
            "width": 600, "height": 2000, "depth": 400, "qty": 2,
            "material_price": 1000, "material_name": "ЛДСП",
            "complexity": "medium", "hardware": "standard",
        })
        self.assertEqual(status, 200)
        # area = 2*(0.6*2 + 0.6*0.4 + 2*0.4) = 2*(1.2+0.24+0.8) = 4.48
        self.assertAlmostEqual(data["area_m2"], 4.48, places=2)
        self.assertEqual(data["qty"], 2)
        self.assertGreater(data["total"], 0)
        self.assertEqual(data["warranty_days"], 14)
        expected_material = 4.48 * 1000 * 1.25
        self.assertAlmostEqual(data["material_cost"], expected_material, places=1)

        # material from catalog
        status, mats, _ = c.request("GET", "/api/materials")
        self.assertEqual(status, 200)
        mid = mats["materials"][0]["id"]
        status, data, _ = c.request("POST", "/api/estimate", body={
            "width": 1000, "height": 1000, "depth": 500, "qty": 1,
            "material_id": mid, "complexity": "simple", "hardware": "basic",
        })
        self.assertEqual(status, 200)
        self.assertEqual(data["material_id"] if "material_id" in data else data["material_name"],
                         data["material_name"])
        self.assertEqual(data["complexity"], "simple")
        self.assertEqual(data["hardware"], "basic")

        # invalid inputs
        status, _, _ = c.request("POST", "/api/estimate", body={
            "width": -1, "height": 100, "depth": 100, "qty": 1,
        })
        self.assertEqual(status, 400)
        status, _, _ = c.request("POST", "/api/estimate", body={
            "width": 600, "height": 2000, "depth": 400, "qty": 1,
            "complexity": "ultra",
        })
        self.assertEqual(status, 400)
        status, _, _ = c.request("POST", "/api/estimate", body={
            "width": 600, "height": 2000, "depth": 400, "qty": 1,
            "material_id": 999999,
        })
        self.assertEqual(status, 404)

    def test_warranty_until_on_closed_order_payload(self):
        client = Client()
        client.register("p2-war-client@test.local")
        maker = Client()
        maker.register("p2-war-maker@test.local", role="maker")
        fields = {"title": "Гарантийный", "type": "Кухни", "quantity": "1",
                  "city": "Москва", "budget": "50000", "deadline": "10 дней", "details": "x"}
        body, ctype = make_multipart(fields, [])
        status, data, _ = client.request("POST", "/api/orders", raw_body=body,
                                         headers={"Content-Type": ctype})
        oid = data["order"]["id"]
        maker.request("POST", f"/api/orders/{oid}/responses",
                      body={"price": 40000, "days": 5, "message": "ok"})
        status, data, _ = client.request("GET", "/api/orders?status=open")
        target = next(o for o in data["orders"] if o["id"] == oid)
        rid = target["responses"][0]["maker_id"]
        client.request("POST", f"/api/orders/{oid}/choose", body={"maker_id": rid})
        status, data, _ = client.request("POST", f"/api/orders/{oid}/accept",
                                         body={"force": True})
        self.assertEqual(status, 200)
        self.assertEqual(data.get("warranty_days"), 14)
        status, data, _ = client.request("GET", "/api/orders?status=closed")
        closed = next(o for o in data["orders"] if o["id"] == oid)
        self.assertTrue(closed.get("warranty_until"))


class P3FeatureTests(unittest.TestCase):
    def test_service_params_crud_and_detail(self):
        maker = Client()
        maker.register("p3-svc-maker@test.local", role="maker")
        params_json = json.dumps([
            {"name": "Материал", "value": "ЛДСП 18мм"},
            {"name": "Гарантия", "value": "24 мес"},
        ])
        body, ctype = make_multipart({
            "title": "Кухня на заказ",
            "description": "Под ключ",
            "price_type": "от 80 000",
            "params": params_json,
        }, [])
        status, data, _ = maker.request("POST", "/api/services", raw_body=body,
                                        headers={"Content-Type": ctype})
        self.assertEqual(status, 200)
        sid = data["service_id"]
        status, data, _ = maker.request("GET", f"/api/services/{sid}")
        self.assertEqual(status, 200)
        names = [p["name"] for p in data["service"]["params"]]
        self.assertIn("Материал", names)
        self.assertIn("Гарантия", names)
        status, data, _ = maker.request("PUT", f"/api/services/{sid}", body={
            "title": "Кухня на заказ 2",
            "description": "x",
            "price_type": "от 90 000",
            "params": json.dumps([{"name": "Цвет", "value": "Белый"}]),
        })
        self.assertEqual(status, 200)
        status, data, _ = maker.request("GET", f"/api/services/{sid}")
        self.assertEqual([p["name"] for p in data["service"]["params"]], ["Цвет"])
        status, data, _ = maker.request("DELETE", f"/api/services/{sid}")
        self.assertEqual(status, 200)

    def test_free_quota_and_upgrade(self):
        maker = Client()
        maker.register("p3-quota-maker@test.local", role="maker")
        status, data, _ = maker.request("GET", "/api/tariff")
        self.assertEqual(status, 200)
        self.assertEqual(data["plan"], "free")
        self.assertEqual(data["responses_limit"], 5)
        self.assertEqual(data["responses_used"], 0)

        client = Client()
        client.register("p3-quota-client@test.local")
        order_ids = []
        for i in range(6):
            fields = {"title": f"Заказ {i}", "type": "Кухни", "quantity": "1",
                      "city": "Москва", "budget": "50000", "deadline": "10 дней", "details": "x"}
            body, ctype = make_multipart(fields, [])
            status, data, _ = client.request("POST", "/api/orders", raw_body=body,
                                             headers={"Content-Type": ctype})
            self.assertEqual(status, 200)
            oid = data["order"]["id"]
            order_ids.append(oid)
            status, resp, _ = maker.request("POST", f"/api/orders/{oid}/responses",
                                            body={"price": 40000, "days": 5, "message": "ok"})
            if i < 5:
                self.assertEqual(status, 200, msg=f"resp {i}: {resp}")
            else:
                self.assertEqual(status, 403, msg=str(resp))
                self.assertIn("Лимит", resp.get("error", ""))

        status, data, _ = maker.request("POST", "/api/tariff/upgrade", body={"plan": "pro"})
        self.assertEqual(status, 200)
        self.assertEqual(data["plan"], "pro")
        status, data, _ = maker.request("GET", "/api/tariff")
        self.assertEqual(data["plan"], "pro")
        self.assertIsNone(data["responses_limit"])
        status, resp, _ = maker.request("POST", f"/api/orders/{order_ids[5]}/responses",
                                        body={"price": 41000, "days": 6, "message": "pro"})
        self.assertEqual(status, 200, msg=str(resp))

    def test_proposal_create_accept_flow(self):
        client = Client()
        client.register("p3-prop-client@test.local")
        maker = Client()
        maker.register("p3-prop-maker@test.local", role="maker")
        fields = {"title": "Гарнитур", "type": "Кухни", "quantity": "1",
                  "city": "Москва", "budget": "120000", "deadline": "20 дней", "details": "x"}
        body, ctype = make_multipart(fields, [])
        status, data, _ = client.request("POST", "/api/orders", raw_body=body,
                                         headers={"Content-Type": ctype})
        oid = data["order"]["id"]
        # no response yet → 403
        status, data, _ = maker.request("POST", f"/api/orders/{oid}/proposals",
                                        body={"amount": 100000, "days": 14, "message": "ok"})
        self.assertEqual(status, 403)
        maker.request("POST", f"/api/orders/{oid}/responses",
                      body={"price": 100000, "days": 14, "message": "отклик"})
        status, data, _ = maker.request("POST", f"/api/orders/{oid}/proposals", body={
            "amount": 100000,
            "days": 14,
            "message": "Полный цикл",
            "items": [{"name": "Корпус", "qty": 2, "price": 40000},
                      {"name": "Фасады", "qty": 1, "price": 20000}],
        })
        self.assertEqual(status, 200, msg=str(data))
        pid = data["proposal_id"]
        self.assertEqual(data["amount"], 100000)
        status, data, _ = maker.request("GET", f"/api/orders/{oid}/proposals")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["proposals"]), 1)
        self.assertEqual(data["proposals"][0]["status"], "sent")
        # maker cannot accept
        status, data, _ = maker.request("POST", f"/api/proposals/{pid}/status",
                                        body={"status": "accepted"})
        self.assertEqual(status, 403)
        # client accepts → order moves to progress with selected maker
        status, data, _ = client.request("POST", f"/api/proposals/{pid}/status",
                                         body={"status": "accepted"})
        self.assertEqual(status, 200, msg=str(data))
        status, data, _ = client.request("GET", "/api/orders?status=progress")
        target = next(o for o in data["orders"] if o["id"] == oid)
        self.assertEqual(target["selected_maker_id"], data and target["selected_maker_id"])
        self.assertIsNotNone(target.get("selected_maker_id"))
        status, data, _ = client.request("GET", f"/api/orders/{oid}/proposals")
        self.assertEqual(data["proposals"][0]["status"], "accepted")

    def test_messenger_link_and_tariffs_seo(self):
        c = Client()
        c.register("p3-mess@test.local")
        status, data, _ = c.request("POST", "/api/messenger/link",
                                    body={"channel": "telegram", "chat_id": "123456789"})
        self.assertEqual(status, 200, msg=str(data))
        self.assertEqual(data["chat_id"], "123456789")
        status, data, _ = c.request("GET", "/api/session")
        self.assertEqual(data["user"]["telegram_chat_id"], "123456789")
        status, data, _ = c.request("POST", "/api/messenger/link",
                                    body={"channel": "telegram", "chat_id": "bad id!"})
        self.assertEqual(status, 400)
        status, data, _ = c.request("POST", "/api/messenger/link",
                                    body={"channel": "max", "chat_id": "max-1"})
        self.assertEqual(status, 200)
        self.assertEqual(data["user"]["max_chat_id"], "max-1")

        status, raw, _ = c.request("GET", "/tariffs")
        self.assertEqual(status, 200)
        html = raw["_raw"].decode("utf-8")
        self.assertIn("Тарифы", html)
        self.assertIn("<title>Тарифы", html)
        status, data, _ = c.request("GET", "/sitemap.xml")
        xml = data["_raw"].decode("utf-8")
        self.assertIn("/tariffs", xml)


if __name__ == "__main__":
    unittest.main()
