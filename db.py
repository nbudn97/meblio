import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("MEBLIO_DB", BASE_DIR / "meblio.db"))
UPLOAD_DIR = Path(os.environ.get("MEBLIO_UPLOADS", BASE_DIR / "uploads"))

COMPANY_TYPES = [
    ("client", "Заказчик"),
    ("designer", "Проектировщик"),
    ("manufacturer", "Производитель"),
    ("serial", "Серийное производство"),
    ("supplier", "Поставщик"),
]

REGIONS = [
    "Москва", "Санкт-Петербург", "Московская область", "Ленинградская область",
    "Краснодарский край", "Свердловская область", "Новосибирская область",
    "Тюменская область", "Ростовская область", "Волгоградская область",
    "Самарская область", "Челябинская область", "Нижегородская область",
    "Воронежская область", "Пермский край", "Омская область",
    "Красноярский край", "Республика Татарстан", "Башкортостан",
    "Тульская область", "Калининградская область", "Иркутская область",
]


def _open_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    # Built-in LOWER()/UPPER() are ASCII-only in SQLite; override with Unicode-aware versions
    conn.create_function("LOWER", 1, lambda s: s.lower() if isinstance(s, str) else s)
    conn.create_function("UPPER", 1, lambda s: s.upper() if isinstance(s, str) else s)
    return conn


@contextmanager
def connect():
    """Connection as a context manager: commits on success, rolls back on error, always closes."""
    conn = _open_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return salt, digest.hex()


def verify_password(password, salt, digest):
    _, check = hash_password(password, salt)
    return hmac.compare_digest(check, digest)


def row_to_dict(row):
    return dict(row) if row is not None else None


def rows_to_list(rows):
    return [dict(row) for row in rows]


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


SESSION_TTL_DAYS = 14


def session_cutoff():
    import datetime
    cutoff = datetime.datetime.now() - datetime.timedelta(days=SESSION_TTL_DAYS)
    return cutoff.strftime("%Y-%m-%d %H:%M:%S")


def purge_expired_sessions(conn):
    conn.execute("DELETE FROM sessions WHERE created_at < ?", (session_cutoff(),))


