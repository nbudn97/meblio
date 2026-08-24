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
    session_cutoff,
    purge_expired_sessions,
    init_db,
    create_user,
    ensure_thread,
)
from common import (
    MAX_UPLOAD_BYTES,
    PAGE_SIZE,
    ALLOWED_UPLOAD_EXTS,
    INLINE_UPLOAD_EXTS,
    check_rate_limit,
    json_dumps,
    safe_filename,
    validate_upload_file,
    csv_safe,
    parse_deadline_days,
    create_notification,
    store_upload,
)
from logger import get_logger
from api_admin import AdminMixin
from api_catalog import CatalogMixin

logger = get_logger("http")

STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/styles.css": "styles.css",
    "/script.js": "script.js",
    "/meblio.png": "meblio.png",
}
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self' ws://127.0.0.1:8001 http://127.0.0.1:8001"
    ),
}
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
ADMIN_REPORT_RE = re.compile(r"^/api/admin/reports/(\d+)/resolve$")
GALLERY_RE = re.compile(r"^/api/gallery/(\d+)$")
ARTICLE_SLUG_RE = re.compile(r"^/api/articles/([\w-]+)$")
COMPANY_INVITE_RE = re.compile(r"^/api/companies/(\d+)/invite$")
ADMIN_ARTICLE_RE = re.compile(r"^/api/admin/articles/(\d+)$")

CSRF_EXEMPT_PATHS = {"/api/login", "/api/register", "/api/csrf-token", "/api/tfa/login"}


def verify_totp(secret, code):
    """Check a 6-digit TOTP code against a base32 secret (30s window, ±1 step)."""
    import base64 as _b64
    import hashlib as _hashlib
    import hmac as _hmac
    import struct as _struct
    import time as _time
    try:
        secret_bytes = _b64.b32decode(secret)
        counter = int(_time.time()) // 30
        for offset in (-1, 0, 1):
            msg = _struct.pack(">Q", counter + offset)
            h = _hmac.new(secret_bytes, msg, _hashlib.sha1).digest()
            o = h[-1] & 0x0F
            num = _struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF
            if str(num % 1000000).zfill(6) == code:
                return True
    except Exception:
        return False
    return False


TRUST_DEVICE_COOKIE = "meblio_device"
TRUST_DEVICE_DAYS = 30


def get_tfa_trust_secret(conn):
    row = conn.execute("SELECT value FROM app_config WHERE key = 'tfa_trust_secret'").fetchone()
    if row:
        return row["value"]
    secret = secrets.token_hex(32)
    conn.execute("INSERT OR IGNORE INTO app_config (key, value) VALUES ('tfa_trust_secret', ?)", (secret,))
    return secret


def make_trust_cookie(secret, user_id):
    import hashlib as _hashlib
    import hmac as _hmac
    import time as _time
    expires = int(_time.time()) + TRUST_DEVICE_DAYS * 86400
    sig = _hmac.new(secret.encode(), f"{user_id}:{expires}".encode(), _hashlib.sha256).hexdigest()
    return f"{user_id}:{expires}:{sig}"


def verify_trust_cookie(secret, cookie_value):
    import hashlib as _hashlib
    import hmac as _hmac
    import time as _time
    try:
        user_id, expires, sig = cookie_value.split(":")
        expected = _hmac.new(secret.encode(), f"{user_id}:{expires}".encode(), _hashlib.sha256).hexdigest()
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
    return verify_trust_cookie(get_tfa_trust_secret(conn), raw.value)


INDEX_CACHE = {"mtime": 0, "html": ""}


def load_index_template():
    path = BASE_DIR / "index.html"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ""
    if INDEX_CACHE["mtime"] != mtime:
        INDEX_CACHE["mtime"] = mtime
        INDEX_CACHE["html"] = path.read_text(encoding="utf-8")
    return INDEX_CACHE["html"]


def _esc(text):
    import html as _html
    return _html.escape(str(text or ""), quote=True)


def seo_for_path(path):
    host = "meblio.local"
    base_title = "Meblio — площадка для заказчиков и производителей мебели"
    base_desc = ("Meblio — рабочая площадка для общения заказчиков мебели и мебельных производств: "
                 "заказы, отклики, личные кабинеты и чат.")
    info = {"title": base_title, "description": base_desc,
            "canonical": f"https://{host}{path}", "json_ld": None}
    m = re.match(r"^/companies/(\d+)/?$", path)
    if m:
        with connect() as conn:
            row = conn.execute(
                "SELECT name, city, about, logo, region_id FROM users WHERE id = ?", (int(m.group(1)),)
            ).fetchone()
        if row:
            info["title"] = f"{row['name']} — производитель мебели, {row['city']} | Meblio"
            info["description"] = (row["about"] or f"{row['name']}, {row['city']}")[:160]
            info["json_ld"] = {
                "@context": "https://schema.org", "@type": "Organization",
                "name": row["name"], "description": row["about"] or "",
                "address": {"@type": "PostalAddress", "addressLocality": row["city"], "addressCountry": "RU"},
            }
        return info
    m = re.match(r"^/services/(\d+)/?$", path)
    if m:
        with connect() as conn:
            row = conn.execute(
                "SELECT s.title, s.description, s.price_type, u.name AS company FROM services s "
                "JOIN users u ON u.id = s.user_id WHERE s.id = ?",
                (int(m.group(1)),),
            ).fetchone()
        if row:
            info["title"] = f"{row['title']} — {row['company']} | Meblio"
            info["description"] = (row["description"] or row["title"])[:160]
            info["json_ld"] = {
                "@context": "https://schema.org", "@type": "Service",
                "name": row["title"], "description": row["description"] or "",
                "provider": {"@type": "Organization", "name": row["company"]},
            }
        return info
    m = re.match(r"^/articles/([\w-]+)/?$", path)
    if m:
        with connect() as conn:
            row = conn.execute(
                "SELECT title, excerpt FROM articles WHERE slug = ? AND is_published = 1", (m.group(1),)
            ).fetchone()
        if row:
            info["title"] = f"{row['title']} | Meblio"
            info["description"] = (row["excerpt"] or row["title"])[:160]
        return info
    view_titles = {
        "/market": ("Заказы для производителей мебели", "Открытые заказы на изготовление мебели от заказчиков по всей России."),
        "/companies": ("Каталог мебельных производств и поставщиков", "Производители мебели, проектировщики и поставщики фурнитуры с рейтингами и портфолио."),
        "/services": ("Услуги мебельных производств", "Каталог услуг: кухни, шкафы, корпусная мебель на заказ."),
        "/articles": ("Статьи о мебельном производстве", "Материалы о материалах, фурнитуре и работе с подрядчиками."),
    }
    if path in view_titles:
        info["title"] = f"{view_titles[path][0]} | Meblio"
        info["description"] = view_titles[path][1]
    return info


