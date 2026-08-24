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

from db import init_db  # noqa: E402  (env must be set before import)
import app as app_module  # noqa: E402
from ws_server import validate_session, validate_thread_access  # noqa: E402

_server = None
_base_url = None


def setUpModule():
    global _server, _base_url
    init_db()
    app_module.check_rate_limit = lambda *args, **kwargs: True  # tests create many users fast
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

    def register(self, email, password="secret123", role="client", name="Test Co"):
        status, data, headers = self.request(
            "POST", "/api/register",
            body={"role": role, "name": name, "email": email,
                  "password": password, "city": "Москва"},
        )
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

    def test_static_security_headers(self):
        c = Client()
        status, _, headers = c.request("GET", "/index.html")
        self.assertEqual(status, 200)
        self.assertIn("Content-Security-Policy", headers)
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")

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


def totp_code(secret):
    import base64
    import hashlib
    import hmac
    import struct
    import time
    key = base64.b32decode(secret)
    counter = int(time.time()) // 30
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
        self.assertEqual(status, 200)
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


if __name__ == "__main__":
    unittest.main()