def init_db():
    UPLOAD_DIR.mkdir(exist_ok=True)
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS regions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              role TEXT NOT NULL CHECK (role IN ('client', 'maker', 'admin')),
              company_type TEXT NOT NULL DEFAULT 'client',
              name TEXT NOT NULL,
              email TEXT NOT NULL UNIQUE,
              city TEXT NOT NULL DEFAULT '',
              region_id INTEGER REFERENCES regions(id),
              phone TEXT NOT NULL DEFAULT '',
              about TEXT NOT NULL DEFAULT '',
              skills TEXT NOT NULL DEFAULT '',
              capacity TEXT NOT NULL DEFAULT '',
              logo TEXT NOT NULL DEFAULT '',
              password_salt TEXT NOT NULL,
              password_hash TEXT NOT NULL,
              is_verified INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
              token TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS orders (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              client_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              title TEXT NOT NULL,
              type TEXT NOT NULL,
              quantity INTEGER NOT NULL,
              city TEXT NOT NULL,
              budget INTEGER NOT NULL,
              deadline TEXT NOT NULL,
              details TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'open',
              selected_maker_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS order_files (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
              original_name TEXT NOT NULL,
              stored_name TEXT NOT NULL,
              size INTEGER NOT NULL,
              mime TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS responses (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
              maker_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              price INTEGER NOT NULL,
              days INTEGER NOT NULL,
              message TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE(order_id, maker_id)
            );

            CREATE TABLE IF NOT EXISTS threads (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
              client_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              maker_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              created_at TEXT NOT NULL,
              UNIQUE(order_id, maker_id)
            );

            CREATE TABLE IF NOT EXISTS messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE,
              author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              body TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS services (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              title TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '',
              price_type TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS service_files (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              service_id INTEGER NOT NULL REFERENCES services(id) ON DELETE CASCADE,
              original_name TEXT NOT NULL,
              stored_name TEXT NOT NULL,
              size INTEGER NOT NULL,
              mime TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS company_gallery (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              original_name TEXT NOT NULL,
              stored_name TEXT NOT NULL,
              size INTEGER NOT NULL,
              mime TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS favorites (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              company_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              created_at TEXT NOT NULL,
              UNIQUE(user_id, company_id)
            );

            CREATE TABLE IF NOT EXISTS notifications (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              type TEXT NOT NULL,
              title TEXT NOT NULL,
              body TEXT NOT NULL DEFAULT '',
              link TEXT NOT NULL DEFAULT '',
              is_read INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS notification_preferences (
              user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
              new_order INTEGER NOT NULL DEFAULT 1,
              response INTEGER NOT NULL DEFAULT 1,
              message INTEGER NOT NULL DEFAULT 1,
              chosen INTEGER NOT NULL DEFAULT 1,
              review INTEGER NOT NULL DEFAULT 1,
              order_status INTEGER NOT NULL DEFAULT 1,
              system INTEGER NOT NULL DEFAULT 1,
              push_enabled INTEGER NOT NULL DEFAULT 1,
              email_enabled INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS reviews (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              reviewer_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              company_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              order_id INTEGER REFERENCES orders(id) ON DELETE SET NULL,
              rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
              text TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              UNIQUE(reviewer_id, company_id, order_id)
            );

            CREATE TABLE IF NOT EXISTS email_verifications (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              token TEXT NOT NULL UNIQUE,
              purpose TEXT NOT NULL DEFAULT 'verify',
              expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pending_tfa (
              token TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reports (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              reporter_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              target_type TEXT NOT NULL,
              target_id INTEGER NOT NULL,
              reason TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'pending',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS company_documents (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              original_name TEXT NOT NULL,
              stored_name TEXT NOT NULL,
              doc_type TEXT NOT NULL DEFAULT 'other',
              size INTEGER NOT NULL,
              mime TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS csrf_tokens (
              token TEXT PRIMARY KEY,
              user_id INTEGER,
              session_token TEXT,
              expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS admin_activity (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              admin_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              action TEXT NOT NULL,
              target_type TEXT NOT NULL DEFAULT '',
              target_id INTEGER,
              details TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS materials (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              category TEXT NOT NULL DEFAULT 'ldsp',
              price_per_m2 INTEGER NOT NULL DEFAULT 0,
              thickness_mm INTEGER NOT NULL DEFAULT 18,
              description TEXT NOT NULL DEFAULT '',
              color TEXT NOT NULL DEFAULT '',
              brand TEXT NOT NULL DEFAULT '',
              is_active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS order_templates (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              type TEXT NOT NULL,
              quantity INTEGER NOT NULL DEFAULT 1,
              city TEXT NOT NULL DEFAULT '',
              budget INTEGER NOT NULL DEFAULT 0,
              deadline TEXT NOT NULL DEFAULT '',
              details TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS order_history (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              field TEXT NOT NULL,
              old_value TEXT NOT NULL DEFAULT '',
              new_value TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS delivery_tracking (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
              status TEXT NOT NULL DEFAULT 'production',
              location TEXT NOT NULL DEFAULT '',
              notes TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invoices (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
              from_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              to_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              amount INTEGER NOT NULL,
              status TEXT NOT NULL DEFAULT 'pending',
              due_date TEXT NOT NULL DEFAULT '',
              items TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tfa_secrets (
              user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
              secret TEXT NOT NULL,
              enabled INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS suppliers (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              contact_name TEXT NOT NULL DEFAULT '',
              email TEXT NOT NULL DEFAULT '',
              phone TEXT NOT NULL DEFAULT '',
              website TEXT NOT NULL DEFAULT '',
              city TEXT NOT NULL DEFAULT '',
              region_id INTEGER REFERENCES regions(id),
              materials TEXT NOT NULL DEFAULT '',
              description TEXT NOT NULL DEFAULT '',
              rating REAL NOT NULL DEFAULT 0,
              is_active INTEGER NOT NULL DEFAULT 1,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS file_versions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              original_file_id INTEGER,
              file_type TEXT NOT NULL DEFAULT 'order',
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              original_name TEXT NOT NULL,
              stored_name TEXT NOT NULL,
              version INTEGER NOT NULL DEFAULT 1,
              size INTEGER NOT NULL,
              mime TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS time_tracking (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              task TEXT NOT NULL DEFAULT '',
              hours REAL NOT NULL DEFAULT 0,
              date TEXT NOT NULL,
              notes TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS company_certificates (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              name TEXT NOT NULL,
              cert_type TEXT NOT NULL DEFAULT 'quality',
              number TEXT NOT NULL DEFAULT '',
              issued_by TEXT NOT NULL DEFAULT '',
              issued_at TEXT NOT NULL DEFAULT '',
              expires_at TEXT NOT NULL DEFAULT '',
              stored_name TEXT NOT NULL DEFAULT '',
              original_name TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS client_ratings (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
              maker_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              client_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
              text TEXT NOT NULL DEFAULT '',
              created_at TEXT NOT NULL,
              UNIQUE(order_id, maker_id)
            );

            CREATE TABLE IF NOT EXISTS api_tokens (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              token TEXT NOT NULL UNIQUE,
              name TEXT NOT NULL DEFAULT '',
              expires_at TEXT NOT NULL,
              created_at TEXT NOT NULL
            );
            """
        )
        existing_regions = conn.execute("SELECT COUNT(*) FROM regions").fetchone()[0]
        if existing_regions == 0:
            for name in REGIONS:
                conn.execute("INSERT OR IGNORE INTO regions (name) VALUES (?)", (name,))
        purge_expired_sessions(conn)

        def ensure_column(table, column, ddl):
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            if column not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

        ensure_column("users", "is_verified", "is_verified INTEGER NOT NULL DEFAULT 0")
        ensure_column("email_verifications", "purpose", "purpose TEXT NOT NULL DEFAULT 'verify'")

        conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id);
            CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read);
            CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_orders_city ON orders(city);
            CREATE INDEX IF NOT EXISTS idx_orders_client ON orders(client_id);
            CREATE INDEX IF NOT EXISTS idx_reviews_company ON reviews(company_id);
            CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);
            CREATE INDEX IF NOT EXISTS idx_responses_order ON responses(order_id);
            CREATE INDEX IF NOT EXISTS idx_threads_order ON threads(order_id);
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            """
        )
        count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        if count == 0:
            seed(conn)
        # Ensure materials and suppliers are seeded
        mat_count = conn.execute("SELECT COUNT(*) FROM materials").fetchone()[0]
        if mat_count == 0:
            seed_materials(conn)
        sup_count = conn.execute("SELECT COUNT(*) FROM suppliers").fetchone()[0]
        if sup_count == 0:
            seed_suppliers(conn)


def create_user(conn, role, name, email, password, city="", phone="", about="", skills="", capacity="", company_type="client", region_id=None):
    salt, digest = hash_password(password)
    cur = conn.execute(
        """
        INSERT INTO users (role, company_type, name, email, city, region_id, phone, about, skills, capacity, password_salt, password_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (role, company_type, name, email.lower(), city, region_id, phone, about, skills, capacity, salt, digest, now()),
    )
    return cur.lastrowid


def ensure_thread(conn, order_id, client_id, maker_id):
    row = conn.execute("SELECT id FROM threads WHERE order_id = ? AND maker_id = ?", (order_id, maker_id)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO threads (order_id, client_id, maker_id, created_at) VALUES (?, ?, ?, ?)",
        (order_id, client_id, maker_id, now()),
    )
    return cur.lastrowid


def seed(conn):
    moscow_id = conn.execute("SELECT id FROM regions WHERE name = 'Москва'").fetchone()
    spb_id = conn.execute("SELECT id FROM regions WHERE name = 'Санкт-Петербург'").fetchone()
    moscow_rid = moscow_id["id"] if moscow_id else None
    spb_rid = spb_id["id"] if spb_id else None

    client_id = create_user(
        conn, "client", "Анна Орлова", "client@meblio.ru", "client123",
        "Москва", "+7 900 000-00-01",
        "Заказываю мебель для апарт-отелей, офисов и коммерческих интерьеров.",
        company_type="client", region_id=moscow_rid,
    )
    maker_id = create_user(
        conn, "maker", "Modul Pro", "maker@meblio.ru", "maker123",
        "Москва", "+7 900 000-00-02",
        "Производим корпусную мебель для офисов, гостиниц и коммерческих объектов.",
        "Офисная мебель, Серийные партии, ЛДСП, Монтаж", "до 120 изделий в месяц",
        company_type="manufacturer", region_id=moscow_rid,
    )
    admin_id = create_user(
        conn, "admin", "Администратор Meblio", "admin@meblio.ru", "admin123",
        "Москва", "+7 900 000-00-00",
        "Администратор платформы Meblio.",
        company_type="client", region_id=moscow_rid,
    )
    maker_2_id = create_user(
        conn, "maker", "Linea Wood", "linea@meblio.ru", "linea123",
        "Санкт-Петербург", "+7 900 000-00-03",
        "Индивидуальные кухни, гардеробные и премиальные корпусные решения.",
        "Кухни, Шкафы, Шпон, МДФ эмаль", "до 45 комплектов в месяц",
        company_type="manufacturer", region_id=spb_rid,
    )
    order_1 = conn.execute(
        """
        INSERT INTO orders (client_id, title, type, quantity, city, budget, deadline, details, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?)
        """,
        (
            client_id, "Кухни для апарт-отеля, 46 комплектов", "Кухни и шкафы",
            46, "Москва", 3900000, "35 дней",
            "ЛДСП Egger, фасады МДФ эмаль, столешницы HPL. Нужны замер, производство, доставка и монтаж.",
            now(),
        ),
    ).lastrowid
    order_2 = conn.execute(
        """
        INSERT INTO orders (client_id, title, type, quantity, city, budget, deadline, details, status, selected_maker_id, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'progress', ?, ?)
        """,
        (
            client_id, "Рабочие места для нового офиса", "Офисная мебель",
            68, "Москва", 1250000, "21 день",
            "Столы, тумбы, шкафы хранения и зона ресепшн. Требуется производство по дизайн-проекту.",
            maker_id, now(),
        ),
    ).lastrowid
    conn.execute(
        "INSERT INTO responses (order_id, maker_id, price, days, message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (order_1, maker_id, 3650000, 32, "Готовы взять в работу. Есть свободная линия раскроя и монтажная бригада.", now()),
    )
    conn.execute(
        "INSERT INTO responses (order_id, maker_id, price, days, message, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (order_1, maker_2_id, 4100000, 38, "Можем сделать фасады в эмали и подготовить образец цвета перед запуском партии.", now()),
    )
    thread_id = ensure_thread(conn, order_1, client_id, maker_id)
    conn.execute(
        "INSERT INTO messages (thread_id, author_id, body, created_at) VALUES (?, ?, ?, ?)",
        (thread_id, maker_id, "Добрый день. Уточните, нужна ли фурнитура Blum или можно рассмотреть аналоги?", now()),
    )
    conn.execute(
        "INSERT INTO messages (thread_id, author_id, body, created_at) VALUES (?, ?, ?, ?)",
        (thread_id, client_id, "Добрый день. В базовом расчете нужен Blum, альтернативу можно дать отдельной строкой.", now()),
    )
    ensure_thread(conn, order_2, client_id, maker_id)

    conn.execute(
        "INSERT INTO services (user_id, title, description, price_type, created_at) VALUES (?, ?, ?, ?, ?)",
        (maker_id, "Производство кухонь", "Корпусные кухни из ЛДСП и МДФ, фасады МДФ эмаль и плёнка.", "по проекту", now()),
    )
    conn.execute(
        "INSERT INTO services (user_id, title, description, price_type, created_at) VALUES (?, ?, ?, ?, ?)",
        (maker_id, "Офисная мебель", "Производство столов, тумб, шкафов для офисов.", "по проекту", now()),
    )
    conn.execute(
        "INSERT INTO services (user_id, title, description, price_type, created_at) VALUES (?, ?, ?, ?, ?)",
        (maker_2_id, "Премиальные кухни", "Кухни из натурального шпона и МДФ эмаль, авторский дизайн.", "по проекту", now()),
    )
    seed_materials(conn)
    seed_suppliers(conn)


def seed_materials(conn):
    materials_data = [
        ("ЛДСП Egger белый", "ldsp", 1800, 18, "Белый ЛДСП Egger, 18мм", "Белый", "Egger"),
        ("ЛДСП Egger серый", "ldsp", 1900, 18, "Серый ЛДСП Egger, 18мм", "Серый", "Egger"),
        ("ЛДСП Egger орех", "ldsp", 2100, 18, "Орех ЛДСП Egger, 18мм", "Орех", "Egger"),
        ("ЛДСП Egger дуб", "ldsp", 2200, 18, "Дуб ЛДСП Egger, 18мм", "Дуб", "Egger"),
        ("ЛДСП Egger венге", "ldsp", 2300, 18, "Венге ЛДСП Egger, 18мм", "Венге", "Egger"),
        ("МДФ эмаль белая", "mdf", 4500, 19, "МДФ с эмалевым покрытием, белый", "Белый", "Россия"),
        ("МДФ эмаль чёрная", "mdf", 5200, 19, "МДФ с эмалевым покрытием, чёрный", "Чёрный", "Россия"),
        ("МДФ шпон дуб", "mdf", 6800, 19, "МДФ с натуральным шпоном дуба", "Дуб", "Россия"),
        ("МДФ шпон орех", "mdf", 7200, 19, "МДФ с натуральным шпоном ореха", "Орех", "Россия"),
        ("Фанера 18мм", "other", 2400, 18, "Фанера берёзовая 18мм", "Натуральный", "Россия"),
        ("Фанера 10мм", "other", 1500, 10, "Фанера берёзовая 10мм", "Натуральный", "Россия"),
        ("ДСП 16мм", "ldsp", 1200, 16, "ДСП 16мм, бюджетный вариант", "Белый", "Россия"),
        ("Столешница HPL", "other", 8500, 38, "Столешница HPL, 38мм", "Белый", "Egger"),
        ("Столешница каменная", "other", 15000, 20, "Столешница из искусственного камня", "Белый", "Светлый"),
    ]
    for name, cat, price, thick, desc, color, brand in materials_data:
        conn.execute(
            "INSERT INTO materials (name, category, price_per_m2, thickness_mm, description, color, brand, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, cat, price, thick, desc, color, brand, now()),
        )


def seed_suppliers(conn):
    suppliers_data = [
        ("Egger Москва", "Иван Петров", "info@egger-moscow.ru", "+7 495 123-45-67", "https://egger.com", "Москва", "ЛДСП, столешницы, кромка", "Официальный дистрибьютор Egger в России"),
        ("Kronospan Russia", "Сергей Иванов", "sales@kronospan.ru", "+7 495 234-56-78", "https://kronospan.com", "Москва", "ЛДСП, МДФ, HDF", "Производитель ЛДСП и МДФ"),
        ("Sonae Arauco", "Мария Сидорова", "info@sonae.ru", "+7 812 345-67-89", "https://sonae.ru", "Санкт-Петербург", "ЛДСП, декоративные панели", "Португальский производитель"),
        ("Русские обои", "Анна Козлова", "opt@russian-wallpaper.ru", "+7 495 456-78-90", "", "Москва", "Плёнки, обои для мебели", "Декоративные плёнки и обои"),
        ("Фурнитура Blum", "Дмитрий Волков", "blum@furniture-hardware.ru", "+7 495 567-89-01", "https://blum.com", "Москва", "Фурнитура Blum, петли, выдвижные системы", "Официальный партнёр Blum"),
        ("Hettich Россия", "Ольга Новикова", "sales@hettich.ru", "+7 495 678-90-12", "https://hettich.com", "Москва", "Фурнитура Hettich, направляющие", "Немецкая фурнитура"),
    ]
    for name, contact, email, phone, website, city, materials, desc in suppliers_data:
        conn.execute(
            "INSERT INTO suppliers (name, contact_name, email, phone, website, city, materials, description, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, contact, email, phone, website, city, materials, desc, now()),
        )