def render_index(self, path):
    html = load_index_template()
    if not html:
        return self.send_error_json(404, "Файл не найден")
    seo = seo_for_path(path)
    html = re.sub(r"<title>.*?</title>", f"<title>{_esc(seo['title'])}</title>", html, count=1, flags=re.S)
    html = re.sub(
        r'<meta\s+name="description"[^>]*>',
        f'<meta name="description" content="{_esc(seo["description"])}">',
        html, count=1,
    )
    block = (
        f'<link rel="canonical" href="{_esc(seo["canonical"])}">\n'
        f'<meta property="og:title" content="{_esc(seo["title"])}">\n'
        f'<meta property="og:description" content="{_esc(seo["description"])}">\n'
        f'<meta property="og:type" content="website">\n'
    )
    if seo["json_ld"]:
        block += f'<script type="application/ld+json">{json.dumps(seo["json_ld"], ensure_ascii=False)}</script>\n'
    html = html.replace("</title>", "</title>\n    " + block.strip(), 1)
    metrica_id = os.environ.get("MEBLIO_METRICA_ID", "")
    if metrica_id:
        block_m = (
            f"<script>(function(m,e,t,r,i,k,a){{m[i]=m[i]||function(){{(m[i].a=m[i].a||[]).push(arguments)}};"
            f"m[i].l=1*new Date();k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,"
            f"a.parentNode.insertBefore(k,a)}})(window,document,'script','https://mc.yandex.ru/metrika/tag.js','ym');"
            f"ym({metrica_id},'init',{{clickmap:true,trackLinks:true,accurateTrackBounce:true}});</script>"
        )
        html = html.replace("</body>", block_m + "</body>", 1)
    data = html.encode("utf-8")
    self.send_response(200)
    self.send_header("Content-Type", "text/html; charset=utf-8")
    self.send_header("Content-Length", str(len(data)))
    for header, value in SECURITY_HEADERS.items():
        self.send_header(header, value)
    self.end_headers()
    self.wfile.write(data)


def serve_sitemap(self):
    host = self.headers.get("Host", "127.0.0.1:8000")
    today = now()[:10]
    urls = [f"https://{host}/"]
    with connect() as conn:
        for row in conn.execute("SELECT id FROM users WHERE role = 'maker'").fetchall():
            urls.append(f"https://{host}/companies/{row['id']}")
        for row in conn.execute("SELECT id FROM services WHERE is_hidden = 0").fetchall():
            urls.append(f"https://{host}/services/{row['id']}")
        for row in conn.execute("SELECT slug, updated_at FROM articles WHERE is_published = 1").fetchall():
            urls.append(f"https://{host}/articles/{row['slug']}")
        region_slugs = [r["slug"] for r in conn.execute("SELECT slug FROM regions ORDER BY id").fetchall()]
    type_slugs = ["client", "designer", "manufacturer", "serial", "supplier"]
    for t_slug in type_slugs:
        urls.append(f"https://{host}/companies/?type={t_slug}")
        for r_slug in region_slugs:
            urls.append(f"https://{host}/companies/?type={t_slug}&region={r_slug}")
    for r_slug in region_slugs:
        urls.append(f"https://{host}/companies/?region={r_slug}")
    xml = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url in urls:
        xml.append(f"<url><loc>{url}</loc><lastmod>{today}</lastmod></url>")
    xml.append("</urlset>")
    data = "\n".join(xml).encode("utf-8")
    self.send_response(200)
    self.send_header("Content-Type", "application/xml; charset=utf-8")
    self.send_header("Content-Length", str(len(data)))
    self.end_headers()
    self.wfile.write(data)


def serve_robots(self):
    host = self.headers.get("Host", "127.0.0.1:8000")
    body = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        "Disallow: /dashboard\n"
        "Disallow: /chat\n"
        "Disallow: /admin\n"
        "Disallow: /notifications\n"
        "Disallow: /uploads/\n"
        f"Sitemap: https://{host}/sitemap.xml\n"
    )
    data = body.encode("utf-8")
    self.send_response(200)
    self.send_header("Content-Type", "text/plain; charset=utf-8")
    self.send_header("Content-Length", str(len(data)))
    self.end_headers()
    self.wfile.write(data)


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


