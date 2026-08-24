from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http import cookies
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
import json
import mimetypes
import os
import re
import secrets
import shutil
import sqlite3

from db import (
    BASE_DIR,
    UPLOAD_DIR,
    COMPANY_TYPES,
    connect,
    hash_password,
    verify_password,
    row_to_dict,
    rows_to_list,
    now,
    init_db,
    create_user,
    ensure_thread,
)

STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/styles.css": "styles.css",
    "/script.js": "script.js",
    "/meblio.png": "meblio.png",
}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
PAGE_SIZE = 20

ORDER_ID_RE = re.compile(r"^/api/orders/(\d+)/")
THREAD_ID_RE = re.compile(r"^/api/threads/(\d+)/")
COMPANY_ID_RE = re.compile(r"^/api/companies/(\d+)")
SERVICE_ID_RE = re.compile(r"^/api/services/(\d+)")
ADMIN_USER_RE = re.compile(r"^/api/admin/users/(\d+)$")
ADMIN_ORDER_RE = re.compile(r"^/api/admin/orders/(\d+)$")
ADMIN_SERVICE_RE = re.compile(r"^/api/admin/services/(\d+)$")
REVIEW_RE = re.compile(r"^/api/reviews/(\d+)$")
NOTIFICATION_RE = re.compile(r"^/api/notifications/(\d+)$")
DOCUMENT_RE = re.compile(r"^/api/documents/(\d+)$")
MATERIAL_RE = re.compile(r"^/api/materials/(\d+)$")
TEMPLATE_RE = re.compile(r"^/api/templates/(\d+)$")
INVOICE_RE = re.compile(r"^/api/invoices/(\d+)$")
DELIVERY_RE = re.compile(r"^/api/delivery/(\d+)$")
HISTORY_RE = re.compile(r"^/api/order-history/(\d+)$")
SUPPLIER_RE = re.compile(r"^/api/suppliers/(\d+)$")
CERTIFICATE_RE = re.compile(r"^/api/certificates/(\d+)$")
TIMEENTRY_RE = re.compile(r"^/api/time-entries/(\d+)$")
CLIENT_RATING_RE = re.compile(r"^/api/client-ratings/(\d+)$")

rate_limits = {}

CSRF_EXEMPT_PATHS = {"/api/login", "/api/register", "/api/csrf-token"}


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


def check_rate_limit(key, max_attempts=5, window=300):
    import time as _time
    now = _time.time()
    if key not in rate_limits:
        rate_limits[key] = []
    rate_limits[key] = [t for t in rate_limits[key] if now - t < window]
    if len(rate_limits[key]) >= max_attempts:
        return False
    rate_limits[key].append(now)
    return True


def json_dumps(data):
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


def safe_filename(name):
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in "._- ").strip()
    return cleaned or "file"


def parse_deadline_days(deadline_str):
    import re as _re
    m = _re.search(r"(\d+)", deadline_str)
    return int(m.group(1)) if m else 0


