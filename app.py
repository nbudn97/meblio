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
    is_dev_mode,
    public_base_url,
    public_host,
    canonical_base_url,
)
from logger import get_logger
from api_admin import AdminMixin
from api_catalog import CatalogMixin
from api_ai import AiMixin
from api_accounts import AccountMixin
from api_orders import OrderMixin
from api_market import MarketMixin
from auth_util import (
    verify_totp,
    TRUST_DEVICE_COOKIE,
    TRUST_DEVICE_DAYS,
    get_tfa_trust_secret,
    make_trust_cookie,
    verify_trust_cookie,
    read_trusted_user_id,
    create_pending_token,
    generate_csrf_token,
    validate_csrf_token,
)

logger = get_logger("http")

STATIC_FILES = {
    "/": "index.html",
    "/index.html": "index.html",
    "/styles.css": "styles.css",
    "/script.js": "script.js",
    "/meblio.png": "meblio.png",
    "/hero-workshop.png": "hero-workshop.png",
    "/sw.js": "sw.js",
    "/manifest.json": "manifest.json",
}
def security_headers(host=""):
    """Security headers; connect-src allows same-origin wss (nginx /ws) and local dev ws port."""
    ws_port = os.environ.get("WS_PORT", "8001")
    connect = "'self'"
    if host:
        connect += f" wss://{host}"
    connect += f" ws://127.0.0.1:{ws_port}"
    metrica_id = os.environ.get("MEBLIO_METRICA_ID", "").strip()
    script_src = "'self'"
    img_src = "'self' data:"
    if metrica_id:
        script_src += " https://mc.yandex.ru"
        img_src += " https://mc.yandex.ru https://mc.yandex.net"
        connect += " https://mc.yandex.ru https://mc.yandex.net"
    return {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Content-Security-Policy": (
            "default-src 'self'; "
            f"script-src {script_src}; "
            "style-src 'self' 'unsafe-inline'; "
            "font-src 'self'; "
            f"img-src {img_src}; "
            f"connect-src {connect}"
        ),
    }
ORDER_ID_RE = re.compile(r"^/api/orders/(\d+)/")
ORDER_STAGE_RE = re.compile(r"^/api/orders/(\d+)/stages/(\d+)$")
ORDER_STAGES_RE = re.compile(r"^/api/orders/(\d+)/stages$")
ORDER_ACCEPT_RE = re.compile(r"^/api/orders/(\d+)/accept$")
ORDER_DUPLICATE_RE = re.compile(r"^/api/orders/(\d+)/duplicate$")
ORDER_PROPOSALS_RE = re.compile(r"^/api/orders/(\d+)/proposals$")
PROPOSAL_RE = re.compile(r"^/api/proposals/(\d+)$")
PROPOSAL_STATUS_RE = re.compile(r"^/api/proposals/(\d+)/status$")
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


def seo_for_path(path, headers=None):
    base_title = "Meblio — площадка для заказчиков и производителей мебели"
    base_desc = ("Meblio — рабочая площадка для общения заказчиков мебели и мебельных производств: "
                 "заказы, отклики, личные кабинеты и чат.")
    info = {"title": base_title, "description": base_desc,
            "canonical": f"{canonical_base_url(headers)}{path}", "json_ld": None}
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
        "/privacy": ("Политика конфиденциальности", "Политика обработки и защиты персональных данных пользователей Meblio в соответствии с 152-ФЗ."),
        "/offer": ("Публичная оферта", "Условия пользования информационной площадкой Meblio: права и обязанности сторон, порядок оказания услуг."),
        "/tariffs": ("Тарифы Free и Pro", "Тарифы Meblio: free с лимитом 5 откликов в месяц и Pro без ограничений, гарантия 14 дней."),
    }
    if path in view_titles:
        info["title"] = f"{view_titles[path][0]} | Meblio"
        info["description"] = view_titles[path][1]
    return info


def render_index(self, path):
    html = load_index_template()
    if not html:
        return self.send_error_json(404, "Файл не найден")
    seo = seo_for_path(path, self.headers)
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
    metrica_id = os.environ.get("MEBLIO_METRICA_ID", "").strip()
    if metrica_id:
        html = html.replace("</body>", '    <script src="/metrica.js"></script>\n</body>', 1)
    data = html.encode("utf-8")
    self.send_response(200)
    self.send_header("Content-Type", "text/html; charset=utf-8")
    self.send_header("Content-Length", str(len(data)))
    self.send_header("Cache-Control", "no-store")
    for header, value in security_headers(self.headers.get("Host", "")).items():
        self.send_header(header, value)
    self.end_headers()
    self.wfile.write(data)