class MeblioHandler(AdminMixin, CatalogMixin, BaseHTTPRequestHandler):
    server_version = "MeblioHTTP/1.0"

    def log_message(self, fmt, *args):
        logger.info("%s [%s]", self.address_string(), fmt % args)

    def send_json(self, status, data, extra_headers=None):
        payload = json_dumps(data)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        if extra_headers:
            for key, value in extra_headers.items():
                for single in (value if isinstance(value, list) else [value]):
                    self.send_header(key, single)
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

    def _safe_dispatch(self, handler, *args, **kwargs):
        import time as _time
        started = _time.time()
        try:
            return handler(*args, **kwargs)
        except Exception:
            logger.exception("Unhandled error in %s %s", self.command, self.path)
            try:
                self.send_error_json(500, "Внутренняя ошибка сервера")
            except Exception:
                pass
            return None
        finally:
            elapsed = _time.time() - started
            if elapsed > 1.0:
                logger.warning("SLOW REQUEST %s %s took %.2fs", self.command, self.path, elapsed)

    def do_GET(self):
        return self._safe_dispatch(self._handle_GET)

    def do_POST(self):
        return self._safe_dispatch(self._handle_POST)

    def do_PUT(self):
        return self._safe_dispatch(self._handle_PUT)

    def do_DELETE(self):
        return self._safe_dispatch(self._handle_DELETE)

    def _handle_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/healthz":
            return self.send_json(200, {"ok": True, "app": "meblio", "time": now()})
        if path == "/sitemap.xml":
            return serve_sitemap(self)
        if path == "/robots.txt":
            return serve_robots(self)
        if path == "/api/verify-email":
            return self.api_verify_email(parsed.query)
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
        if path == "/api/maker/stats":
            return self.api_maker_stats()
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
        if path == "/api/articles":
            return self.api_articles_list()
        m = ARTICLE_SLUG_RE.match(path)
        if m:
            return self.api_article_detail(m.group(1))
        if path == "/api/admin/reports":
            return self.api_admin_reports(parsed.query)
        if path.startswith("/uploads/"):
            return self.serve_upload(path)
        if path in STATIC_FILES:
            if path == "/":
                return render_index(self, path)
            return self.serve_static(STATIC_FILES[path])
        if path.startswith("/api/"):
            return self.send_error_json(404, "Страница не найдена")
        return render_index(self, path)

    def _handle_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/") and path not in CSRF_EXEMPT_PATHS and not self.check_csrf():
            return
        if path == "/api/register":
            return self.api_register()
        if path == "/api/login":
            return self.api_login()
        if path == "/api/tfa/login":
            return self.api_tfa_login()
        if path == "/api/forgot-password":
            return self.api_forgot_password()
        if path == "/api/reset-password":
            return self.api_reset_password()
        if path == "/api/change-password":
            return self.api_change_password()
        if path == "/api/change-email":
            return self.api_change_email()
        if path == "/api/delete-account":
            return self.api_delete_account()
        if path == "/api/resend-verification":
            return self.api_resend_verification()
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
            if path.endswith("/cancel"):
                return self.api_cancel_order(order_id)
            if path.endswith("/close"):
                return self.api_close_order(order_id)
            if path.endswith("/publish"):
                return self.api_publish_order(order_id)
        if path == "/api/reports":
            return self.api_create_report()
        if path == "/api/admin/hide":
            return self.api_admin_hide_content()
        if path == "/api/admin/articles":
            return self.api_admin_articles_list()
        m = ADMIN_ARTICLE_RE.match(path)
        if m:
            return self.api_admin_article_delete(int(m.group(1)))
        if path == "/api/gallery":
            return self.api_upload_gallery()
        if path == "/api/admin/article-save":
            return self.api_admin_article_save()
        m = ADMIN_REPORT_RE.match(path)
        if m:
            return self.api_admin_report_resolve(int(m.group(1)))
        m = COMPANY_INVITE_RE.match(path)
        if m:
            return self.api_invite_to_quote(int(m.group(1)))
        m = THREAD_ID_RE.match(path)
        if m and path.endswith("/messages"):
            return self.api_send_message(int(m.group(1)))
        m = THREAD_ID_RE.match(path)
        if m and path.endswith("/files"):
            return self.api_upload_thread_file(int(m.group(1)))
        if path == "/api/profile":
            return self.api_profile()
        if path == "/api/admin/orders/status":
            return self.api_admin_update_order_status()
        if path == "/api/admin/users":
            return self.api_admin_create_user()
        return self.send_error_json(404, "Метод не найден")

    def _handle_PUT(self):
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

    def _handle_DELETE(self):
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
        m = GALLERY_RE.match(path)
        if m:
            return self.api_delete_gallery_item(int(m.group(1)))
        m = ADMIN_ARTICLE_RE.match(path)
        if m:
            return self.api_admin_article_delete(int(m.group(1)))
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
                validate_upload_file(original, value)
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
            WHERE sessions.token = ? AND sessions.created_at >= ?
            """,
            (token.value, session_cutoff()),
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

    def public_user(self, user, include_contacts=True):
        if not user:
            return None
        data = {
            "id": user["id"],
            "role": user["role"],
            "company_type": user["company_type"],
            "name": user["name"],
            "city": user["city"],
            "region_id": user["region_id"],
            "region_name": user.get("region_name", ""),
            "about": user["about"],
            "skills": [item.strip() for item in user["skills"].split(",") if item.strip()],
            "capacity": user["capacity"],
            "logo": user["logo"],
            "is_verified": bool(user.get("is_verified")),
            "created_at": user["created_at"],
        }
        if include_contacts:
            data["email"] = user["email"]
            data["phone"] = user["phone"]
        else:
            data["email"] = ""
            data["phone"] = ""
        return data

    def contacts_visible(self, conn, viewer_id, company_id):
        """Contacts of a company are visible to participants of deals/threads and admins."""
        if not viewer_id:
            return False
        if viewer_id == company_id:
            return True
        viewer = conn.execute("SELECT role FROM users WHERE id = ?", (viewer_id,)).fetchone()
        if viewer and viewer["role"] == "admin":
            return True
        row = conn.execute(
            "SELECT 1 FROM threads WHERE (client_id = ? AND maker_id = ?) OR (client_id = ? AND maker_id = ?) LIMIT 1",
            (viewer_id, company_id, company_id, viewer_id),
        ).fetchone()
        if row:
            return True
        row = conn.execute(
            "SELECT 1 FROM orders WHERE client_id = ? AND selected_maker_id = ? LIMIT 1",
            (viewer_id, company_id),
        ).fetchone()
        return row is not None

    def order_payload(self, conn, order):
        return self.order_payload_batch(conn, [order])[0]

    def order_payload_batch(self, conn, order_rows):
        orders = [dict(row) for row in order_rows]
        if not orders:
            return []
        ids = [o["id"] for o in orders]
        placeholders = ",".join("?" * len(ids))
        files_by_order = {}
        for f in conn.execute(
            f"SELECT * FROM order_files WHERE order_id IN ({placeholders}) ORDER BY id", ids
        ).fetchall():
            files_by_order.setdefault(f["order_id"], []).append(
                {
                    "id": f["id"],
                    "name": f["original_name"],
                    "size": f["size"],
                    "mime": f["mime"],
                    "url": f"/uploads/{f['stored_name']}",
                }
            )
        responses_by_order = {}
        for r in conn.execute(
            f"""
            SELECT responses.*, users.name AS maker_name, users.city AS maker_city
            FROM responses JOIN users ON users.id = responses.maker_id
            WHERE order_id IN ({placeholders}) ORDER BY responses.created_at DESC
            """,
            ids,
        ).fetchall():
            responses_by_order.setdefault(r["order_id"], []).append(dict(r))
        for o in orders:
            o["files"] = files_by_order.get(o["id"], [])
            o["responses"] = responses_by_order.get(o["id"], [])
        return orders

    def serve_static(self, filename):
        path = BASE_DIR / filename
        if not path.exists():
            return self.send_error_json(404, "Файл не найден")
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        stat = path.stat()
        etag = f'W/"{int(stat.st_mtime)}-{stat.st_size}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "public, max-age=3600")
            for header, value in SECURITY_HEADERS.items():
                self.send_header(header, value)
            self.end_headers()
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", self.date_time_string(int(stat.st_mtime)))
        self.send_header("Cache-Control", "public, max-age=3600")
        for header, value in SECURITY_HEADERS.items():
            self.send_header(header, value)
        self.end_headers()
        self.wfile.write(data)

    def serve_upload(self, path):
        name = unquote(path.replace("/uploads/", "", 1))
        file_path = (UPLOAD_DIR / name).resolve()
        if not str(file_path).startswith(str(UPLOAD_DIR.resolve())) or not file_path.exists():
            return self.send_error_json(404, "Файл не найден")
        mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        ext = file_path.suffix.lower()
        disposition = "inline" if ext in INLINE_UPLOAD_EXTS else "attachment"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Disposition", f'{disposition}; filename="{name}"')
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
            if data.get("website"):
                return self.send_error_json(400, "Регистрация не прошла проверку")
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
                purge_expired_sessions(conn)
                conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)", (token, user_id, now()))
                user = conn.execute("SELECT users.*, regions.name AS region_name FROM users LEFT JOIN regions ON regions.id = users.region_id WHERE users.id = ?", (user_id,)).fetchone()
                email = user["email"]
                verify_token = create_pending_token(conn, "email_verifications", user_id, 60 * 24)
                base_url = f"http://{self.headers.get('Host', '127.0.0.1:8000')}"
                from mailer import send_email
                send_email(
                    email,
                    "Подтвердите email на Meblio",
                    "Для подтверждения адреса перейдите по ссылке:",
                    link_url=f"{base_url}/api/verify-email?token={verify_token}",
                )
            payload = {"user": self.public_user(row_to_dict(user))}
            if os.environ.get("MEBLIO_DEV", "1") == "1":
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
            with connect() as conn:
                user = conn.execute("SELECT users.*, regions.name AS region_name FROM users LEFT JOIN regions ON regions.id = users.region_id WHERE email = ?", (data.get("email", "").strip().lower(),)).fetchone()
                if not user or not verify_password(data.get("password", ""), user["password_salt"], user["password_hash"]):
                    return self.send_error_json(401, "Неверный email или пароль")
                tfa = conn.execute("SELECT * FROM tfa_secrets WHERE user_id = ? AND enabled = 1", (user["id"],)).fetchone()
                trusted = bool(tfa) and read_trusted_user_id(self, conn) == user["id"]
                if tfa and not trusted:
                    login_token = create_pending_token(conn, "pending_tfa", user["id"], 10)
                    return self.send_json(200, {"tfa_required": True, "login_token": login_token})
                token = secrets.token_urlsafe(32)
                purge_expired_sessions(conn)
                conn.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)", (token, user["id"], now()))
                trust_value = make_trust_cookie(get_tfa_trust_secret(conn), user["id"]) if tfa else None
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

    def api_orders(self, query):
        params = parse_qs(query)
        type_filter = params.get("type", [""])[0]
        city_filter = params.get("city", [""])[0]
        status_filter = params.get("status", [""])[0]
        page = max(1, int(params.get("page", ["1"])[0]))
        offset = (page - 1) * PAGE_SIZE
        with connect() as conn:
            viewer = self.current_user(conn)
        where = []
        values = []
        if type_filter:
            where.append("orders.type = ?")
            values.append(type_filter)
        if city_filter:
            where.append("LOWER(orders.city) LIKE ?")
            values.append(f"%{city_filter.lower()}%")
        if status_filter == "draft":
            if not viewer:
                return self.send_json(200, {"orders": [], "total": 0, "page": page, "page_size": PAGE_SIZE})
            where.append("orders.client_id = ?")
            values.append(viewer["id"])
        elif status_filter:
            where.append("orders.status = ?")
            values.append(status_filter)
        else:
            where.append("orders.status != 'draft'")
        where.append("orders.is_hidden = 0")
        budget_min = params.get("budget_min", [""])[0]
        budget_max = params.get("budget_max", [""])[0]
        if budget_min.isdigit():
            where.append("orders.budget >= ?")
            values.append(int(budget_min))
        if budget_max.isdigit():
            where.append("orders.budget <= ?")
            values.append(int(budget_max))
        where_clause = (" WHERE " + " AND ".join(where)) if where else ""
        with connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM orders{where_clause}", values).fetchone()[0]
            sql = f"""
                SELECT orders.*, clients.name AS client_name, makers.name AS selected_maker_name
                FROM orders
                JOIN users clients ON clients.id = orders.client_id
                LEFT JOIN users makers ON makers.id = orders.selected_maker_id
                {where_clause}
                ORDER BY orders.created_at DESC, orders.id DESC
                LIMIT ? OFFSET ?
            """
            orders = self.order_payload_batch(conn, conn.execute(sql, values + [PAGE_SIZE, offset]).fetchall())
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
            if region_filter.isdigit():
                where.append("region_id = ?")
                values.append(int(region_filter))
            else:
                where.append("region_id IN (SELECT id FROM regions WHERE slug = ?)")
                values.append(region_filter)
        if search:
            where.append("(LOWER(users.name) LIKE ? OR LOWER(about) LIKE ?)")
            values.extend([f"%{search.lower()}%", f"%{search.lower()}%"])
        where_clause = " WHERE " + " AND ".join(where)
        with connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM users{where_clause}", values).fetchone()[0]
            sql = f"""
                SELECT users.*, regions.name AS region_name
                FROM users LEFT JOIN regions ON regions.id = users.region_id
                {where_clause}
                ORDER BY users.name, users.id
                LIMIT ? OFFSET ?
            """
            companies = []
            company_rows = conn.execute(sql, values + [PAGE_SIZE, offset]).fetchall()
            company_ids = [row["id"] for row in company_rows]
            review_stats = {}
            if company_ids:
                stat_placeholders = ",".join("?" * len(company_ids))
                for stat in conn.execute(
                    f"""
                    SELECT company_id, AVG(rating) AS avg_rating, COUNT(*) AS reviews_count
                    FROM reviews WHERE company_id IN ({stat_placeholders}) GROUP BY company_id
                    """,
                    company_ids,
                ).fetchall():
                    review_stats[stat["company_id"]] = (stat["avg_rating"], stat["reviews_count"])
            for row in company_rows:
                company = self.public_user(row_to_dict(row), include_contacts=False)
                avg_rating, reviews_count = review_stats.get(row["id"], (None, 0))
                company["avg_rating"] = round(avg_rating, 1) if avg_rating else 0
                company["reviews_count"] = reviews_count
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
            viewer = self.current_user(conn)
            include_contacts = self.contacts_visible(conn, viewer["id"] if viewer else None, company_id)
        data = self.public_user(row_to_dict(user), include_contacts=include_contacts)
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
            if not check_rate_limit(f"create_order:{user['id']}", 20, 600):
                return self.send_error_json(429, "Слишком много заказов. Подождите немного.")
            if user["role"] != "client":
                return self.send_error_json(403, "Размещать заказы может только заказчик")
            try:
                fields, files = self.read_multipart()
                is_draft = fields.get("is_draft") == "1"
                status = "draft" if is_draft else "open"
                cur = conn.execute(
                    f"""
                    INSERT INTO orders (client_id, title, type, quantity, city, budget, deadline, details, status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{status}', ?)
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
                    stored, original = store_upload(f"order_{order_id}", file["filename"], file["content"])
                    conn.execute(
                        "INSERT INTO order_files (order_id, original_name, stored_name, size, mime, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (order_id, original, stored, len(file["content"]), file["mime"], now()),
                    )
                order = conn.execute("SELECT orders.*, users.name AS client_name, NULL AS selected_maker_name FROM orders JOIN users ON users.id = orders.client_id WHERE orders.id = ?", (order_id,)).fetchone()
                # Notify all makers about new order
                makers = [] if is_draft else conn.execute("SELECT id FROM users WHERE role = 'maker'").fetchall()
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
                if not check_rate_limit(f"response:{user['id']}", 30, 600):
                    return self.send_error_json(429, "Слишком много откликов. Подождите немного.")
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

    def api_cancel_order(self, order_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not order:
                return self.send_error_json(404, "Заказ не найден")
            if user["role"] != "admin" and order["client_id"] != user["id"]:
                return self.send_error_json(403, "Отменить заказ может только заказчик")
            if order["status"] not in ("open", "progress"):
                return self.send_error_json(400, "Заказ уже нельзя отменить")
            conn.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (order_id,))
            self.log_order_change(conn, order_id, user["id"], "status", order["status"], "cancelled")
            if order["selected_maker_id"]:
                create_notification(conn, order["selected_maker_id"], "order_status",
                    "Заказ отменён", f"Заказ «{order['title']}» был отменён заказчиком",
                    f"/order/{order_id}")
        self.send_json(200, {"ok": True})

    def api_close_order(self, order_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not order:
                return self.send_error_json(404, "Заказ не найден")
            is_client = order["client_id"] == user["id"]
            is_maker = order["selected_maker_id"] == user["id"]
            if not (is_client or is_maker or user["role"] == "admin"):
                return self.send_error_json(403, "Завершить заказ могут только участники сделки")
            if order["status"] != "progress":
                return self.send_error_json(400, "Завершить можно только заказ в работе")
            conn.execute("UPDATE orders SET status = 'closed' WHERE id = ?", (order_id,))
            self.log_order_change(conn, order_id, user["id"], "status", "progress", "closed")
            other_party = order["client_id"] if is_maker else order["selected_maker_id"]
            if other_party:
                create_notification(conn, other_party, "order_status",
                    "Заказ завершён", f"Заказ «{order['title']}» переведён в статус «Завершён»",
                    f"/order/{order_id}")
        self.send_json(200, {"ok": True})

    def api_publish_order(self, order_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not order or order["client_id"] != user["id"]:
                return self.send_error_json(403, "Опубликовать может только заказчик")
            if order["status"] != "draft":
                return self.send_error_json(400, "Публикуется только черновик")
            conn.execute("UPDATE orders SET status = 'open' WHERE id = ?", (order_id,))
            self.log_order_change(conn, order_id, user["id"], "status", "draft", "open")
            makers = conn.execute("SELECT id FROM users WHERE role = 'maker'").fetchall()
            for m in makers:
                create_notification(conn, m["id"], "new_order",
                    "Новый заказ", f"{user['name']} опубликовал заказ: {order['title']}",
                    "/market")
        self.send_json(200, {"ok": True})

    def api_invite_to_quote(self, company_id):
        data = self.read_json()
        order_id = int(data.get("order_id", 0))
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            company = conn.execute("SELECT * FROM users WHERE id = ?", (company_id,)).fetchone()
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not company or not order:
                return self.send_error_json(404, "Компания или заказ не найдены")
            if order["client_id"] != user["id"] or order["status"] not in ("open", "progress"):
                return self.send_error_json(403, "Приглашать можно только к своему открытому заказу")
            thread_id = ensure_thread(conn, order_id, user["id"], company_id)
            conn.execute(
                "INSERT INTO messages (thread_id, author_id, body, created_at) VALUES (?, ?, ?, ?)",
                (thread_id, user["id"], f"Прошу рассчитать заказ: «{order['title']}» ({order['budget']} руб., {order['deadline']}).", now()),
            )
            create_notification(conn, company_id, "message",
                "Запрос расчёта", f"{user['name']} просит рассчитать заказ «{order['title']}»",
                "/chat")
        self.send_json(200, {"ok": True})

    def api_create_report(self):
        data = self.read_json()
        target_type = data.get("target_type", "")
        target_id = data.get("target_id")
        reason = data.get("reason", "").strip()[:500]
        if target_type not in ("order", "company", "service", "review", "user"):
            return self.send_error_json(400, "Некорректный тип объекта")
        if not target_id:
            return self.send_error_json(400, "Не указан объект жалобы")
        if not reason:
            return self.send_error_json(400, "Опишите причину жалобы")
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            conn.execute(
                "INSERT INTO reports (reporter_id, target_type, target_id, reason, status, created_at) VALUES (?, ?, ?, ?, 'pending', ?)",
                (user["id"], target_type, int(target_id), reason, now()),
            )
        self.send_json(200, {"ok": True})

    def api_admin_hide_content(self):
        data = self.read_json()
        target_type = data.get("target_type", "")
        target_id = int(data.get("target_id", 0))
        hidden = 1 if data.get("hidden") else 0
        table = {"order": "orders", "service": "services"}.get(target_type)
        if not table or not target_id:
            return self.send_error_json(400, "Некорректный объект скрытия")
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            cur = conn.execute(f"UPDATE {table} SET is_hidden = ? WHERE id = ?", (hidden, target_id))
            if cur.rowcount == 0:
                return self.send_error_json(404, "Объект не найден")
            action = "hide_content" if hidden else "unhide_content"
            self.log_admin_activity(conn, admin["id"], action, target_type, target_id,
                                    f"{action} #{target_id}")
        self.send_json(200, {"ok": True})

    def api_maker_stats(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            if user["role"] != "maker":
                return self.send_error_json(403, "Только для производителя")
            responses_count = conn.execute("SELECT COUNT(*) FROM responses WHERE maker_id = ?", (user["id"],)).fetchone()[0]
            chosen_count = conn.execute("SELECT COUNT(*) FROM orders WHERE selected_maker_id = ?", (user["id"],)).fetchone()[0]
            active_orders = conn.execute("SELECT COUNT(*) FROM orders WHERE selected_maker_id = ? AND status = 'progress'", (user["id"],)).fetchone()[0]
            closed = conn.execute("SELECT COUNT(*), COALESCE(SUM(budget), 0), COALESCE(AVG(budget), 0) FROM orders WHERE selected_maker_id = ? AND status = 'closed'", (user["id"],)).fetchone()
            total_hours = conn.execute("SELECT COALESCE(SUM(hours), 0) FROM time_tracking WHERE user_id = ?", (user["id"],)).fetchone()[0]
            avg_rating = conn.execute("SELECT COALESCE(AVG(rating), 0) FROM reviews WHERE company_id = ?", (user["id"],)).fetchone()[0]
            by_month = rows_to_list(conn.execute(
                "SELECT strftime('%Y-%m', created_at) AS month, COUNT(*) AS cnt FROM responses WHERE maker_id = ? GROUP BY month ORDER BY month DESC LIMIT 6",
                (user["id"],),
            ).fetchall())
        self.send_json(200, {
            "responses_count": responses_count,
            "chosen_count": chosen_count,
            "conversion_rate": round(chosen_count / responses_count * 100, 1) if responses_count else 0,
            "active_orders": active_orders,
            "closed_orders": closed[0],
            "revenue": closed[1],
            "avg_order_budget": round(closed[2]) if closed[0] else 0,
            "total_hours": round(total_hours, 1),
            "avg_rating": round(avg_rating, 1),
            "by_month": by_month,
        })

    def api_upload_gallery(self):
        try:
            fields, files = self.read_multipart()
            if not files:
                return self.send_error_json(400, "Файл не загружен")
            with connect() as conn:
                user = self.require_user(conn)
                if not user:
                    return
                for file in files:
                    stored, original = store_upload(f"gallery_{user['id']}", file["filename"], file["content"])
                    conn.execute(
                        "INSERT INTO company_gallery (user_id, original_name, stored_name, size, mime, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (user["id"], original, stored, len(file["content"]), file["mime"], now()),
                    )
            self.send_json(200, {"ok": True})
        except Exception as exc:
            self.send_error_json(400, str(exc))

    def api_delete_gallery_item(self, gallery_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            item = conn.execute("SELECT * FROM company_gallery WHERE id = ?", (gallery_id,)).fetchone()
            if not item:
                return self.send_error_json(404, "Работа не найдена")
            if item["user_id"] != user["id"] and user["role"] != "admin":
                return self.send_error_json(403, "Нет доступа")
            file_path = UPLOAD_DIR / item["stored_name"]
            if file_path.exists():
                file_path.unlink()
            conn.execute("DELETE FROM company_gallery WHERE id = ?", (gallery_id,))
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
            files_by_message = {}
            for f in conn.execute(
                "SELECT * FROM message_files WHERE thread_id = ? ORDER BY id",
                (thread_id,),
            ).fetchall():
                files_by_message.setdefault(f["message_id"], []).append({
                    "id": f["id"],
                    "name": f["original_name"],
                    "size": f["size"],
                    "mime": f["mime"],
                    "url": f"/uploads/{f['stored_name']}",
                })
            for msg in messages:
                msg["files"] = files_by_message.get(msg["id"], [])
        self.send_json(200, {"messages": messages})

    def api_upload_thread_file(self, thread_id):
        try:
            fields, files = self.read_multipart()
            if not files:
                return self.send_error_json(400, "Файл не загружен")
            with connect() as conn:
                user = self.require_user(conn)
                if not user:
                    return
                thread = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
                if not thread or user["id"] not in (thread["client_id"], thread["maker_id"]):
                    return self.send_error_json(403, "Нет доступа к переписке")
                if not check_rate_limit(f"msg:{user['id']}", 60, 60):
                    return self.send_error_json(429, "Слишком часто отправляете сообщения")
                for file in files:
                    stored, original = store_upload(f"chat_{thread_id}", file["filename"], file["content"])
                    cur = conn.execute(
                        "INSERT INTO messages (thread_id, author_id, body, created_at) VALUES (?, ?, ?, ?)",
                        (thread_id, user["id"], f"📎 {original}", now()),
                    )
                    conn.execute(
                        "INSERT INTO message_files (message_id, thread_id, user_id, original_name, stored_name, size, mime, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (cur.lastrowid, thread_id, user["id"], original, stored, len(file["content"]), file["mime"], now()),
                    )
                    msg = conn.execute(
                        "SELECT m.*, u.name AS author_name FROM messages m JOIN users u ON u.id = m.author_id WHERE m.id = ?",
                        (cur.lastrowid,),
                    ).fetchone()
                    message = row_to_dict(msg)
                    message["files"] = [{
                        "id": cur.lastrowid, "name": original,
                        "size": len(file["content"]), "mime": file["mime"],
                        "url": f"/uploads/{stored}",
                    }]
            try:
                from ws_server import ws_manager
                ws_manager.broadcast_to_thread(thread_id, {
                    "type": "message",
                    "thread_id": thread_id,
                    "message": message,
                })
            except ImportError:
                pass
            with connect() as conn:
                notify_user = thread["client_id"] if user["id"] == thread["maker_id"] else thread["maker_id"]
                create_notification(conn, notify_user, "message",
                    "Новое сообщение", f"{user['name']}: 📎 файл",
                    f"/chat")
            self.send_json(200, {"ok": True, "message": message})
        except Exception as exc:
            self.send_error_json(400, str(exc))

    def api_send_message(self, thread_id):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            if not check_rate_limit(f"msg:{user['id']}", 60, 60):
                return self.send_error_json(429, "Слишком часто отправляете сообщения")
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
        with connect() as conn:
            viewer = self.current_user(conn)
            is_own = user_id and viewer and viewer["id"] == int(user_id)
            where = []
            values = []
            if user_id:
                where.append("services.user_id = ?")
                values.append(int(user_id))
            if not is_own:
                where.append("services.is_hidden = 0")
            where_clause = (" WHERE " + " AND ".join(where)) if where else ""
            total = conn.execute(f"SELECT COUNT(*) FROM services{where_clause}", values).fetchone()[0]
            sql = f"""
                SELECT services.*, users.name AS company_name, users.city AS company_city
                FROM services JOIN users ON users.id = services.user_id
                {where_clause}
                ORDER BY services.created_at DESC, services.id DESC
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
                    stored, original = store_upload(f"svc_{service_id}", file["filename"], file["content"])
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
                stored, _ = store_upload(f"logo_{user['id']}", file["filename"], file["content"])
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

    # --- Email verification ---
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
            base_url = f"http://{self.headers.get('Host', '127.0.0.1:8000')}"
            from mailer import send_email
            send_email(
                user["email"],
                "Подтвердите email на Meblio",
                "Для подтверждения адреса перейдите по ссылке:",
                link_url=f"{base_url}/api/verify-email?token={verify_token}",
            )
            payload = {"ok": True}
            if os.environ.get("MEBLIO_DEV", "1") == "1":
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

    # --- 2FA login (second step) ---
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
            trust_value = make_trust_cookie(get_tfa_trust_secret(conn), user["id"])
        self.send_json(200, {"user": self.public_user(row_to_dict(user))}, {"Set-Cookie": [
            f"meblio_session={token}; Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age=604800",
            f"{TRUST_DEVICE_COOKIE}={trust_value}; Path=/; HttpOnly; SameSite=Lax; Secure; Max-Age={TRUST_DEVICE_DAYS * 86400}",
        ]})

    # --- Password recovery ---
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
                base_url = f"http://{self.headers.get('Host', '127.0.0.1:8000')}"
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
            base_url = f"http://{self.headers.get('Host', '127.0.0.1:8000')}"
            from mailer import send_email
            send_email(
                new_email,
                "Подтвердите новый email на Meblio",
                "Для подтверждения адреса перейдите по ссылке:",
                link_url=f"{base_url}/api/verify-email?token={verify_token}",
            )
            payload = {"ok": True}
            if os.environ.get("MEBLIO_DEV", "1") == "1":
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
                   ORDER BY orders.created_at DESC, orders.id DESC""",
                (user["id"], user["id"]),
            ).fetchall())
        # Generate simple CSV (Excel-compatible with UTF-8 BOM)
        import io
        output = io.StringIO()
        output.write("\ufeff")  # BOM for Excel
        output.write("ID,Название,Тип,Количество,Город,Бюджет,Срок,Статус,Заказчик,Исполнитель,Дата\n")
        for o in orders:
            output.write(
                f'{o["id"]},"{csv_safe(o["title"])}","{csv_safe(o["type"])}",{o["quantity"]},'
                f'"{csv_safe(o["city"])}",{o["budget"]},"{csv_safe(o["deadline"])}",'
                f'"{o["status"]}","{csv_safe(o["client_name"])}",'
                f'"{csv_safe(o.get("selected_maker_name") or "")}","{o["created_at"]}"\n'
            )
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
            expires = (datetime.datetime.now() + datetime.timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
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
                if not check_rate_limit(f"review:{user['id']}", 5, 3600):
                    return self.send_error_json(429, "Слишком много отзывов")
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
                if order_id:
                    order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
                    if not order or order["status"] != "closed" or order["client_id"] != user["id"] or order["selected_maker_id"] != company_id:
                        return self.send_error_json(403, "Отзыв можно оставить только по завершённому заказу с этой компанией")
                else:
                    deal = conn.execute(
                        "SELECT id FROM orders WHERE client_id = ? AND selected_maker_id = ? AND status = 'closed' LIMIT 1",
                        (user["id"], company_id),
                    ).fetchone()
                    if not deal:
                        return self.send_error_json(403, "Отзыв можно оставить только после завершённой сделки с этой компанией")
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
        ip = self.client_address[0]
        if not check_rate_limit(f"search:{ip}", 30, 60):
            return self.send_error_json(429, "Слишком частые поисковые запросы")
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

    # --- Articles ---
    def api_articles_list(self):
        with connect() as conn:
            rows = rows_to_list(conn.execute(
                "SELECT slug, title, excerpt, updated_at FROM articles WHERE is_published = 1 ORDER BY updated_at DESC"
            ).fetchall())
        self.send_json(200, {"articles": rows})

    def api_article_detail(self, slug):
        with connect() as conn:
            row = conn.execute(
                "SELECT slug, title, excerpt, body_md, updated_at FROM articles WHERE slug = ? AND is_published = 1",
                (slug,),
            ).fetchone()
        if not row:
            return self.send_error_json(404, "Статья не найдена")
        self.send_json(200, {"article": row_to_dict(row)})

    def api_admin_articles_list(self):
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            rows = rows_to_list(conn.execute(
                "SELECT id, slug, title, excerpt, is_published, updated_at FROM articles ORDER BY updated_at DESC"
            ).fetchall())
        self.send_json(200, {"articles": rows})

    def api_admin_article_save(self):
        data = self.read_json()
        slug = data.get("slug", "").strip()
        title = data.get("title", "").strip()
        if not re.match(r"^[\w-]+$", slug or "") or not title:
            return self.send_error_json(400, "Нужны корректные slug и заголовок")
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            article_id = data.get("id")
            fields = (title, data.get("excerpt", "").strip(), data.get("body_md", ""), 1 if data.get("is_published") else 0)
            if article_id:
                conn.execute(
                    "UPDATE articles SET title=?, excerpt=?, body_md=?, is_published=?, updated_at=? WHERE id=?",
                    (*fields, now(), int(article_id)),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO articles (slug, title, excerpt, body_md, is_published, author_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (slug, *fields, admin["id"], now(), now()),
                )
                article_id = cur.lastrowid
            self.log_admin_activity(conn, admin["id"], "save_article", "article", article_id, title)
        self.send_json(200, {"ok": True, "id": article_id})

    def api_admin_article_delete(self, article_id):
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))
            self.log_admin_activity(conn, admin["id"], "delete_article", "article", article_id, "")
        self.send_json(200, {"ok": True})

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
                    stored, original = store_upload(f"doc_{user['id']}", file["filename"], file["content"])
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
