"""Catalog & project-tools API mixin: materials, templates, invoices, delivery,
order history, suppliers, certificates, time tracking, client ratings."""
import secrets
import sqlite3
from urllib.parse import parse_qs

from db import UPLOAD_DIR, connect, row_to_dict, rows_to_list, now
from common import PAGE_SIZE, create_notification, safe_filename


class CatalogMixin:
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