class MeblioHandler(BaseHTTPRequestHandler):
    server_version = "MeblioHTTP/1.0"

    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def send_json(self, status, data, extra_headers=None):
        payload = json_dumps(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(payload)

    def send_error_json(self, status, message):
        self.send_json(status, {"error": message})

    def check_csrf(self):
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        sess = jar.get("meblio_session")
        if not sess:
            return True
        header_token = self.headers.get("X-CSRF-Token", "")
        with connect() as conn:
            if validate_csrf_token(conn, header_token, sess.value):
                return True
        self.send_error_json(403, "Недействительный CSRF-токен. Обновите страницу.")
        return False

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/session":
            return self.api_session()
        if path == "/api/orders":
            return self.api_orders(parsed.query)
        if path == "/api/makers":
            return self.api_makers()
        if path == "/api/threads":
            return self.api_threads()
        if path == "/api/regions":
            return self.api_regions()
        if path == "/api/company-types":
            return self.api_company_types()
        m = THREAD_ID_RE.match(path)
        if m and path.endswith("/messages"):
            return self.api_messages(int(m.group(1)))
        m = COMPANY_ID_RE.match(path)
        if m and path == f"/api/companies/{m.group(1)}":
            return self.api_company_detail(int(m.group(1)))
        if path == "/api/companies":
            return self.api_companies(parsed.query)
        if path == "/api/services":
            return self.api_services_list(parsed.query)
        if path == "/api/favorites":
            return self.api_favorites_list()
        m = SERVICE_ID_RE.match(path)
        if m and path == f"/api/services/{m.group(1)}":
            return self.api_service_detail(int(m.group(1)))
        if path == "/api/notifications":
            return self.api_notifications_list()
        if path == "/api/notifications/preferences":
            return self.api_notification_preferences()
        m = NOTIFICATION_RE.match(path)
        if m:
            return self.api_notification_read(int(m.group(1)))
        if path == "/api/reviews":
            return self.api_reviews_list(parsed.query)
        if path == "/api/search":
            return self.api_global_search(parsed.query)
        if path == "/api/documents":
            return self.api_documents_list()
        if path == "/api/materials":
            return self.api_materials_list(parsed.query)
        m = MATERIAL_RE.match(path)
        if m:
            return self.api_material_detail(int(m.group(1)))
        if path == "/api/templates":
            return self.api_templates_list()
        m = TEMPLATE_RE.match(path)
        if m:
            return self.api_template_detail(int(m.group(1)))
        if path == "/api/invoices":
            return self.api_invoices_list(parsed.query)
        m = INVOICE_RE.match(path)
        if m:
            return self.api_invoice_detail(int(m.group(1)))
        if path == "/api/order-history":
            return self.api_order_history(parsed.query)
        if path == "/api/delivery":
            return self.api_delivery_list(parsed.query)
        if path == "/api/tfa/status":
            return self.api_tfa_status()
        if path == "/api/suppliers":
            return self.api_suppliers_list(parsed.query)
        m = SUPPLIER_RE.match(path)
        if m:
            return self.api_supplier_detail(int(m.group(1)))
        if path == "/api/certificates":
            return self.api_certificates_list(parsed.query)
        m = CERTIFICATE_RE.match(path)
        if m:
            return self.api_certificate_detail(int(m.group(1)))
        if path == "/api/time-entries":
            return self.api_time_entries_list(parsed.query)
        if path == "/api/client-ratings":
            return self.api_client_ratings_list(parsed.query)
        if path == "/api/admin/stats":
            return self.api_admin_stats()
        if path == "/api/admin/analytics":
            return self.api_admin_analytics()
        if path == "/api/admin/activity":
            return self.api_admin_activity(parsed.query)
        if path == "/api/admin/users":
            return self.api_admin_users(parsed.query)
        m = ADMIN_USER_RE.match(path)
        if m:
            return self.api_admin_user_detail(int(m.group(1)))
        if path == "/api/admin/orders":
            return self.api_admin_orders(parsed.query)
        m = ADMIN_ORDER_RE.match(path)
        if m:
            return self.api_admin_order_detail(int(m.group(1)))
        if path == "/api/admin/services":
            return self.api_admin_services(parsed.query)
        m = ADMIN_SERVICE_RE.match(path)
        if m:
            return self.api_admin_service_detail(int(m.group(1)))
        if path.startswith("/uploads/"):
            return self.serve_upload(path)
        if path in STATIC_FILES:
            return self.serve_static(STATIC_FILES[path])
        return self.send_error_json(404, "Страница не найдена")

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/") and path not in CSRF_EXEMPT_PATHS and not self.check_csrf():
            return
        if path == "/api/register":
            return self.api_register()
        if path == "/api/login":
            return self.api_login()
        if path == "/api/logout":
            return self.api_logout()
        if path == "/api/orders":
            return self.api_create_order()
        if path == "/api/services":
            return self.api_create_service()
        if path == "/api/companies/logo":
            return self.api_upload_logo()
        if path == "/api/favorites":
            return self.api_add_favorite()
        if path == "/api/reviews":
            return self.api_create_review()
        if path == "/api/notifications/read-all":
            return self.api_notifications_read_all()
        if path == "/api/notifications/preferences":
            return self.api_update_notification_preferences()
        if path == "/api/documents":
            return self.api_upload_document()
        if path == "/api/csrf-token":
            return self.api_csrf_token()
        if path == "/api/materials":
            return self.api_create_material()
        if path == "/api/templates":
            return self.api_create_template()
        if path == "/api/invoices":
            return self.api_create_invoice()
        if path == "/api/delivery":
            return self.api_create_delivery()
        if path == "/api/admin/bulk-orders":
            return self.api_admin_bulk_orders()
        if path == "/api/admin/bulk-users":
            return self.api_admin_bulk_users()
        if path == "/api/tfa/setup":
            return self.api_tfa_setup()
        if path == "/api/tfa/verify":
            return self.api_tfa_verify()
        if path == "/api/suppliers":
            return self.api_create_supplier()
        if path == "/api/certificates":
            return self.api_create_certificate()
        if path == "/api/time-entries":
            return self.api_create_time_entry()
        if path == "/api/client-ratings":
            return self.api_create_client_rating()
        if path == "/api/export/excel":
            return self.api_export_excel()
        if path == "/api/auth/token":
            return self.api_auth_token()
        m = ORDER_ID_RE.match(path)
        if m:
            order_id = int(m.group(1))
            if path.endswith("/responses"):
                return self.api_create_response(order_id)
            if path.endswith("/choose"):
                return self.api_choose_maker(order_id)
        m = THREAD_ID_RE.match(path)
        if m and path.endswith("/messages"):
            return self.api_send_message(int(m.group(1)))
        if path == "/api/profile":
            return self.api_profile()
        if path == "/api/admin/orders/status":
            return self.api_admin_update_order_status()
        if path == "/api/admin/users":
            return self.api_admin_create_user()
        return self.send_error_json(404, "Метод не найден")

    def do_PUT(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/") and not self.check_csrf():
            return
        m = SERVICE_ID_RE.match(path)
        if m and path == f"/api/services/{m.group(1)}":
            return self.api_update_service(int(m.group(1)))
        m = MATERIAL_RE.match(path)
        if m:
            return self.api_update_material(int(m.group(1)))
        m = TEMPLATE_RE.match(path)
        if m:
            return self.api_update_template(int(m.group(1)))
        m = SUPPLIER_RE.match(path)
        if m:
            return self.api_update_supplier(int(m.group(1)))
        m = CERTIFICATE_RE.match(path)
        if m:
            return self.api_update_certificate(int(m.group(1)))
        m = ADMIN_USER_RE.match(path)
        if m:
            return self.api_admin_update_user(int(m.group(1)))
        return self.send_error_json(404, "Метод не найден")

    def do_DELETE(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/") and not self.check_csrf():
            return
        if path.startswith("/api/notifications/"):
            notif_id = path.split("/")[-1]
            if notif_id == "clear-all":
                return self.api_notifications_clear_all()
            return self.api_delete_notification(int(notif_id))
        if path.startswith("/api/documents/"):
            doc_id = path.split("/")[-1]
            return self.api_delete_document(int(doc_id))
        m = SERVICE_ID_RE.match(path)
        if m and path == f"/api/services/{m.group(1)}":
            return self.api_delete_service(int(m.group(1)))
        if path.startswith("/api/favorites/"):
            company_id = path.split("/")[-1]
            return self.api_remove_favorite(int(company_id))
        m = MATERIAL_RE.match(path)
        if m:
            return self.api_delete_material(int(m.group(1)))
        m = TEMPLATE_RE.match(path)
        if m:
            return self.api_delete_template(int(m.group(1)))
        m = SUPPLIER_RE.match(path)
        if m:
            return self.api_delete_supplier(int(m.group(1)))
        m = CERTIFICATE_RE.match(path)
        if m:
            return self.api_delete_certificate(int(m.group(1)))
        m = TIMEENTRY_RE.match(path)
        if m:
            return self.api_delete_time_entry(int(m.group(1)))
        m = ADMIN_USER_RE.match(path)
        if m:
            return self.api_admin_delete_user(int(m.group(1)))
        m = ADMIN_ORDER_RE.match(path)
        if m:
            return self.api_admin_delete_order(int(m.group(1)))
        m = ADMIN_SERVICE_RE.match(path)
        if m:
            return self.api_admin_delete_service(int(m.group(1)))
        return self.send_error_json(404, "Метод не найден")

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def read_multipart(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("Файлы слишком большие. Лимит 25 МБ на заявку.")
        content_type = self.headers.get("Content-Type", "")
        marker = "boundary="
        if marker not in content_type:
            raise ValueError("Некорректная multipart-форма")
        boundary = content_type.split(marker, 1)[1].strip().strip('"').encode("utf-8")
        body = self.rfile.read(length)
        fields = {}
        files = []
        for part in body.split(b"--" + boundary):
            part = part.strip(b"\r\n")
            if not part or part == b"--":
                continue
            head, sep, value = part.partition(b"\r\n\r\n")
            if not sep:
                continue
            headers = head.decode("utf-8", errors="ignore").split("\r\n")
            disposition = next((h for h in headers if h.lower().startswith("content-disposition:")), "")
            if "name=" not in disposition:
                continue
            name = disposition.split('name="', 1)[1].split('"', 1)[0]
            value = value.rstrip(b"\r\n")
            if 'filename="' in disposition:
                original = disposition.split('filename="', 1)[1].split('"', 1)[0]
                if not original:
                    continue
                mime = "application/octet-stream"
                for header in headers:
                    if header.lower().startswith("content-type:"):
                        mime = header.split(":", 1)[1].strip()
                files.append({"field": name, "filename": original, "content": value, "mime": mime})
            else:
                fields[name] = value.decode("utf-8", errors="replace")
        return fields, files

    def current_user(self, conn):
        cookie_header = self.headers.get("Cookie", "")
        jar = cookies.SimpleCookie(cookie_header)
        token = jar.get("meblio_session")
        if not token:
            return None
        row = conn.execute(
            """
            SELECT users.*, regions.name AS region_name FROM users
            LEFT JOIN regions ON regions.id = users.region_id
            JOIN sessions ON sessions.user_id = users.id
            WHERE sessions.token = ?
            """,
            (token.value,),
        ).fetchone()
        return row_to_dict(row)

    def require_user(self, conn):
        user = self.current_user(conn)
        if not user:
            self.send_error_json(401, "Нужно войти в личный кабинет")
            return None
        return user

    def require_admin(self, conn):
        user = self.require_user(conn)
        if not user:
            return None
        if user["role"] != "admin":
            self.send_error_json(403, "Доступ запрещён")
            return None
        return user

    def public_user(self, user):
        if not user:
            return None
        return {
            "id": user["id"],
            "role": user["role"],
            "company_type": user["company_type"],
            "name": user["name"],
            "email": user["email"],
            "city": user["city"],
            "region_id": user["region_id"],
            "region_name": user.get("region_name", ""),
            "phone": user["phone"],
            "about": user["about"],
            "skills": [item.strip() for item in user["skills"].split(",") if item.strip()],
            "capacity": user["capacity"],
            "logo": user["logo"],
            "created_at": user["created_at"],
        }

    def order_payload(self, conn, order):
        files = rows_to_list(conn.execute("SELECT * FROM order_files WHERE order_id = ? ORDER BY id", (order["id"],)).fetchall())
        responses = rows_to_list(
            conn.execute(
                """
                SELECT responses.*, users.name AS maker_name, users.city AS maker_city
                FROM responses JOIN users ON users.id = responses.maker_id
                WHERE order_id = ? ORDER BY responses.created_at DESC
                """,
                (order["id"],),
            ).fetchall()
        )
        data = dict(order)
        data["files"] = [
            {
                "id": file["id"],
                "name": file["original_name"],
                "size": file["size"],
                "mime": file["mime"],
                "url": f"/uploads/{file['stored_name']}",
            }
            for file in files
        ]
        data["responses"] = responses
        return data

    def serve_static(self, filename):
        path = BASE_DIR / filename
        if not path.exists():
            return self.send_error_json(404, "Файл не найден")
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def serve_upload(self, path):
        name = unquote(path.replace("/uploads/", "", 1))
        file_path = (UPLOAD_DIR / name).resolve()
        if not str(file_path).startswith(str(UPLOAD_DIR.resolve())) or not file_path.exists():
            return self.send_error_json(404, "Файл не найден")
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.end_headers()
        with file_path.open("rb") as source:
            shutil.copyfileobj(source, self.wfile)

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
                )
                token = secrets.token_urlsafe(32)
                conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)", (token, user_id, now()))
                user = conn.execute("SELECT users.*, regions.name AS region_name FROM users LEFT JOIN regions ON regions.id = users.region_id WHERE users.id = ?", (user_id,)).fetchone()
            self.send_json(200, {"user": self.public_user(row_to_dict(user))}, {"Set-Cookie": f"meblio_session={token}; Path=/; HttpOnly; SameSite=Lax; Secure"})
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
            with connect() as conn:
                user = conn.execute("SELECT users.*, regions.name AS region_name FROM users LEFT JOIN regions ON regions.id = users.region_id WHERE email = ?", (data.get("email", "").strip().lower(),)).fetchone()
                if not user or not verify_password(data.get("password", ""), user["password_salt"], user["password_hash"]):
                    return self.send_error_json(401, "Неверный email или пароль")
                token = secrets.token_urlsafe(32)
                conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)", (token, user["id"], now()))
            self.send_json(200, {"user": self.public_user(row_to_dict(user))}, {"Set-Cookie": f"meblio_session={token}; Path=/; HttpOnly; SameSite=Lax; Secure"})
        except Exception as exc:
            self.send_error_json(400, str(exc))

    def api_logout(self):
        jar = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        token = jar.get("meblio_session")
        if token:
            with connect() as conn:
                conn.execute("DELETE FROM sessions WHERE token = ?", (token.value,))
        self.send_json(200, {"ok": True}, {"Set-Cookie": "meblio_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax; Secure"})

    def api_orders(self, query):
        params = parse_qs(query)
        type_filter = params.get("type", [""])[0]
        city_filter = params.get("city", [""])[0]
        status_filter = params.get("status", [""])[0]
        page = max(1, int(params.get("page", ["1"])[0]))
        offset = (page - 1) * PAGE_SIZE
        where = []
        values = []
        if type_filter:
            where.append("orders.type = ?")
            values.append(type_filter)
        if city_filter:
            where.append("LOWER(orders.city) LIKE ?")
            values.append(f"%{city_filter.lower()}%")
        if status_filter:
            where.append("orders.status = ?")
            values.append(status_filter)
        where_clause = (" WHERE " + " AND ".join(where)) if where else ""
        with connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM orders{where_clause}", values).fetchone()[0]
            sql = f"""
                SELECT orders.*, clients.name AS client_name, makers.name AS selected_maker_name
                FROM orders
                JOIN users clients ON clients.id = orders.client_id
                LEFT JOIN users makers ON makers.id = orders.selected_maker_id
                {where_clause}
                ORDER BY orders.created_at DESC
                LIMIT ? OFFSET ?
            """
            orders = [self.order_payload(conn, row) for row in conn.execute(sql, values + [PAGE_SIZE, offset]).fetchall()]
        self.send_json(200, {"orders": orders, "total": total, "page": page, "page_size": PAGE_SIZE})

    def api_makers(self):
        with connect() as conn:
            makers = [self.public_user(row_to_dict(row)) for row in conn.execute(
                "SELECT users.*, regions.name AS region_name FROM users LEFT JOIN regions ON regions.id = users.region_id WHERE role = 'maker' ORDER BY name"
            ).fetchall()]
        self.send_json(200, {"makers": makers})

    def api_regions(self):
        with connect() as conn:
            regions = rows_to_list(conn.execute("SELECT * FROM regions ORDER BY name").fetchall())
        self.send_json(200, {"regions": regions})

    def api_company_types(self):
        self.send_json(200, {"types": [{"id": t[0], "name": t[1]} for t in COMPANY_TYPES]})

    def api_companies(self, query):
        params = parse_qs(query)
        type_filter = params.get("type", [""])[0]
        region_filter = params.get("region", [""])[0]
        search = params.get("search", [""])[0]
        page = max(1, int(params.get("page", ["1"])[0]))
        offset = (page - 1) * PAGE_SIZE
        where = ["role = 'maker'"]
        values = []
        if type_filter:
            where.append("company_type = ?")
            values.append(type_filter)
        if region_filter:
            where.append("region_id = ?")
            values.append(int(region_filter))
        if search:
            where.append("(LOWER(name) LIKE ? OR LOWER(about) LIKE ?)")
            values.extend([f"%{search.lower()}%", f"%{search.lower()}%"])
        where_clause = " WHERE " + " AND ".join(where)
        with connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM users{where_clause}", values).fetchone()[0]
            sql = f"""
                SELECT users.*, regions.name AS region_name
                FROM users LEFT JOIN regions ON regions.id = users.region_id
                {where_clause}
                ORDER BY name
                LIMIT ? OFFSET ?
            """
            companies = []
            for row in conn.execute(sql, values + [PAGE_SIZE, offset]).fetchall():
                company = self.public_user(row_to_dict(row))
                avg = conn.execute("SELECT AVG(rating) FROM reviews WHERE company_id = ?", (row["id"],)).fetchone()[0]
                company["avg_rating"] = round(avg, 1) if avg else 0
                company["reviews_count"] = conn.execute("SELECT COUNT(*) FROM reviews WHERE company_id = ?", (row["id"],)).fetchone()[0]
                companies.append(company)
        self.send_json(200, {"companies": companies, "total": total, "page": page, "page_size": PAGE_SIZE})

    def api_company_detail(self, company_id):
        with connect() as conn:
            user = conn.execute("SELECT users.*, regions.name AS region_name FROM users LEFT JOIN regions ON regions.id = users.region_id WHERE users.id = ?", (company_id,)).fetchone()
            if not user:
                return self.send_error_json(404, "Компания не найдена")
            services = rows_to_list(conn.execute("SELECT * FROM services WHERE user_id = ? ORDER BY created_at DESC", (company_id,)).fetchall())
            gallery = rows_to_list(conn.execute("SELECT * FROM company_gallery WHERE user_id = ? ORDER BY id", (company_id,)).fetchall())
            orders_count = conn.execute("SELECT COUNT(*) FROM orders WHERE client_id = ?", (company_id,)).fetchone()[0]
            responses_count = conn.execute("SELECT COUNT(*) FROM responses WHERE maker_id = ?", (company_id,)).fetchone()[0]
            reviews = rows_to_list(conn.execute(
                "SELECT reviews.*, users.name AS reviewer_name FROM reviews JOIN users ON users.id = reviews.reviewer_id WHERE reviews.company_id = ? ORDER BY reviews.created_at DESC",
                (company_id,),
            ).fetchall())
            avg_rating = conn.execute("SELECT AVG(rating) FROM reviews WHERE company_id = ?", (company_id,)).fetchone()[0]
            docs = rows_to_list(conn.execute(
                "SELECT id, original_name, doc_type, size FROM company_documents WHERE user_id = ? ORDER BY created_at DESC",
                (company_id,),
            ).fetchall())
        data = self.public_user(row_to_dict(user))
        data["services"] = services
        data["gallery"] = [{"id": g["id"], "name": g["original_name"], "url": f"/uploads/{g['stored_name']}"} for g in gallery]
        data["orders_count"] = orders_count
        data["responses_count"] = responses_count
        data["reviews"] = reviews
        data["avg_rating"] = round(avg_rating, 1) if avg_rating else 0
        data["reviews_count"] = len(reviews)
        data["documents"] = docs
        self.send_json(200, {"company": data})

    def api_create_order(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            if user["role"] != "client":
                return self.send_error_json(403, "Размещать заказы может только заказчик")
            try:
                fields, files = self.read_multipart()
                cur = conn.execute(
                    """
                    INSERT INTO orders (client_id, title, type, quantity, city, budget, deadline, details, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
                    """,
                    (
                        user["id"],
                        fields.get("title", "").strip(),
                        fields.get("type", "").strip(),
                        int(fields.get("quantity", "0")),
                        fields.get("city", "").strip(),
                        int(fields.get("budget", "0")),
                        fields.get("deadline", "").strip(),
                        fields.get("details", "").strip(),
                        now(),
                    ),
                )
                order_id = cur.lastrowid
                for file in files:
                    original = safe_filename(file["filename"])
                    stored = f"{order_id}_{secrets.token_hex(8)}_{original}"
                    (UPLOAD_DIR / stored).write_bytes(file["content"])
                    conn.execute(
                        "INSERT INTO order_files (order_id, original_name, stored_name, size, mime, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (order_id, original, stored, len(file["content"]), file["mime"], now()),
                    )
                order = conn.execute("SELECT orders.*, users.name AS client_name, NULL AS selected_maker_name FROM orders JOIN users ON users.id = orders.client_id WHERE orders.id = ?", (order_id,)).fetchone()
                # Notify all makers about new order
                makers = conn.execute("SELECT id FROM users WHERE role = 'maker'").fetchall()
                for m in makers:
                    create_notification(conn, m["id"], "new_order",
                        "Новый заказ", f"{user['name']} создал заказ: {fields.get('title', '')}",
                        f"/market")
                self.send_json(200, {"order": self.order_payload(conn, order)})
            except Exception as exc:
                self.send_error_json(400, str(exc))

    def api_create_response(self, order_id):
        try:
            data = self.read_json()
            with connect() as conn:
                user = self.require_user(conn)
                if not user:
                    return
                if user["role"] != "maker":
                    return self.send_error_json(403, "Откликаться может только производитель")
                order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
                if not order:
                    return self.send_error_json(404, "Заказ не найден")
                conn.execute(
                    "INSERT INTO responses (order_id, maker_id, price, days, message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (order_id, user["id"], int(data.get("price", 0)), int(data.get("days", 0)), data.get("message", "").strip(), now()),
                )
                thread_id = ensure_thread(conn, order_id, order["client_id"], user["id"])
                conn.execute(
                    "INSERT INTO messages (thread_id, author_id, body, created_at) VALUES (?, ?, ?, ?)",
                    (thread_id, user["id"], f"Отклик: {data.get('message', '').strip()}", now()),
                )
                create_notification(conn, order["client_id"], "response",
                    "Новый отклик", f"{user['name']} откликнулся на заказ",
                    f"/order/{order_id}")
            self.send_json(200, {"ok": True})
        except sqlite3.IntegrityError:
            self.send_error_json(409, "Вы уже откликались на этот заказ")
        except Exception as exc:
            self.send_error_json(400, str(exc))

    def api_choose_maker(self, order_id):
        data = self.read_json()
        maker_id = int(data.get("maker_id", 0))
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not order or order["client_id"] != user["id"]:
                return self.send_error_json(403, "Нет доступа к заказу")
            conn.execute("UPDATE orders SET selected_maker_id = ?, status = 'progress' WHERE id = ?", (maker_id, order_id))
            self.log_order_change(conn, order_id, user["id"], "status", order["status"], "progress")
            self.log_order_change(conn, order_id, user["id"], "selected_maker_id", order["selected_maker_id"] or "", str(maker_id))
            thread_id = ensure_thread(conn, order_id, user["id"], maker_id)
            conn.execute(
                "INSERT INTO messages (thread_id, author_id, body, created_at) VALUES (?, ?, ?, ?)",
                (thread_id, user["id"], "Выбрали вас исполнителем. Давайте согласуем следующий шаг.", now()),
            )
            create_notification(conn, maker_id, "chosen",
                "Вы выбраны исполнителем", f"{user['name']} выбрал вас для заказа: {order['title']}",
                f"/order/{order_id}")
        self.send_json(200, {"ok": True})

    def api_threads(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            if user["role"] == "client":
                rows = conn.execute(
                    """
                    SELECT threads.*, orders.title AS order_title, users.name AS companion_name
                    FROM threads
                    JOIN orders ON orders.id = threads.order_id
                    JOIN users ON users.id = threads.maker_id
                    WHERE threads.client_id = ?
                    ORDER BY threads.created_at DESC
                    """,
                    (user["id"],),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT threads.*, orders.title AS order_title, users.name AS companion_name
                    FROM threads
                    JOIN orders ON orders.id = threads.order_id
                    JOIN users ON users.id = threads.client_id
                    WHERE threads.maker_id = ?
                    ORDER BY threads.created_at DESC
                    """,
                    (user["id"],),
                ).fetchall()
        self.send_json(200, {"threads": rows_to_list(rows)})

    def api_messages(self, thread_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            thread = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
            if not thread or user["id"] not in (thread["client_id"], thread["maker_id"]):
                return self.send_error_json(403, "Нет доступа к переписке")
            messages = rows_to_list(
                conn.execute(
                    """
                    SELECT messages.*, users.name AS author_name
                    FROM messages JOIN users ON users.id = messages.author_id
                    WHERE thread_id = ? ORDER BY messages.id
                    """,
                    (thread_id,),
                ).fetchall()
            )
        self.send_json(200, {"messages": messages})

    def api_send_message(self, thread_id):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            thread = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
            if not thread or user["id"] not in (thread["client_id"], thread["maker_id"]):
                return self.send_error_json(403, "Нет доступа к переписке")
            cur = conn.execute(
                "INSERT INTO messages (thread_id, author_id, body, created_at) VALUES (?, ?, ?, ?)",
                (thread_id, user["id"], data.get("body", "").strip(), now()),
            )
            msg = conn.execute(
                "SELECT m.*, u.name AS author_name FROM messages m JOIN users u ON u.id = m.author_id WHERE m.id = ?",
                (cur.lastrowid,),
            ).fetchone()
        # Broadcast to WebSocket subscribers
        try:
            from ws_server import ws_manager
            ws_manager.broadcast_to_thread(thread_id, {
                "type": "message",
                "thread_id": thread_id,
                "message": row_to_dict(msg),
            })
        except ImportError:
            pass
        # Notify recipient
        with connect() as conn:
            notify_user = thread["client_id"] if user["id"] == thread["maker_id"] else thread["maker_id"]
            create_notification(conn, notify_user, "message",
                "Новое сообщение", f"{user['name']}: {data.get('body', '')[:100]}",
                f"/chat")
        self.send_json(200, {"ok": True, "message": row_to_dict(msg)})

    def api_profile(self):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            region_id = data.get("region_id")
            if region_id:
                region_id = int(region_id)
            conn.execute(
                """
                UPDATE users SET name = ?, city = ?, region_id = ?, phone = ?, about = ?, skills = ?, capacity = ?
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
                    user["id"],
                ),
            )
            updated = conn.execute("SELECT users.*, regions.name AS region_name FROM users LEFT JOIN regions ON regions.id = users.region_id WHERE users.id = ?", (user["id"],)).fetchone()
        self.send_json(200, {"user": self.public_user(row_to_dict(updated))})

    def api_services_list(self, query):
        params = parse_qs(query)
        user_id = params.get("user_id", [""])[0]
        page = max(1, int(params.get("page", ["1"])[0]))
        offset = (page - 1) * PAGE_SIZE
        where = []
        values = []
        if user_id:
            where.append("services.user_id = ?")
            values.append(int(user_id))
        where_clause = (" WHERE " + " AND ".join(where)) if where else ""
        with connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM services{where_clause}", values).fetchone()[0]
            sql = f"""
                SELECT services.*, users.name AS company_name, users.city AS company_city
                FROM services JOIN users ON users.id = services.user_id
                {where_clause}
                ORDER BY services.created_at DESC
                LIMIT ? OFFSET ?
            """
            services = rows_to_list(conn.execute(sql, values + [PAGE_SIZE, offset]).fetchall())
        self.send_json(200, {"services": services, "total": total, "page": page, "page_size": PAGE_SIZE})

    def api_service_detail(self, service_id):
        with connect() as conn:
            service = conn.execute(
                "SELECT services.*, users.name AS company_name, users.city AS company_city FROM services JOIN users ON users.id = services.user_id WHERE services.id = ?",
                (service_id,),
            ).fetchone()
            if not service:
                return self.send_error_json(404, "Услуга не найдена")
            files = rows_to_list(conn.execute("SELECT * FROM service_files WHERE service_id = ? ORDER BY id", (service_id,)).fetchall())
        data = dict(service)
        data["files"] = [{"id": f["id"], "name": f["original_name"], "url": f"/uploads/{f['stored_name']}"} for f in files]
        self.send_json(200, {"service": data})

    def api_create_service(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            if user["role"] != "maker":
                return self.send_error_json(403, "Услуги может добавлять только производитель")
            try:
                fields, files = self.read_multipart()
                cur = conn.execute(
                    "INSERT INTO services (user_id, title, description, price_type, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user["id"], fields.get("title", "").strip(), fields.get("description", "").strip(), fields.get("price_type", "").strip(), now()),
                )
                service_id = cur.lastrowid
                for file in files:
                    original = safe_filename(file["filename"])
                    stored = f"svc_{service_id}_{secrets.token_hex(8)}_{original}"
                    (UPLOAD_DIR / stored).write_bytes(file["content"])
                    conn.execute(
                        "INSERT INTO service_files (service_id, original_name, stored_name, size, mime, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (service_id, original, stored, len(file["content"]), file["mime"], now()),
                    )
                self.send_json(200, {"ok": True, "service_id": service_id})
            except Exception as exc:
                self.send_error_json(400, str(exc))

    def api_update_service(self, service_id):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            service = conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()
            if not service or service["user_id"] != user["id"]:
                return self.send_error_json(403, "Нет доступа к услуге")
            conn.execute(
                "UPDATE services SET title = ?, description = ?, price_type = ? WHERE id = ?",
                (data.get("title", "").strip(), data.get("description", "").strip(), data.get("price_type", "").strip(), service_id),
            )
        self.send_json(200, {"ok": True})

    def api_delete_service(self, service_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            service = conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()
            if not service or service["user_id"] != user["id"]:
                return self.send_error_json(403, "Нет доступа к услуге")
            conn.execute("DELETE FROM service_files WHERE service_id = ?", (service_id,))
            conn.execute("DELETE FROM services WHERE id = ?", (service_id,))
        self.send_json(200, {"ok": True})

    def api_upload_logo(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            try:
                fields, files = self.read_multipart()
                if not files:
                    return self.send_error_json(400, "Файл не загружен")
                file = files[0]
                original = safe_filename(file["filename"])
                stored = f"logo_{user['id']}_{secrets.token_hex(8)}_{original}"
                (UPLOAD_DIR / stored).write_bytes(file["content"])
                conn.execute("UPDATE users SET logo = ? WHERE id = ?", (stored, user["id"]))
                self.send_json(200, {"ok": True, "logo": f"/uploads/{stored}"})
            except Exception as exc:
                self.send_error_json(400, str(exc))

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
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            if company_id == user["id"]:
                return self.send_error_json(400, "Нельзя добавить себя")
            try:
                conn.execute(
                    "INSERT INTO favorites (user_id, company_id, created_at) VALUES (?, ?, ?)",
                    (user["id"], company_id, now()),
                )
                self.send_json(200, {"ok": True})
            except sqlite3.IntegrityError:
                self.send_error_json(409, "Уже в избранном")

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

    def api_admin_stats(self):
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            users_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            makers_count = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'maker'").fetchone()[0]
            clients_count = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'client'").fetchone()[0]
            orders_count = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            open_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'open'").fetchone()[0]
            progress_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'progress'").fetchone()[0]
            closed_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'closed'").fetchone()[0]
            services_count = conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]
            responses_count = conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
            messages_count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            total_budget = conn.execute("SELECT COALESCE(SUM(budget), 0) FROM orders").fetchone()[0]
            avg_budget = conn.execute("SELECT COALESCE(AVG(budget), 0) FROM orders").fetchone()[0]
            reviews_count = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
            avg_rating = conn.execute("SELECT COALESCE(AVG(rating), 0) FROM reviews").fetchone()[0]
            new_users_week = conn.execute("SELECT COUNT(*) FROM users WHERE created_at >= datetime('now', '-7 days')").fetchone()[0]
            new_orders_week = conn.execute("SELECT COUNT(*) FROM orders WHERE created_at >= datetime('now', '-7 days')").fetchone()[0]
        self.send_json(200, {
            "users": users_count, "makers": makers_count, "clients": clients_count,
            "orders": orders_count, "open_orders": open_orders, "progress_orders": progress_orders, "closed_orders": closed_orders,
            "services": services_count, "responses": responses_count, "messages": messages_count,
            "total_budget": total_budget, "avg_budget": round(avg_budget),
            "reviews_count": reviews_count, "avg_rating": round(avg_rating, 1),
            "new_users_week": new_users_week, "new_orders_week": new_orders_week,
        })

    def api_admin_analytics(self):
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            # Orders by status
            by_status = {row[0]: row[1] for row in conn.execute("SELECT status, COUNT(*) FROM orders GROUP BY status").fetchall()}
            # Orders by type
            by_type = {row[0]: row[1] for row in conn.execute("SELECT type, COUNT(*) FROM orders GROUP BY type").fetchall()}
            # Users by role
            by_role = {row[0]: row[1] for row in conn.execute("SELECT role, COUNT(*) FROM users GROUP BY role").fetchall()}
            # Users by company_type
            by_company = {row[0]: row[1] for row in conn.execute("SELECT company_type, COUNT(*) FROM users WHERE role='maker' GROUP BY company_type").fetchall()}
            # Recent orders (last 10)
            recent_orders = rows_to_list(conn.execute(
                "SELECT id, title, status, budget, created_at FROM orders ORDER BY created_at DESC LIMIT 10"
            ).fetchall())
            # Recent users (last 10)
            recent_users = rows_to_list(conn.execute(
                "SELECT id, name, role, email, created_at FROM users ORDER BY created_at DESC LIMIT 10"
            ).fetchall())
            # Top makers by responses
            top_makers = rows_to_list(conn.execute(
                "SELECT users.name, COUNT(responses.id) as cnt FROM responses JOIN users ON users.id = responses.maker_id GROUP BY responses.maker_id ORDER BY cnt DESC LIMIT 5"
            ).fetchall())
            # Revenue by month
            revenue = rows_to_list(conn.execute(
                "SELECT strftime('%Y-%m', created_at) as month, SUM(budget) as total, COUNT(*) as count FROM orders WHERE status IN ('progress','closed') GROUP BY month ORDER BY month DESC LIMIT 12"
            ).fetchall())
            # Conversion rate (orders with responses / total orders)
            total_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
            orders_with_responses = conn.execute("SELECT COUNT(DISTINCT order_id) FROM responses").fetchone()[0]
            conversion_rate = round(orders_with_responses / total_orders * 100, 1) if total_orders else 0
            # Average responses per order
            avg_responses = round(conn.execute("SELECT COALESCE(AVG(cnt), 0) FROM (SELECT COUNT(*) as cnt FROM responses GROUP BY order_id)").fetchone()[0], 1)
            # Orders by city (top 10)
            by_city = rows_to_list(conn.execute(
                "SELECT city, COUNT(*) as cnt, SUM(budget) as total_budget FROM orders GROUP BY city ORDER BY cnt DESC LIMIT 10"
            ).fetchall())
            # Orders by region
            by_region = rows_to_list(conn.execute(
                "SELECT regions.name as region, COUNT(*) as cnt FROM orders JOIN users ON users.id = orders.client_id JOIN regions ON regions.id = users.region_id GROUP BY regions.name ORDER BY cnt DESC LIMIT 10"
            ).fetchall())
            # Activity by hour (orders created)
            by_hour = {str(row[0]): row[1] for row in conn.execute(
                "SELECT CAST(strftime('%H', created_at) AS INTEGER) as hour, COUNT(*) FROM orders GROUP BY hour ORDER BY hour"
            ).fetchall()}
            # Activity by day of week
            by_dow = {str(row[0]): row[1] for row in conn.execute(
                "SELECT CAST(strftime('%w', created_at) AS INTEGER) as dow, COUNT(*) FROM orders GROUP BY dow ORDER BY dow"
            ).fetchall()}
            # Top clients by orders
            top_clients = rows_to_list(conn.execute(
                "SELECT users.name, COUNT(orders.id) as cnt, SUM(orders.budget) as total FROM orders JOIN users ON users.id = orders.client_id GROUP BY orders.client_id ORDER BY cnt DESC LIMIT 5"
            ).fetchall())
            # Services statistics
            services_count = conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]
            services_with_files = conn.execute("SELECT COUNT(DISTINCT service_id) FROM service_files").fetchone()[0]
            # Messages statistics
            total_messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            active_threads = conn.execute("SELECT COUNT(DISTINCT thread_id) FROM messages").fetchone()[0]
            avg_messages_per_thread = round(total_messages / active_threads, 1) if active_threads else 0
            # User growth (last 12 months)
            user_growth = rows_to_list(conn.execute(
                "SELECT strftime('%Y-%m', created_at) as month, COUNT(*) as count FROM users GROUP BY month ORDER BY month DESC LIMIT 12"
            ).fetchall())
            # Order growth (last 12 months)
            order_growth = rows_to_list(conn.execute(
                "SELECT strftime('%Y-%m', created_at) as month, COUNT(*) as count FROM orders GROUP BY month ORDER BY month DESC LIMIT 12"
            ).fetchall())
            # Completion rate
            completed = conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'closed'").fetchone()[0]
            completion_rate = round(completed / total_orders * 100, 1) if total_orders else 0
            # Average budget by type
            avg_budget_by_type = rows_to_list(conn.execute(
                "SELECT type, ROUND(AVG(budget)) as avg_budget, COUNT(*) as count FROM orders GROUP BY type"
            ).fetchall())
        self.send_json(200, {
            "by_status": by_status, "by_type": by_type, "by_role": by_role,
            "by_company": by_company, "recent_orders": recent_orders,
            "recent_users": recent_users, "top_makers": top_makers, "revenue": revenue,
            "conversion_rate": conversion_rate, "avg_responses": avg_responses,
            "by_city": by_city, "by_region": by_region,
            "by_hour": by_hour, "by_dow": by_dow,
            "top_clients": top_clients,
            "services_count": services_count, "services_with_files": services_with_files,
            "total_messages": total_messages, "active_threads": active_threads,
            "avg_messages_per_thread": avg_messages_per_thread,
            "user_growth": user_growth, "order_growth": order_growth,
            "completion_rate": completion_rate,
            "avg_budget_by_type": avg_budget_by_type,
        })

    def api_admin_activity(self, query):
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            params = parse_qs(query)
            page = max(1, int(params.get("page", ["1"])[0]))
            offset = (page - 1) * PAGE_SIZE
            total = conn.execute("SELECT COUNT(*) FROM admin_activity").fetchone()[0]
            rows = rows_to_list(conn.execute(
                """
                SELECT admin_activity.*, users.name AS admin_name
                FROM admin_activity JOIN users ON users.id = admin_activity.admin_id
                ORDER BY admin_activity.created_at DESC LIMIT ? OFFSET ?
                """,
                (PAGE_SIZE, offset),
            ).fetchall())
        self.send_json(200, {"activity": rows, "total": total, "page": page, "page_size": PAGE_SIZE})

    def log_admin_activity(self, conn, admin_id, action, target_type="", target_id=None, details=""):
        conn.execute(
            "INSERT INTO admin_activity (admin_id, action, target_type, target_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (admin_id, action, target_type, target_id, details, now()),
        )

    def api_admin_create_user(self):
        try:
            data = self.read_json()
            with connect() as conn:
                admin = self.require_admin(conn)
                if not admin:
                    return
                role = data.get("role", "client")
                if role not in ("client", "maker", "admin"):
                    return self.send_error_json(400, "Некорректная роль")
                if len(data.get("password", "")) < 6:
                    return self.send_error_json(400, "Пароль не короче 6 символов")
                user_id = create_user(
                    conn, role,
                    data.get("name", "").strip(),
                    data.get("email", "").strip(),
                    data.get("password", ""),
                    data.get("city", "").strip(),
                    data.get("phone", "").strip(),
                    company_type=data.get("company_type", "client"),
                )
                self.log_admin_activity(conn, admin["id"], "create_user", "user", user_id, f"Создан пользователь {data.get('name', '')}")
            self.send_json(200, {"ok": True, "user_id": user_id})
        except sqlite3.IntegrityError:
            self.send_error_json(409, "Пользователь с таким email уже существует")
        except Exception as exc:
            self.send_error_json(400, str(exc))

    def api_admin_users(self, query):
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            params = parse_qs(query)
            role_filter = params.get("role", [""])[0]
            search = params.get("search", [""])[0]
            page = max(1, int(params.get("page", ["1"])[0]))
            offset = (page - 1) * PAGE_SIZE
            where = []
            values = []
            if role_filter:
                where.append("role = ?")
                values.append(role_filter)
            if search:
                where.append("(LOWER(name) LIKE ? OR LOWER(email) LIKE ?)")
                values.extend([f"%{search.lower()}%", f"%{search.lower()}%"])
            where_clause = (" WHERE " + " AND ".join(where)) if where else ""
            total = conn.execute(f"SELECT COUNT(*) FROM users{where_clause}", values).fetchone()[0]
            sql = f"""
                SELECT users.id, users.role, users.company_type, users.name, users.email, users.city,
                       users.phone, users.created_at, regions.name AS region_name
                FROM users LEFT JOIN regions ON regions.id = users.region_id
                {where_clause}
                ORDER BY users.created_at DESC
                LIMIT ? OFFSET ?
            """
            users = rows_to_list(conn.execute(sql, values + [PAGE_SIZE, offset]).fetchall())
        self.send_json(200, {"users": users, "total": total, "page": page, "page_size": PAGE_SIZE})

    def api_admin_user_detail(self, user_id):
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            user = conn.execute(
                "SELECT users.id, users.role, users.company_type, users.name, users.email, users.city, users.region_id, users.phone, users.about, users.skills, users.capacity, users.created_at, regions.name AS region_name FROM users LEFT JOIN regions ON regions.id = users.region_id WHERE users.id = ?",
                (user_id,),
            ).fetchone()
            if not user:
                return self.send_error_json(404, "Пользователь не найден")
            orders_count = conn.execute("SELECT COUNT(*) FROM orders WHERE client_id = ?", (user_id,)).fetchone()[0]
            responses_count = conn.execute("SELECT COUNT(*) FROM responses WHERE maker_id = ?", (user_id,)).fetchone()[0]
        data = row_to_dict(user)
        data["orders_count"] = orders_count
        data["responses_count"] = responses_count
        self.send_json(200, {"user": data})

    def api_admin_update_user(self, user_id):
        data = self.read_json()
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                return self.send_error_json(404, "Пользователь не найден")
            role = data.get("role", user["role"])
            if role not in ("client", "maker", "admin"):
                return self.send_error_json(400, "Некорректная роль")
            conn.execute("UPDATE users SET role = ?, name = ?, city = ?, phone = ? WHERE id = ?", (role, data.get("name", user["name"]), data.get("city", user["city"]), data.get("phone", user["phone"]), user_id))
            self.log_admin_activity(conn, admin["id"], "update_user", "user", user_id, f"Обновлён пользователь {data.get('name', user['name'])}")
        self.send_json(200, {"ok": True})

    def api_admin_delete_user(self, user_id):
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            if user_id == admin["id"]:
                return self.send_error_json(400, "Нельзя удалить себя")
            user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if not user:
                return self.send_error_json(404, "Пользователь не найден")
            conn.execute("DELETE FROM messages WHERE author_id = ?", (user_id,))
            conn.execute("DELETE FROM favorites WHERE user_id = ? OR company_id = ?", (user_id, user_id))
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            self.log_admin_activity(conn, admin["id"], "delete_user", "user", user_id, f"Удалён пользователь {user['name']}")
        self.send_json(200, {"ok": True})

    def api_admin_orders(self, query):
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            params = parse_qs(query)
            status_filter = params.get("status", [""])[0]
            search = params.get("search", [""])[0]
            page = max(1, int(params.get("page", ["1"])[0]))
            offset = (page - 1) * PAGE_SIZE
            where = []
            values = []
            if status_filter:
                where.append("orders.status = ?")
                values.append(status_filter)
            if search:
                where.append("LOWER(orders.title) LIKE ?")
                values.append(f"%{search.lower()}%")
            where_clause = (" WHERE " + " AND ".join(where)) if where else ""
            total = conn.execute(f"SELECT COUNT(*) FROM orders{where_clause}", values).fetchone()[0]
            sql = f"""
                SELECT orders.*, clients.name AS client_name, makers.name AS selected_maker_name
                FROM orders
                JOIN users clients ON clients.id = orders.client_id
                LEFT JOIN users makers ON makers.id = orders.selected_maker_id
                {where_clause}
                ORDER BY orders.created_at DESC
                LIMIT ? OFFSET ?
            """
            orders = rows_to_list(conn.execute(sql, values + [PAGE_SIZE, offset]).fetchall())
        self.send_json(200, {"orders": orders, "total": total, "page": page, "page_size": PAGE_SIZE})

    def api_admin_order_detail(self, order_id):
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            order = conn.execute(
                "SELECT orders.*, clients.name AS client_name FROM orders JOIN users clients ON clients.id = orders.client_id WHERE orders.id = ?",
                (order_id,),
            ).fetchone()
            if not order:
                return self.send_error_json(404, "Заказ не найден")
            files = rows_to_list(conn.execute("SELECT * FROM order_files WHERE order_id = ? ORDER BY id", (order_id,)).fetchall())
            responses = rows_to_list(conn.execute("SELECT responses.*, users.name AS maker_name FROM responses JOIN users ON users.id = responses.maker_id WHERE order_id = ?", (order_id,)).fetchall())
        data = dict(order)
        data["files"] = files
        data["responses"] = responses
        self.send_json(200, {"order": data})

    def api_admin_update_order_status(self):
        data = self.read_json()
        order_id = int(data.get("order_id", 0))
        status = data.get("status", "")
        if status not in ("open", "progress", "closed"):
            return self.send_error_json(400, "Некорректный статус")
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not order:
                return self.send_error_json(404, "Заказ не найден")
            conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
            self.log_admin_activity(conn, admin["id"], "update_order_status", "order", order_id, f"Статус заказа #{order_id} -> {status}")
        self.send_json(200, {"ok": True})

    def api_admin_delete_order(self, order_id):
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not order:
                return self.send_error_json(404, "Заказ не найден")
            conn.execute("DELETE FROM messages WHERE thread_id IN (SELECT id FROM threads WHERE order_id = ?)", (order_id,))
            conn.execute("DELETE FROM threads WHERE order_id = ?", (order_id,))
            conn.execute("DELETE FROM responses WHERE order_id = ?", (order_id,))
            conn.execute("DELETE FROM order_files WHERE order_id = ?", (order_id,))
            conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))
            self.log_admin_activity(conn, admin["id"], "delete_order", "order", order_id, f"Удалён заказ #{order_id}: {order['title']}")
        self.send_json(200, {"ok": True})

    def api_admin_services(self, query):
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            params = parse_qs(query)
            search = params.get("search", [""])[0]
            page = max(1, int(params.get("page", ["1"])[0]))
            offset = (page - 1) * PAGE_SIZE
            where = []
            values = []
            if search:
                where.append("LOWER(services.title) LIKE ?")
                values.append(f"%{search.lower()}%")
            where_clause = (" WHERE " + " AND ".join(where)) if where else ""
            total = conn.execute(f"SELECT COUNT(*) FROM services{where_clause}", values).fetchone()[0]
            sql = f"""
                SELECT services.*, users.name AS company_name, users.city AS company_city
                FROM services JOIN users ON users.id = services.user_id
                {where_clause}
                ORDER BY services.created_at DESC
                LIMIT ? OFFSET ?
            """
            services = rows_to_list(conn.execute(sql, values + [PAGE_SIZE, offset]).fetchall())
        self.send_json(200, {"services": services, "total": total, "page": page, "page_size": PAGE_SIZE})

    def api_admin_service_detail(self, service_id):
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            service = conn.execute(
                "SELECT services.*, users.name AS company_name FROM services JOIN users ON users.id = services.user_id WHERE services.id = ?",
                (service_id,),
            ).fetchone()
            if not service:
                return self.send_error_json(404, "Услуга не найдена")
        self.send_json(200, {"service": row_to_dict(service)})

    def api_admin_delete_service(self, service_id):
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            service = conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()
            if not service:
                return self.send_error_json(404, "Услуга не найдена")
            conn.execute("DELETE FROM service_files WHERE service_id = ?", (service_id,))
            conn.execute("DELETE FROM services WHERE id = ?", (service_id,))
            self.log_admin_activity(conn, admin["id"], "delete_service", "service", service_id, f"Удалена услуга: {service['title']}")
        self.send_json(200, {"ok": True})

    # --- Materials ---
    def api_materials_list(self, query):
        params = parse_qs(query)
        category = params.get("category", [""])[0]
        where = ["is_active = 1"]
        values = []
        if category:
            where.append("category = ?")
            values.append(category)
        where_clause = " WHERE " + " AND ".join(where)
        with connect() as conn:
            materials = rows_to_list(conn.execute(f"SELECT * FROM materials{where_clause} ORDER BY category, name", values).fetchall())
        self.send_json(200, {"materials": materials})

    def api_material_detail(self, material_id):
        with connect() as conn:
            mat = conn.execute("SELECT * FROM materials WHERE id = ?", (material_id,)).fetchone()
            if not mat:
                return self.send_error_json(404, "Материал не найден")
        self.send_json(200, {"material": dict(mat)})

    def api_create_material(self):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            if user["role"] != "admin":
                return self.send_error_json(403, "Только админ")
            cur = conn.execute(
                "INSERT INTO materials (name, category, price_per_m2, thickness_mm, description, color, brand, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (data.get("name", ""), data.get("category", "ldsp"), int(data.get("price_per_m2", 0)),
                 int(data.get("thickness_mm", 18)), data.get("description", ""), data.get("color", ""),
                 data.get("brand", ""), now()),
            )
        self.send_json(200, {"ok": True, "id": cur.lastrowid})

    def api_update_material(self, material_id):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user or user["role"] != "admin":
                return self.send_error_json(403, "Только админ")
            conn.execute(
                "UPDATE materials SET name=?, category=?, price_per_m2=?, thickness_mm=?, description=?, color=?, brand=? WHERE id=?",
                (data.get("name", ""), data.get("category", "ldsp"), int(data.get("price_per_m2", 0)),
                 int(data.get("thickness_mm", 18)), data.get("description", ""), data.get("color", ""),
                 data.get("brand", ""), material_id),
            )
        self.send_json(200, {"ok": True})

    def api_delete_material(self, material_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user or user["role"] != "admin":
                return self.send_error_json(403, "Только админ")
            conn.execute("DELETE FROM materials WHERE id = ?", (material_id,))
        self.send_json(200, {"ok": True})

    # --- Order Templates ---
    def api_templates_list(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            templates = rows_to_list(conn.execute(
                "SELECT * FROM order_templates WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)
            ).fetchall())
        self.send_json(200, {"templates": templates})

    def api_template_detail(self, template_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            t = conn.execute("SELECT * FROM order_templates WHERE id = ? AND user_id = ?", (template_id, user["id"])).fetchone()
            if not t:
                return self.send_error_json(404, "Шаблон не найден")
        self.send_json(200, {"template": dict(t)})

    def api_create_template(self):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            cur = conn.execute(
                "INSERT INTO order_templates (user_id, name, type, quantity, city, budget, deadline, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (user["id"], data.get("name", ""), data.get("type", ""), int(data.get("quantity", 1)),
                 data.get("city", ""), int(data.get("budget", 0)), data.get("deadline", ""),
                 data.get("details", ""), now()),
            )
        self.send_json(200, {"ok": True, "id": cur.lastrowid})

    def api_update_template(self, template_id):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            conn.execute(
                "UPDATE order_templates SET name=?, type=?, quantity=?, city=?, budget=?, deadline=?, details=? WHERE id=? AND user_id=?",
                (data.get("name", ""), data.get("type", ""), int(data.get("quantity", 1)),
                 data.get("city", ""), int(data.get("budget", 0)), data.get("deadline", ""),
                 data.get("details", ""), template_id, user["id"]),
            )
        self.send_json(200, {"ok": True})

    def api_delete_template(self, template_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            conn.execute("DELETE FROM order_templates WHERE id = ? AND user_id = ?", (template_id, user["id"]))
        self.send_json(200, {"ok": True})

    # --- Invoices ---
    def api_invoices_list(self, query):
        params = parse_qs(query)
        order_id = params.get("order_id", [""])[0]
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            where = "(from_user_id = ? OR to_user_id = ?)"
            values = [user["id"], user["id"]]
            if order_id:
                where += " AND order_id = ?"
                values.append(int(order_id))
            invoices = rows_to_list(conn.execute(
                f"""SELECT invoices.*, orders.title as order_title,
                    f.name as from_name, t.name as to_name
                    FROM invoices
                    JOIN orders ON orders.id = invoices.order_id
                    JOIN users f ON f.id = invoices.from_user_id
                    JOIN users t ON t.id = invoices.to_user_id
                    WHERE {where} ORDER BY invoices.created_at DESC""",
                values,
            ).fetchall())
        self.send_json(200, {"invoices": invoices})

    def api_invoice_detail(self, invoice_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            inv = conn.execute(
                """SELECT invoices.*, orders.title as order_title, orders.budget,
                    f.name as from_name, f.email as from_email, f.phone as from_phone,
                    t.name as to_name, t.email as to_email, t.phone as to_phone
                    FROM invoices JOIN orders ON orders.id = invoices.order_id
                    JOIN users f ON f.id = invoices.from_user_id
                    JOIN users t ON t.id = invoices.to_user_id
                    WHERE invoices.id = ?""",
                (invoice_id,),
            ).fetchone()
            if not inv:
                return self.send_error_json(404, "Счёт не найден")
        self.send_json(200, {"invoice": dict(inv)})

    def api_create_invoice(self):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            order_id = int(data.get("order_id", 0))
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not order:
                return self.send_error_json(404, "Заказ не найден")
            to_user_id = int(data.get("to_user_id", 0))
            cur = conn.execute(
                "INSERT INTO invoices (order_id, from_user_id, to_user_id, amount, status, due_date, items, created_at) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
                (order_id, user["id"], to_user_id, int(data.get("amount", 0)),
                 data.get("due_date", ""), data.get("items", "[]"), now()),
            )
            create_notification(conn, to_user_id, "system",
                "Новый счёт", f"{user['name']} выставил счёт на {data.get('amount', 0)} руб.",
                f"/invoices")
        self.send_json(200, {"ok": True, "id": cur.lastrowid})

    # --- Delivery Tracking ---
    def api_delivery_list(self, query):
        params = parse_qs(query)
        order_id = params.get("order_id", [""])[0]
        if not order_id:
            return self.send_json(200, {"deliveries": []})
        with connect() as conn:
            deliveries = rows_to_list(conn.execute(
                "SELECT * FROM delivery_tracking WHERE order_id = ? ORDER BY created_at DESC",
                (int(order_id),),
            ).fetchall())
        self.send_json(200, {"deliveries": deliveries})

    def api_create_delivery(self):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            order_id = int(data.get("order_id", 0))
            cur = conn.execute(
                "INSERT INTO delivery_tracking (order_id, status, location, notes, created_at) VALUES (?, ?, ?, ?, ?)",
                (order_id, data.get("status", "production"), data.get("location", ""),
                 data.get("notes", ""), now()),
            )
            # Notify order client
            order = conn.execute("SELECT client_id FROM orders WHERE id = ?", (order_id,)).fetchone()
            if order and order["client_id"] != user["id"]:
                status_names = {"production": "В производстве", "ready": "Готов к отгрузке", "shipped": "Отгружен", "delivering": "В доставке", "delivered": "Доставлен"}
                create_notification(conn, order["client_id"], "order_status",
                    "Обновление доставки", f"Статус: {status_names.get(data.get('status',''), data.get('status',''))}",
                    f"/order/{order_id}")
        self.send_json(200, {"ok": True, "id": cur.lastrowid})

    # --- Order History ---
    def api_order_history(self, query):
        params = parse_qs(query)
        order_id = params.get("order_id", [""])[0]
        if not order_id:
            return self.send_json(200, {"history": []})
        with connect() as conn:
            history = rows_to_list(conn.execute(
                """SELECT order_history.*, users.name as user_name
                   FROM order_history JOIN users ON users.id = order_history.user_id
                   WHERE order_history.order_id = ? ORDER BY order_history.created_at DESC""",
                (int(order_id),),
            ).fetchall())
        self.send_json(200, {"history": history})

    def log_order_change(self, conn, order_id, user_id, field, old_value, new_value):
        if str(old_value) != str(new_value):
            conn.execute(
                "INSERT INTO order_history (order_id, user_id, field, old_value, new_value, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (order_id, user_id, field, str(old_value), str(new_value), now()),
            )

    # --- Bulk Operations ---
    def api_admin_bulk_orders(self):
        data = self.read_json()
        action = data.get("action", "")
        ids = data.get("ids", [])
        if not ids:
            return self.send_error_json(400, "Не выбраны заказы")
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            if action == "delete":
                for oid in ids:
                    oid = int(oid)
                    conn.execute("DELETE FROM messages WHERE thread_id IN (SELECT id FROM threads WHERE order_id = ?)", (oid,))
                    conn.execute("DELETE FROM threads WHERE order_id = ?", (oid,))
                    conn.execute("DELETE FROM responses WHERE order_id = ?", (oid,))
                    conn.execute("DELETE FROM order_files WHERE order_id = ?", (oid,))
                    conn.execute("DELETE FROM orders WHERE id = ?", (oid,))
                    self.log_admin_activity(conn, admin["id"], "delete_order", "order", oid, f"Массовое удаление заказа #{oid}")
            elif action == "status":
                status = data.get("status", "")
                if status not in ("open", "progress", "closed"):
                    return self.send_error_json(400, "Некорректный статус")
                for oid in ids:
                    conn.execute("UPDATE orders SET status = ? WHERE id = ?", (status, int(oid)))
                    self.log_admin_activity(conn, admin["id"], "update_order_status", "order", int(oid), f"Массовая смена статуса #{oid} -> {status}")
        self.send_json(200, {"ok": True, "affected": len(ids)})

    def api_admin_bulk_users(self):
        data = self.read_json()
        action = data.get("action", "")
        ids = data.get("ids", [])
        if not ids:
            return self.send_error_json(400, "Не выбраны пользователи")
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            if action == "delete":
                for uid in ids:
                    uid = int(uid)
                    if uid == admin["id"]:
                        continue
                    conn.execute("DELETE FROM messages WHERE author_id = ?", (uid,))
                    conn.execute("DELETE FROM favorites WHERE user_id = ? OR company_id = ?", (uid, uid))
                    conn.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
                    conn.execute("DELETE FROM users WHERE id = ?", (uid,))
                    self.log_admin_activity(conn, admin["id"], "delete_user", "user", uid, f"Массовое удаление пользователя #{uid}")
            elif action == "role":
                role = data.get("role", "")
                if role not in ("client", "maker", "admin"):
                    return self.send_error_json(400, "Некорректная роль")
                for uid in ids:
                    conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, int(uid)))
                    self.log_admin_activity(conn, admin["id"], "update_user", "user", int(uid), f"Массовая смена роли #{uid} -> {role}")
        self.send_json(200, {"ok": True, "affected": len(ids)})

    # --- 2FA ---
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
        import hmac as _hmac
        import hashlib as _hashlib
        import time as _time
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
            # Simple TOTP verification (30s window, 6 digits)
            secret_bytes = _b64.b32decode(tfa["secret"])
            counter = int(_time.time()) // 30
            for offset in (-1, 0, 1):
                msg = _struct.pack(">Q", counter + offset)
                h = _hmac.new(secret_bytes, msg, _hashlib.sha1).digest()
                o = h[-1] & 0x0F
                num = _struct.unpack(">I", h[o:o+4])[0] & 0x7FFFFFFF
                if str(num % 1000000).zfill(6) == code:
                    if enable:
                        conn.execute("UPDATE tfa_secrets SET enabled = 1 WHERE user_id = ?", (user["id"],))
                    self.send_json(200, {"ok": True})
                    return
        self.send_error_json(400, "Неверный код")

    # --- Suppliers ---
    def api_suppliers_list(self, query):
        params = parse_qs(query)
        search = params.get("search", [""])[0]
        where = ["is_active = 1"]
        values = []
        if search:
            where.append("(LOWER(name) LIKE ? OR LOWER(materials) LIKE ? OR LOWER(city) LIKE ?)")
            values.extend([f"%{search.lower()}%"] * 3)
        where_clause = " WHERE " + " AND ".join(where)
        with connect() as conn:
            suppliers = rows_to_list(conn.execute(f"SELECT * FROM suppliers{where_clause} ORDER BY name", values).fetchall())
        self.send_json(200, {"suppliers": suppliers})

    def api_supplier_detail(self, supplier_id):
        with connect() as conn:
            s = conn.execute("SELECT * FROM suppliers WHERE id = ?", (supplier_id,)).fetchone()
            if not s:
                return self.send_error_json(404, "Поставщик не найден")
        self.send_json(200, {"supplier": dict(s)})

    def api_create_supplier(self):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user or user["role"] != "admin":
                return self.send_error_json(403, "Только админ")
            cur = conn.execute(
                "INSERT INTO suppliers (name, contact_name, email, phone, website, city, materials, description, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (data.get("name", ""), data.get("contact_name", ""), data.get("email", ""),
                 data.get("phone", ""), data.get("website", ""), data.get("city", ""),
                 data.get("materials", ""), data.get("description", ""), now()),
            )
        self.send_json(200, {"ok": True, "id": cur.lastrowid})

    def api_update_supplier(self, supplier_id):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user or user["role"] != "admin":
                return self.send_error_json(403, "Только админ")
            conn.execute(
                "UPDATE suppliers SET name=?, contact_name=?, email=?, phone=?, website=?, city=?, materials=?, description=? WHERE id=?",
                (data.get("name", ""), data.get("contact_name", ""), data.get("email", ""),
                 data.get("phone", ""), data.get("website", ""), data.get("city", ""),
                 data.get("materials", ""), data.get("description", ""), supplier_id),
            )
        self.send_json(200, {"ok": True})

    def api_delete_supplier(self, supplier_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user or user["role"] != "admin":
                return self.send_error_json(403, "Только админ")
            conn.execute("DELETE FROM suppliers WHERE id = ?", (supplier_id,))
        self.send_json(200, {"ok": True})

    # --- Certificates ---
    def api_certificates_list(self, query):
        params = parse_qs(query)
        user_id = params.get("user_id", [""])[0]
        with connect() as conn:
            if user_id:
                certs = rows_to_list(conn.execute(
                    "SELECT * FROM company_certificates WHERE user_id = ? ORDER BY created_at DESC", (int(user_id),)
                ).fetchall())
            else:
                user = self.require_user(conn)
                if not user:
                    return
                certs = rows_to_list(conn.execute(
                    "SELECT * FROM company_certificates WHERE user_id = ? ORDER BY created_at DESC", (user["id"],)
                ).fetchall())
        self.send_json(200, {"certificates": certs})

    def api_certificate_detail(self, cert_id):
        with connect() as conn:
            cert = conn.execute("SELECT * FROM company_certificates WHERE id = ?", (cert_id,)).fetchone()
            if not cert:
                return self.send_error_json(404, "Сертификат не найден")
        self.send_json(200, {"certificate": dict(cert)})

    def api_create_certificate(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            try:
                fields, files = self.read_multipart()
                stored_name = ""
                original_name = ""
                if files:
                    file = files[0]
                    original_name = safe_filename(file["filename"])
                    stored_name = f"cert_{user['id']}_{secrets.token_hex(8)}_{original_name}"
                    (UPLOAD_DIR / stored_name).write_bytes(file["content"])
                cur = conn.execute(
                    "INSERT INTO company_certificates (user_id, name, cert_type, number, issued_by, issued_at, expires_at, stored_name, original_name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (user["id"], fields.get("name", ""), fields.get("cert_type", "quality"),
                     fields.get("number", ""), fields.get("issued_by", ""), fields.get("issued_at", ""),
                     fields.get("expires_at", ""), stored_name, original_name, now()),
                )
                self.send_json(200, {"ok": True, "id": cur.lastrowid})
            except Exception as exc:
                self.send_error_json(400, str(exc))

    def api_update_certificate(self, cert_id):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            cert = conn.execute("SELECT * FROM company_certificates WHERE id = ? AND user_id = ?", (cert_id, user["id"])).fetchone()
            if not cert:
                return self.send_error_json(404, "Сертификат не найден")
            conn.execute(
                "UPDATE company_certificates SET name=?, cert_type=?, number=?, issued_by=?, issued_at=?, expires_at=? WHERE id=?",
                (data.get("name", cert["name"]), data.get("cert_type", cert["cert_type"]),
                 data.get("number", cert["number"]), data.get("issued_by", cert["issued_by"]),
                 data.get("issued_at", cert["issued_at"]), data.get("expires_at", cert["expires_at"]), cert_id),
            )
        self.send_json(200, {"ok": True})

    def api_delete_certificate(self, cert_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            cert = conn.execute("SELECT * FROM company_certificates WHERE id = ? AND user_id = ?", (cert_id, user["id"])).fetchone()
            if not cert:
                return self.send_error_json(404, "Сертификат не найден")
            if cert["stored_name"]:
                fp = UPLOAD_DIR / cert["stored_name"]
                if fp.exists():
                    fp.unlink()
            conn.execute("DELETE FROM company_certificates WHERE id = ?", (cert_id,))
        self.send_json(200, {"ok": True})

    # --- Time Tracking ---
    def api_time_entries_list(self, query):
        params = parse_qs(query)
        order_id = params.get("order_id", [""])[0]
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            if order_id:
                entries = rows_to_list(conn.execute(
                    """SELECT time_tracking.*, users.name as user_name FROM time_tracking
                       JOIN users ON users.id = time_tracking.user_id
                       WHERE time_tracking.order_id = ? ORDER BY time_tracking.date DESC""",
                    (int(order_id),),
                ).fetchall())
            else:
                entries = rows_to_list(conn.execute(
                    """SELECT time_tracking.*, users.name as user_name, orders.title as order_title FROM time_tracking
                       JOIN users ON users.id = time_tracking.user_id
                       JOIN orders ON orders.id = time_tracking.order_id
                       WHERE time_tracking.user_id = ? ORDER BY time_tracking.date DESC LIMIT 50""",
                    (user["id"],),
                ).fetchall())
            total_hours = conn.execute("SELECT COALESCE(SUM(hours), 0) FROM time_tracking WHERE user_id = ?", (user["id"],)).fetchone()[0]
        self.send_json(200, {"entries": entries, "total_hours": round(total_hours, 1)})

    def api_create_time_entry(self):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            cur = conn.execute(
                "INSERT INTO time_tracking (order_id, user_id, task, hours, date, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (int(data.get("order_id", 0)), user["id"], data.get("task", ""),
                 float(data.get("hours", 0)), data.get("date", now()[:10]),
                 data.get("notes", ""), now()),
            )
        self.send_json(200, {"ok": True, "id": cur.lastrowid})

    def api_delete_time_entry(self, entry_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            conn.execute("DELETE FROM time_tracking WHERE id = ? AND user_id = ?", (entry_id, user["id"]))
        self.send_json(200, {"ok": True})

    # --- Client Ratings (maker rates client) ---
    def api_client_ratings_list(self, query):
        params = parse_qs(query)
        client_id = params.get("client_id", [""])[0]
        if not client_id:
            return self.send_json(200, {"ratings": []})
        with connect() as conn:
            ratings = rows_to_list(conn.execute(
                """SELECT client_ratings.*, users.name as maker_name, orders.title as order_title
                   FROM client_ratings JOIN users ON users.id = client_ratings.maker_id
                   JOIN orders ON orders.id = client_ratings.order_id
                   WHERE client_ratings.client_id = ? ORDER BY client_ratings.created_at DESC""",
                (int(client_id),),
            ).fetchall())
            avg = conn.execute("SELECT AVG(rating) FROM client_ratings WHERE client_id = ?", (int(client_id),)).fetchone()[0]
        self.send_json(200, {"ratings": ratings, "avg_rating": round(avg, 1) if avg else 0})

    def api_create_client_rating(self):
        try:
            data = self.read_json()
            with connect() as conn:
                user = self.require_user(conn)
                if not user:
                    return
                if user["role"] != "maker":
                    return self.send_error_json(403, "Только производитель")
                order_id = int(data.get("order_id", 0))
                client_id = int(data.get("client_id", 0))
                rating = int(data.get("rating", 5))
                text = data.get("text", "").strip()
                if rating < 1 or rating > 5:
                    return self.send_error_json(400, "Рейтинг от 1 до 5")
                conn.execute(
                    "INSERT INTO client_ratings (order_id, maker_id, client_id, rating, text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (order_id, user["id"], client_id, rating, text, now()),
                )
                create_notification(conn, client_id, "review",
                    "Новая оценка", f"{user['name']} оценил вас ({rating}/5)",
                    f"/company/{client_id}")
            self.send_json(200, {"ok": True})
        except sqlite3.IntegrityError:
            self.send_error_json(409, "Вы уже оценили этого заказчика")
        except Exception as exc:
            self.send_error_json(400, str(exc))

    # --- Excel Export ---
    def api_export_excel(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            orders = rows_to_list(conn.execute(
                """SELECT orders.*, clients.name as client_name, makers.name as selected_maker_name
                   FROM orders JOIN users clients ON clients.id = orders.client_id
                   LEFT JOIN users makers ON makers.id = orders.selected_maker_id
                   WHERE orders.client_id = ? OR orders.selected_maker_id = ?
                   ORDER BY orders.created_at DESC""",
                (user["id"], user["id"]),
            ).fetchall())
        # Generate simple CSV (Excel-compatible with UTF-8 BOM)
        import io
        output = io.StringIO()
        output.write("\ufeff")  # BOM for Excel
        output.write("ID,Название,Тип,Количество,Город,Бюджет,Срок,Статус,Заказчик,Исполнитель,Дата\n")
        for o in orders:
            output.write(f'{o["id"]},"{o["title"]}","{o["type"]}",{o["quantity"]},"{o["city"]}",{o["budget"]},"{o["deadline"]}","{o["status"]}","{o["client_name"]}","{o.get("selected_maker_name") or ""}","{o["created_at"]}"\n')
        data = output.getvalue().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="meblio-orders-{now()[:10]}.csv"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # --- JWT Auth Token ---
    def api_auth_token(self):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            import hashlib as _hl
            import time as _t
            token = secrets.token_urlsafe(48)
            expires = (datetime.datetime.utcnow() + datetime.timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute("INSERT INTO api_tokens (user_id, token, name, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
                         (user["id"], token, data.get("name", "mobile"), expires, now()))
        self.send_json(200, {"token": token, "expires_at": expires})

    # --- CSRF Token ---
    def api_csrf_token(self):
        with connect() as conn:
            cookie_header = self.headers.get("Cookie", "")
            jar = cookies.SimpleCookie(cookie_header)
            token = jar.get("meblio_session")
            session_token = token.value if token else None
            csrf = generate_csrf_token(conn, session_token)
        self.send_json(200, {"csrf_token": csrf})

    # --- Notifications ---
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

    # --- Reviews ---
    def api_reviews_list(self, query):
        params = parse_qs(query)
        company_id = params.get("company_id", [""])[0]
        if not company_id:
            return self.send_json(200, {"reviews": []})
        with connect() as conn:
            reviews = rows_to_list(conn.execute(
                """
                SELECT reviews.*, users.name AS reviewer_name
                FROM reviews JOIN users ON users.id = reviews.reviewer_id
                WHERE reviews.company_id = ?
                ORDER BY reviews.created_at DESC
                """,
                (int(company_id),),
            ).fetchall())
            avg = conn.execute("SELECT AVG(rating) FROM reviews WHERE company_id = ?", (int(company_id),)).fetchone()[0]
            count = len(reviews)
        self.send_json(200, {"reviews": reviews, "avg_rating": round(avg, 1) if avg else 0, "reviews_count": count})

    def api_create_review(self):
        try:
            data = self.read_json()
            with connect() as conn:
                user = self.require_user(conn)
                if not user:
                    return
                company_id = int(data.get("company_id", 0))
                order_id = data.get("order_id")
                if order_id:
                    order_id = int(order_id)
                rating = int(data.get("rating", 5))
                text = data.get("text", "").strip()
                if company_id == user["id"]:
                    return self.send_error_json(400, "Нельзя оставить отзыв себе")
                if rating < 1 or rating > 5:
                    return self.send_error_json(400, "Рейтинг от 1 до 5")
                conn.execute(
                    "INSERT INTO reviews (reviewer_id, company_id, order_id, rating, text, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (user["id"], company_id, order_id, rating, text, now()),
                )
                create_notification(conn, company_id, "review",
                    "Новый отзыв", f"{user['name']} оставил отзыв ({rating}/5)",
                    f"/company/{company_id}")
            self.send_json(200, {"ok": True})
        except sqlite3.IntegrityError:
            self.send_error_json(409, "Вы уже оставляли отзыв на эту компанию к этому заказу")
        except Exception as exc:
            self.send_error_json(400, str(exc))

    # --- Global Search ---
    def api_global_search(self, query):
        params = parse_qs(query)
        q = params.get("q", [""])[0].strip()
        if len(q) < 2:
            return self.send_json(200, {"results": []})
        like = f"%{q.lower()}%"
        with connect() as conn:
            orders = rows_to_list(conn.execute(
                "SELECT id, title, type, city, budget, status FROM orders WHERE LOWER(title) LIKE ? OR LOWER(details) LIKE ? ORDER BY created_at DESC LIMIT 10",
                (like, like),
            ).fetchall())
            companies = rows_to_list(conn.execute(
                "SELECT id, name, city, company_type, about FROM users WHERE role = 'maker' AND (LOWER(name) LIKE ? OR LOWER(about) LIKE ?) LIMIT 10",
                (like, like),
            ).fetchall())
            services = rows_to_list(conn.execute(
                "SELECT id, title, description, price_type FROM services WHERE LOWER(title) LIKE ? OR LOWER(description) LIKE ? LIMIT 10",
                (like, like),
            ).fetchall())
        self.send_json(200, {
            "orders": [{"type": "order", **o} for o in orders],
            "companies": [{"type": "company", **c} for c in companies],
            "services": [{"type": "service", **s} for s in services],
        })

    # --- Company Documents ---
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
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            try:
                fields, files = self.read_multipart()
                if not files:
                    return self.send_error_json(400, "Файл не загружен")
                doc_type = fields.get("doc_type", "other")
                for file in files:
                    original = safe_filename(file["filename"])
                    stored = f"doc_{user['id']}_{secrets.token_hex(8)}_{original}"
                    (UPLOAD_DIR / stored).write_bytes(file["content"])
                    conn.execute(
                        "INSERT INTO company_documents (user_id, original_name, stored_name, doc_type, size, mime, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (user["id"], original, stored, doc_type, len(file["content"]), file["mime"], now()),
                    )
                self.send_json(200, {"ok": True})
            except Exception as exc:
                self.send_error_json(400, str(exc))

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


def main():
    init_db()
    port = int(os.environ.get("PORT", "8000"))
    ws_port = int(os.environ.get("WS_PORT", "8001"))

    # Start WebSocket server
    from ws_server import WebSocketServer
    ws_server = WebSocketServer(ws_port)
    ws_server.start()

    server = ThreadingHTTPServer(("127.0.0.1", port), MeblioHandler)
    print(f"Meblio portal: http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        ws_server.stop()
        print("\nServer stopped")


if __name__ == "__main__":
    main()