def serve_sitemap(self):
    host = public_host(self.headers)
    today = now()[:10]
    urls = [f"https://{host}/", f"https://{host}/privacy", f"https://{host}/offer", f"https://{host}/tariffs"]
    with connect() as conn:
        for row in conn.execute("SELECT id FROM users WHERE role = 'maker' AND is_public = 1 AND is_moderation_hidden = 0").fetchall():
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
    host = public_host(self.headers)
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


class MeblioHandler(AccountMixin, OrderMixin, MarketMixin, AdminMixin, CatalogMixin, AiMixin, BaseHTTPRequestHandler):
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
        m = ORDER_STAGES_RE.match(path)
        if m:
            return self.api_order_stages(int(m.group(1)))
        m = ORDER_STAGE_RE.match(path)
        if m:
            return self.api_update_order_stage(int(m.group(1)), int(m.group(2)))
        m = ORDER_ACCEPT_RE.match(path)
        if m:
            return self.api_accept_order(int(m.group(1)))
        m = ORDER_ID_RE.match(path)
        if m and path.endswith("/contract"):
            return self.api_order_contract(int(m.group(1)))
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
        if path == "/api/maker/funnel":
            return self.api_maker_funnel()
        if path == "/api/deadlines/check":
            return self.api_check_deadlines()
        m = ORDER_PROPOSALS_RE.match(path)
        if m:
            return self.api_list_proposals(int(m.group(1)))
        if path == "/api/tariff":
            return self.api_tariff_info()
        if path == "/api/ai/history":
            return self.api_ai_history()
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
        if path.startswith("/fonts/"):
            name = unquote(path.replace("/fonts/", "", 1))
            if "/" in name or "\\" in name or name.startswith("."):
                return self.send_error_json(404, "Файл не найден")
            return self.serve_static(f"fonts/{name}")
        if path.startswith("/uploads/"):
            return self.serve_upload(path)
        if path == "/config.js":
            return self.serve_config_js()
        if path == "/metrica.js":
            return self.serve_metrica_js()
        if path in STATIC_FILES:
            if path in ("/", "/index.html"):
                return render_index(self, path)
            return self.serve_static(STATIC_FILES[path])
        if path.startswith("/api/"):
            return self.send_error_json(404, "Страница не найдена")
        last_segment = path.rsplit("/", 1)[-1]
        if "." in last_segment and not last_segment.startswith("."):
            return self.send_error_json(404, "Файл не найден")
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
        if path == "/api/ai/chat":
            return self.api_ai_chat()
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
            if path.endswith("/stages"):
                return self.api_add_order_stage(order_id)
            if path.endswith("/accept"):
                return self.api_accept_order(order_id)
            if path.endswith("/duplicate"):
                return self.api_duplicate_order(order_id)
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
        if path == "/api/deadlines/check":
            return self.api_check_deadlines()
        if path == "/api/admin/verify-requisites":
            return self.api_admin_verify_requisites()
        if path == "/api/estimate":
            return self.api_estimate()
        m = ORDER_PROPOSALS_RE.match(path)
        if m:
            return self.api_create_proposal(int(m.group(1)))
        m = PROPOSAL_STATUS_RE.match(path)
        if m:
            return self.api_update_proposal_status(int(m.group(1)))
        if path == "/api/tariff/upgrade":
            return self.api_tariff_upgrade()
        if path == "/api/messenger/link":
            return self.api_link_messenger()
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
        m = INVOICE_RE.match(path)
        if m:
            return self.api_update_invoice(int(m.group(1)))
        m = ORDER_STAGE_RE.match(path)
        if m:
            return self.api_update_order_stage(int(m.group(1)), int(m.group(2)))
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
        if path == "/api/ai/history":
            return self.api_ai_clear()
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
            "is_public": int(user.get("is_public", 1)),
            "inn": user.get("inn", ""),
            "ogrn": user.get("ogrn", ""),
            "website": user.get("website", ""),
            "verified_requisites_at": user.get("verified_requisites_at") or "",
            "plan": user.get("plan") or "free",
            "telegram_chat_id": user.get("telegram_chat_id") or "",
            "max_chat_id": user.get("max_chat_id") or "",
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
        responses = rows_to_list(conn.execute(
            f"""
            SELECT responses.*, users.name AS maker_name, users.city AS maker_city
            FROM responses JOIN users ON users.id = responses.maker_id
            WHERE order_id IN ({placeholders}) ORDER BY responses.created_at DESC
            """,
            ids,
        ).fetchall())
        for r in responses:
            responses_by_order.setdefault(r["order_id"], []).append(r)
        for o in orders:
            o["files"] = files_by_order.get(o["id"], [])
            o["responses"] = responses_by_order.get(o["id"], [])
        return orders

    def serve_config_js(self):
        """Runtime frontend config (external script: allowed by CSP, not cacheable)."""
        payload = json_dumps({
            "wsPort": int(os.environ.get("WS_PORT", "8001")),
            "dev": is_dev_mode(),
        })
        data = b"window.MEBLIO_CONFIG=" + payload + b";"
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for header, value in security_headers(self.headers.get("Host", "")).items():
            self.send_header(header, value)
        self.end_headers()
        self.wfile.write(data)

    def serve_metrica_js(self):
        """External Metrika loader (CSP script-src 'self'; tag.js allowed when configured)."""
        metrica_id = os.environ.get("MEBLIO_METRICA_ID", "").strip()
        if not metrica_id:
            return self.send_error_json(404, "Метрика не настроена")
        if not metrica_id.isdigit():
            return self.send_error_json(400, "Некорректный MEBLIO_METRICA_ID")
        code = (
            "(function(m,e,t,r,i,k,a){m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};"
            "m[i].l=1*new Date();k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,"
            "a.parentNode.insertBefore(k,a)})(window,document,'script','https://mc.yandex.ru/metrika/tag.js','ym');"
            f"ym({metrica_id},'init',{{clickmap:true,trackLinks:true,accurateTrackBounce:true}});"
        )
        data = code.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for header, value in security_headers(self.headers.get("Host", "")).items():
            self.send_header(header, value)
        self.end_headers()
        self.wfile.write(data)

    def serve_static(self, filename):
        path = BASE_DIR / filename
        if not path.exists():
            return self.send_error_json(404, "Файл не найден")
        mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        stat = path.stat()
        etag = f'W/"{int(stat.st_mtime)}-{stat.st_size}"'
        cache_control = "no-store" if filename in ("index.html", "sw.js") or filename.startswith("fonts/") else "public, max-age=3600"
        if self.headers.get("If-None-Match") == etag and cache_control != "no-store":
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache_control)
            for header, value in security_headers(self.headers.get("Host", "")).items():
                self.send_header(header, value)
            self.end_headers()
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime + ("; charset=utf-8" if mime.startswith("text/") else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("ETag", etag)
        self.send_header("Last-Modified", self.date_time_string(int(stat.st_mtime)))
        self.send_header("Cache-Control", cache_control)
        for header, value in security_headers(self.headers.get("Host", "")).items():
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


    def api_admin_verify_requisites(self):
        data = self.read_json()
        user_id = int(data.get("user_id", 0))
        verified = bool(data.get("verified"))
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            target = conn.execute("SELECT id, inn, ogrn FROM users WHERE id = ?", (user_id,)).fetchone()
            if not target:
                return self.send_error_json(404, "Пользователь не найден")
            if verified and not (target["inn"] or target["ogrn"]):
                return self.send_error_json(400, "У пользователя не заполнены ИНН/ОГРН")
            conn.execute(
                "UPDATE users SET verified_requisites_at = ? WHERE id = ?",
                (now() if verified else None, user_id),
            )
            self.log_admin_activity(
                conn, admin["id"],
                "verify_requisites" if verified else "unverify_requisites",
                "user", user_id,
                f"{'verified' if verified else 'unverified'} requisites #{user_id}",
            )
        self.send_json(200, {"ok": True, "verified": verified})


def main():
    init_db()
    port = int(os.environ.get("PORT", "8000"))
    ws_port = int(os.environ.get("WS_PORT", "8001"))

    # Start WebSocket server
    from ws_server import WebSocketServer
    ws_server = WebSocketServer(ws_port)
    ws_server.start()

    # Deadline reminder scheduler (every 6 hours + once at boot)
    def _deadline_loop():
        import time as _time
        while True:
            try:
                with connect() as conn:
                    n = MeblioHandler.run_deadline_reminders(conn)
                    if n:
                        logger.info("deadline reminders sent: %s", n)
            except Exception as exc:
                logger.warning("deadline reminder tick failed: %s", exc)
            _time.sleep(6 * 3600)

    import threading as _threading
    _threading.Thread(target=_deadline_loop, daemon=True, name="deadline-reminders").start()

    server = ThreadingHTTPServer(("127.0.0.1", port), MeblioHandler)
    print(f"Meblio portal: http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        ws_server.stop()
        print("\nServer stopped")


if __name__ == "__main__":
    main()
