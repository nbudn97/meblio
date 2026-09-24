"""Account/session API mixin: auth, 2FA, profile, notifications, tariff, documents."""

import re
import secrets
import sqlite3
from http import cookies
from urllib.parse import urlparse, parse_qs

from db import connect, row_to_dict, rows_to_list, now, create_user, hash_password, verify_password, purge_expired_sessions
from common import PAGE_SIZE, check_rate_limit, create_notification, store_upload, csv_safe, is_dev_mode, public_base_url
from auth_util import (
    verify_totp, get_tfa_trust_secret, make_trust_cookie, read_trusted_user_id,
    create_pending_token, generate_csrf_token, TRUST_DEVICE_COOKIE, TRUST_DEVICE_DAYS,
)
import datetime

class AccountMixin:
    def api_session(self):
        with connect() as conn:
            user = self.current_user(conn)
        self.send_json(200, {"user": self.public_user(user)})

    def api_register(self):
        try:
            data = self.read_json()
            ip = self.client_address[0]
            if not check_rate_limit(f"register:{ip}", 5, 300):
                return self.send_error_json(429, "Слишком много попыток. Подождите 5 минут.")
            role = data.get("role")
            if role not in ("client", "maker"):
                return self.send_error_json(400, "Выберите роль")
            if data.get("website"):
                return self.send_error_json(400, "Регистрация не прошла проверку")
            if str(data.get("consent_pd", "")).lower() not in ("1", "true", "on", "yes"):
                return self.send_error_json(400, "Необходимо согласие на обработку персональных данных и прием оферты")
            if len(data.get("password", "")) < 6:
                return self.send_error_json(400, "Пароль должен быть не короче 6 символов")
            company_type = data.get("company_type", "client" if role == "client" else "manufacturer")
            region_id = data.get("region_id")
            if region_id:
                region_id = int(region_id)
            with connect() as conn:
                user_id = create_user(
                    conn, role,
                    data.get("name", "").strip(),
                    data.get("email", "").strip(),
                    data.get("password", ""),
                    data.get("city", "").strip(),
                    data.get("phone", "").strip(),
                    data.get("about", "").strip(),
                    data.get("skills", "").strip(),
                    data.get("capacity", "").strip(),
                    company_type=company_type,
                    region_id=region_id,
                    consent_pd_at=now(),
                )
                token = secrets.token_urlsafe(32)
                purge_expired_sessions(conn)
                conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)", (token, user_id, now()))
                user = conn.execute("SELECT users.*, regions.name AS region_name FROM users LEFT JOIN regions ON regions.id = users.region_id WHERE users.id = ?", (user_id,)).fetchone()
                email = user["email"]
                verify_token = create_pending_token(conn, "email_verifications", user_id, 60 * 24)
                base_url = public_base_url(self.headers)
                from mailer import send_email
                send_email(
                    email,
                    "Подтвердите email на Meblio",
                    "Для подтверждения адреса перейдите по ссылке:",
                    link_url=f"{base_url}/api/verify-email?token={verify_token}",
                )
            payload = {"user": self.public_user(row_to_dict(user))}
            if is_dev_mode():
                payload["verify_url"] = f"{base_url}/api/verify-email?token={verify_token}"
            self.send_json(200, payload, {"Set-Cookie": f"meblio_session={token}; Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age=604800"})
        except sqlite3.IntegrityError:
            self.send_error_json(409, "Пользователь с таким email уже зарегистрирован")
        except Exception as exc:
            self.send_error_json(400, str(exc))

    def api_login(self):
        try:
            data = self.read_json()
            ip = self.client_address[0]
            email = data.get("email", "").strip().lower()
            if not check_rate_limit(f"login:{ip}:{email}", 5, 300):
                return self.send_error_json(429, "Слишком много попыток. Подождите 5 минут.")
            tfa_payload = None
            with connect() as conn:
                user = conn.execute("SELECT users.*, regions.name AS region_name FROM users LEFT JOIN regions ON regions.id = users.region_id WHERE email = ?", (data.get("email", "").strip().lower(),)).fetchone()
                if not user or not verify_password(data.get("password", ""), user["password_salt"], user["password_hash"]):
                    return self.send_error_json(401, "Неверный email или пароль")
                tfa = conn.execute("SELECT * FROM tfa_secrets WHERE user_id = ? AND enabled = 1", (user["id"],)).fetchone()
                trusted = bool(tfa) and read_trusted_user_id(self, conn) == user["id"]
                if tfa and not trusted:
                    login_token = create_pending_token(conn, "pending_tfa", user["id"], 10)
                    tfa_payload = {"tfa_required": True, "login_token": login_token}
                else:
                    token = secrets.token_urlsafe(32)
                    purge_expired_sessions(conn)
                    conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)", (token, user["id"], now()))
                    trust_value = make_trust_cookie(get_tfa_trust_secret(conn), user["id"], user["password_hash"]) if tfa else None
            if tfa_payload:
                return self.send_json(200, tfa_payload)
            cookies_to_set = [f"meblio_session={token}; Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age=604800"]
            if trust_value:
                cookies_to_set.append(f"{TRUST_DEVICE_COOKIE}={trust_value}; Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age={TRUST_DEVICE_DAYS * 86400}")
            self.send_json(200, {"user": self.public_user(row_to_dict(user))}, {"Set-Cookie": cookies_to_set})
        except Exception as exc:
            self.send_error_json(400, str(exc))

    def api_logout(self):
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        token = jar.get("meblio_session")
        if token:
            with connect() as conn:
                conn.execute("DELETE FROM sessions WHERE token = ?", (token.value,))
        self.send_json(200, {"ok": True}, {"Set-Cookie": "meblio_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax; Secure"})

    def api_profile(self):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            region_id = data.get("region_id")
            if region_id:
                region_id = int(region_id)
            inn = (data.get("inn") or "").strip()
            ogrn = (data.get("ogrn") or "").strip()
            website = (data.get("website") or "").strip()
            if inn and (not inn.isdigit() or len(inn) not in (10, 12)):
                return self.send_error_json(400, "ИНН должен содержать 10 или 12 цифр")
            if ogrn and (not ogrn.isdigit() or len(ogrn) not in (13, 15)):
                return self.send_error_json(400, "ОГРН должен содержать 13 или 15 цифр")
            if website:
                if not re.match(r"^https?://", website, flags=re.I):
                    website = "https://" + website
                parsed = urlparse(website)
                netloc = parsed.netloc
                if parsed.scheme not in ("http", "https") or not netloc or " " in website or not (netloc == "localhost" or "." in netloc):
                    return self.send_error_json(400, "Некорректный адрес веб-сайта")
            is_public = 1 if str(data.get("is_public", "0")).lower() in ("1", "on", "true") else 0
            req_changed = (user.get("inn") or "") != inn or (user.get("ogrn") or "") != ogrn
            conn.execute(
                """
                UPDATE users SET name = ?, city = ?, region_id = ?, phone = ?, about = ?, skills = ?, capacity = ?,
                inn = ?, ogrn = ?, website = ?, is_public = ?,
                verified_requisites_at = CASE WHEN ? THEN NULL ELSE verified_requisites_at END
                WHERE id = ?
                """,
                (
                    data.get("name", "").strip(),
                    data.get("city", "").strip(),
                    region_id,
                    data.get("phone", "").strip(),
                    data.get("about", "").strip(),
                    data.get("skills", "").strip(),
                    data.get("capacity", "").strip(),
                    inn,
                    ogrn,
                    website,
                    is_public,
                    1 if req_changed else 0,
                    user["id"],
                ),
            )
            updated = conn.execute("SELECT users.*, regions.name AS region_name FROM users LEFT JOIN regions ON regions.id = users.region_id WHERE users.id = ?", (user["id"],)).fetchone()
        self.send_json(200, {"user": self.public_user(row_to_dict(updated))})

    def api_favorites_list(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            rows = conn.execute(
                """
                SELECT favorites.id, favorites.company_id, favorites.created_at,
                       users.name, users.city, users.company_type, users.about, users.logo,
                       regions.name AS region_name
                FROM favorites
                JOIN users ON users.id = favorites.company_id
                LEFT JOIN regions ON regions.id = users.region_id
                WHERE favorites.user_id = ?
                ORDER BY favorites.created_at DESC
                """,
                (user["id"],),
            ).fetchall()
        self.send_json(200, {"favorites": rows_to_list(rows)})

    def api_add_favorite(self):
        data = self.read_json()
        company_id = int(data.get("company_id", 0))
        try:
            with connect() as conn:
                user = self.require_user(conn)
                if not user:
                    return
                if company_id == user["id"]:
                    return self.send_error_json(400, "Нельзя добавить себя")
                conn.execute(
                    "INSERT INTO favorites (user_id, company_id, created_at) VALUES (?, ?, ?)",
                    (user["id"], company_id, now()),
                )
        except sqlite3.IntegrityError:
            return self.send_error_json(409, "Уже в избранном")
        self.send_json(200, {"ok": True})

    def api_remove_favorite(self, company_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            conn.execute(
                "DELETE FROM favorites WHERE user_id = ? AND company_id = ?",
                (user["id"], company_id),
            )
        self.send_json(200, {"ok": True})

    def api_tfa_status(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            tfa = conn.execute("SELECT * FROM tfa_secrets WHERE user_id = ?", (user["id"],)).fetchone()
        self.send_json(200, {"enabled": bool(tfa and tfa["enabled"])})

    def api_tfa_setup(self):
        import hmac as _hmac
        import hashlib as _hashlib
        import base64 as _b64
        import struct as _struct
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            secret = _b64.b32encode(secrets.token_bytes(20)).decode()
            conn.execute(
                "INSERT INTO tfa_secrets (user_id, secret, enabled, created_at) VALUES (?, ?, 0, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET secret=excluded.secret, enabled=0",
                (user["id"], secret, now()),
            )
        self.send_json(200, {"secret": secret, "otpauth": f"otpauth://totp/Meblio:{user['email']}?secret={secret}&issuer=Meblio"})

    def api_tfa_verify(self):
        data = self.read_json()
        code = data.get("code", "")
        enable = data.get("enable", False)
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            tfa = conn.execute("SELECT * FROM tfa_secrets WHERE user_id = ?", (user["id"],)).fetchone()
            if not tfa:
                return self.send_error_json(400, "Сначала настройте 2FA")
            if not verify_totp(tfa["secret"], code):
                return self.send_error_json(400, "Неверный код")
            if enable:
                conn.execute("UPDATE tfa_secrets SET enabled = 1 WHERE user_id = ?", (user["id"],))
        self.send_json(200, {"ok": True})

    def api_resend_verification(self):
        data = self.read_json()
        email = data.get("email", "").strip().lower()
        with connect() as conn:
            user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if not user:
                return self.send_error_json(404, "Пользователь не найден")
            if user["is_verified"]:
                return self.send_json(200, {"ok": True, "already_verified": True})
            verify_token = create_pending_token(conn, "email_verifications", user["id"], 60 * 24)
            base_url = public_base_url(self.headers)
            from mailer import send_email
            send_email(
                user["email"],
                "Подтвердите email на Meblio",
                "Для подтверждения адреса перейдите по ссылке:",
                link_url=f"{base_url}/api/verify-email?token={verify_token}",
            )
            payload = {"ok": True}
            if is_dev_mode():
                payload["verify_url"] = f"{base_url}/api/verify-email?token={verify_token}"
        self.send_json(200, payload)

    def api_verify_email(self, query):
        params = parse_qs(query)
        token = params.get("token", [""])[0]
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM email_verifications WHERE token = ? AND purpose = 'verify' AND expires_at > ?",
                (token, now()),
            ).fetchone()
            if not row:
                return self.send_error_json(400, "Ссылка недействительна или устарела")
            conn.execute("UPDATE users SET is_verified = 1 WHERE id = ?", (row["user_id"],))
            conn.execute("DELETE FROM email_verifications WHERE token = ?", (token,))
        self.send_json(200, {"ok": True, "verified": True})

    def api_tfa_login(self):
        data = self.read_json()
        login_token = data.get("login_token", "")
        code = data.get("code", "")
        with connect() as conn:
            pending = conn.execute(
                "SELECT * FROM pending_tfa WHERE token = ? AND expires_at > ?",
                (login_token, now()),
            ).fetchone()
            if not pending:
                return self.send_error_json(400, "Сессия входа истекла, повторите вход")
            user = conn.execute("SELECT users.*, regions.name AS region_name FROM users LEFT JOIN regions ON regions.id = users.region_id WHERE users.id = ?", (pending["user_id"],)).fetchone()
            if not user:
                return self.send_error_json(400, "Пользователь не найден")
            tfa = conn.execute("SELECT * FROM tfa_secrets WHERE user_id = ?", (user["id"],)).fetchone()
            if not tfa or not tfa["enabled"] or not verify_totp(tfa["secret"], code):
                return self.send_error_json(400, "Неверный код")
            conn.execute("DELETE FROM pending_tfa WHERE token = ?", (login_token,))
            token = secrets.token_urlsafe(32)
            purge_expired_sessions(conn)
            conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)", (token, user["id"], now()))
            trust_value = make_trust_cookie(get_tfa_trust_secret(conn), user["id"], user["password_hash"])
        self.send_json(200, {"user": self.public_user(row_to_dict(user))}, {"Set-Cookie": [
            f"meblio_session={token}; Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age=604800",
            f"{TRUST_DEVICE_COOKIE}={trust_value}; Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age={TRUST_DEVICE_DAYS * 86400}",
        ]})

    def api_forgot_password(self):
        data = self.read_json()
        email = data.get("email", "").strip().lower()
        with connect() as conn:
            user = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if user:
                token = create_pending_token(conn, "email_verifications", user["id"], 60)
                conn.execute(
                    "UPDATE email_verifications SET purpose = 'reset' WHERE token = ?", (token,)
                )
                base_url = public_base_url(self.headers)
                from mailer import send_email
                send_email(
                    email,
                    "Восстановление пароля Meblio",
                    "Для восстановления пароля перейдите по ссылке:",
                    link_url=f"{base_url}/reset-password?token={token}",
                )
        self.send_json(200, {"ok": True})

    def api_reset_password(self):
        data = self.read_json()
        token = data.get("token", "")
        password = data.get("password", "")
        if len(password) < 6:
            return self.send_error_json(400, "Пароль должен быть не короче 6 символов")
        with connect() as conn:
            row = conn.execute(
                "SELECT * FROM email_verifications WHERE token = ? AND purpose = 'reset' AND expires_at > ?",
                (token, now()),
            ).fetchone()
            if not row:
                return self.send_error_json(400, "Ссылка недействительна или устарела")
            salt, digest = hash_password(password)
            conn.execute("UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?", (salt, digest, row["user_id"]))
            conn.execute("DELETE FROM email_verifications WHERE token = ?", (token,))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))
        self.send_json(200, {"ok": True})

    def api_change_password(self):
        data = self.read_json()
        old_password = data.get("old_password", "")
        new_password = data.get("new_password", "")
        if len(new_password) < 6:
            return self.send_error_json(400, "Пароль должен быть не короче 6 символов")
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            if not verify_password(old_password, user["password_salt"], user["password_hash"]):
                return self.send_error_json(400, "Неверный текущий пароль")
            salt, digest = hash_password(new_password)
            conn.execute("UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?", (salt, digest, user["id"]))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
        self.send_json(200, {"ok": True})

    def api_change_email(self):
        data = self.read_json()
        new_email = data.get("new_email", "").strip().lower()
        password = data.get("password", "")
        if "@" not in new_email or "." not in new_email:
            return self.send_error_json(400, "Некорректный email")
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            if not verify_password(password, user["password_salt"], user["password_hash"]):
                return self.send_error_json(400, "Неверный пароль")
            existing = conn.execute("SELECT id FROM users WHERE email = ? AND id != ?", (new_email, user["id"])).fetchone()
            if existing:
                return self.send_error_json(409, "Этот email уже занят")
            conn.execute("UPDATE users SET email = ?, is_verified = 0 WHERE id = ?", (new_email, user["id"]))
            verify_token = create_pending_token(conn, "email_verifications", user["id"], 60 * 24)
            base_url = public_base_url(self.headers)
            from mailer import send_email
            send_email(
                new_email,
                "Подтвердите новый email на Meblio",
                "Для подтверждения адреса перейдите по ссылке:",
                link_url=f"{base_url}/api/verify-email?token={verify_token}",
            )
            payload = {"ok": True}
            if is_dev_mode():
                payload["verify_url"] = f"{base_url}/api/verify-email?token={verify_token}"
        self.send_json(200, payload)

    def api_delete_account(self):
        data = self.read_json()
        password = data.get("password", "")
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            if user["role"] == "admin":
                return self.send_error_json(400, "Администратор не может удалить свой аккаунт")
            if not verify_password(password, user["password_salt"], user["password_hash"]):
                return self.send_error_json(400, "Неверный пароль")
            # anonymize instead of hard delete: keep orders/reviews history intact
            anon_email = f"deleted_{user['id']}_{secrets.token_hex(4)}@meblio.local"
            conn.execute(
                "UPDATE users SET name = ?, email = ?, city = '', phone = '', about = '', skills = '', capacity = '', logo = '', is_verified = 0 WHERE id = ?",
                ("Удалённый пользователь", anon_email, user["id"]),
            )
            conn.execute(
                "UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?",
                (secrets.token_hex(16), secrets.token_hex(64), user["id"]),
            )
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user["id"],))
            conn.execute("DELETE FROM api_tokens WHERE user_id = ?", (user["id"],))
            for table in ("favorites", "notification_preferences", "csrf_tokens"):
                try:
                    conn.execute(f"DELETE FROM {table} WHERE {'user_id' if table != 'csrf_tokens' else 'user_id'} = ?", (user["id"],))
                except sqlite3.OperationalError:
                    pass
        self.send_json(200, {"ok": True})

    def api_auth_token(self):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            import hashlib as _hl
            import time as _t
            token = secrets.token_urlsafe(48)
            expires = (datetime.datetime.now() + datetime.timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("INSERT INTO api_tokens (user_id, token, name, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                         (user["id"], token, data.get("name", "mobile"), expires, now()))
        self.send_json(200, {"token": token, "expires_at": expires})

    def api_csrf_token(self):
        with connect() as conn:
            cookie_header = self.headers.get("Cookie", "")
            jar = cookies.SimpleCookie(cookie_header)
            token = jar.get("meblio_session")
            session_token = token.value if token else None
            csrf = generate_csrf_token(conn, session_token)
        self.send_json(200, {"csrf_token": csrf})

    def api_notifications_list(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            rows = conn.execute(
                "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT 50",
                (user["id"],),
            ).fetchall()
            unread = conn.execute(
                "SELECT COUNT(*) FROM notifications WHERE user_id = ? AND is_read = 0",
                (user["id"],),
            ).fetchone()[0]
        self.send_json(200, {"notifications": rows_to_list(rows), "unread": unread})

    def api_notification_read(self, notification_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            conn.execute(
                "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
                (notification_id, user["id"]),
            )
        self.send_json(200, {"ok": True})

    def api_notifications_read_all(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            conn.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ?", (user["id"],))
        self.send_json(200, {"ok": True})

    def api_notification_preferences(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            prefs = conn.execute("SELECT * FROM notification_preferences WHERE user_id = ?", (user["id"],)).fetchone()
            if not prefs:
                conn.execute("INSERT INTO notification_preferences (user_id) VALUES (?)", (user["id"],))
                prefs = conn.execute("SELECT * FROM notification_preferences WHERE user_id = ?", (user["id"],)).fetchone()
        self.send_json(200, {"preferences": dict(prefs)})

    def api_update_notification_preferences(self):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            conn.execute("""
                INSERT INTO notification_preferences (user_id, new_order, response, message, chosen, review, order_status, system, push_enabled, email_enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    new_order=excluded.new_order, response=excluded.response, message=excluded.message,
                    chosen=excluded.chosen, review=excluded.review, order_status=excluded.order_status,
                    system=excluded.system, push_enabled=excluded.push_enabled, email_enabled=excluded.email_enabled
            """, (
                user["id"],
                int(data.get("new_order", 1)), int(data.get("response", 1)), int(data.get("message", 1)),
                int(data.get("chosen", 1)), int(data.get("review", 1)), int(data.get("order_status", 1)),
                int(data.get("system", 1)), int(data.get("push_enabled", 1)), int(data.get("email_enabled", 0)),
            ))
        self.send_json(200, {"ok": True})

    def api_delete_notification(self, notification_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            conn.execute("DELETE FROM notifications WHERE id = ? AND user_id = ?", (notification_id, user["id"]))
        self.send_json(200, {"ok": True})

    def api_notifications_clear_all(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            conn.execute("DELETE FROM notifications WHERE user_id = ?", (user["id"],))
        self.send_json(200, {"ok": True})

    def _response_quota(self, conn, user):
        plan = (user.get("plan") or "free")
        if plan != "free":
            return plan, None, None
        month_prefix = now()[:7] + "-01"
        used = conn.execute(
            "SELECT COUNT(*) FROM responses WHERE maker_id = ? AND created_at >= ?",
            (user["id"], month_prefix),
        ).fetchone()[0]
        return plan, used, 5

    def api_tariff_info(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            plan, used, limit = self._response_quota(conn, user)
        self.send_json(200, {
            "plan": plan,
            "is_pro": plan != "free",
            "responses_used": used,
            "responses_limit": limit,
            "response_month_start": now()[:7] + "-01",
            "warranty_days": 14,
        })

    def api_tariff_upgrade(self):
        data = self.read_json()
        target = str(data.get("plan", "pro")).strip().lower()
        if target not in ("pro", "free"):
            return self.send_error_json(400, "plan: pro или free")
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            # Billing is status-only (no payment provider in this wave)
            conn.execute("UPDATE users SET plan = ? WHERE id = ?", (target, user["id"]))
            updated = conn.execute(
                "SELECT users.*, regions.name AS region_name FROM users LEFT JOIN regions ON regions.id = users.region_id WHERE users.id = ?",
                (user["id"],),
            ).fetchone()
        self.send_json(200, {"ok": True, "plan": target, "user": self.public_user(row_to_dict(updated))})

    def api_link_messenger(self):
        data = self.read_json()
        channel = str(data.get("channel", "")).strip().lower()
        chat_id = str(data.get("chat_id", "")).strip()[:64]
        if channel not in ("telegram", "max"):
            return self.send_error_json(400, "channel: telegram или max")
        if chat_id and not re.fullmatch(r"[\w-]{1,64}", chat_id):
            return self.send_error_json(400, "Некорректный chat_id")
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            col = "telegram_chat_id" if channel == "telegram" else "max_chat_id"
            conn.execute(f"UPDATE users SET {col} = ? WHERE id = ?", (chat_id, user["id"]))
            updated = conn.execute("SELECT users.*, regions.name AS region_name FROM users LEFT JOIN regions ON regions.id = users.region_id WHERE users.id = ?", (user["id"],)).fetchone()
        self.send_json(200, {"ok": True, "channel": channel, "chat_id": chat_id, "user": self.public_user(row_to_dict(updated))})

    def api_documents_list(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            docs = rows_to_list(conn.execute(
                "SELECT id, original_name, doc_type, size, mime, created_at FROM company_documents WHERE user_id = ? ORDER BY created_at DESC",
                (user["id"],),
            ).fetchall())
        self.send_json(200, {"documents": docs})

    def api_upload_document(self):
        try:
            with connect() as conn:
                user = self.require_user(conn)
                if not user:
                    return
                fields, files = self.read_multipart()
                if not files:
                    return self.send_error_json(400, "Файл не загружен")
                doc_type = fields.get("doc_type", "other")
                for file in files:
                    stored, original = store_upload(f"doc_{user['id']}", file["filename"], file["content"])
                    conn.execute(
                        "INSERT INTO company_documents (user_id, original_name, stored_name, doc_type, size, mime, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (user["id"], original, stored, doc_type, len(file["content"]), file["mime"], now()),
                    )
        except Exception as exc:
            return self.send_error_json(400, str(exc))
        self.send_json(200, {"ok": True})

    def api_delete_document(self, doc_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            doc = conn.execute("SELECT * FROM company_documents WHERE id = ? AND user_id = ?", (doc_id, user["id"])).fetchone()
            if not doc:
                return self.send_error_json(404, "Документ не найден")
            file_path = UPLOAD_DIR / doc["stored_name"]
            if file_path.exists():
                file_path.unlink()
            conn.execute("DELETE FROM company_documents WHERE id = ?", (doc_id,))
        self.send_json(200, {"ok": True})

    def api_upload_logo(self):
        try:
            with connect() as conn:
                user = self.require_user(conn)
                if not user:
                    return
                fields, files = self.read_multipart()
                if not files:
                    return self.send_error_json(400, "Файл не загружен")
                file = files[0]
                stored, _ = store_upload(f"logo_{user['id']}", file["filename"], file["content"])
                conn.execute("UPDATE users SET logo = ? WHERE id = ?", (stored, user["id"]))
        except Exception as exc:
            return self.send_error_json(400, str(exc))
        self.send_json(200, {"ok": True, "logo": f"/uploads/{stored}"})

