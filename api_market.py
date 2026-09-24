"""Catalog/market API mixin: companies, services, reviews, search, articles, chat, reports."""

import re
import sqlite3
from urllib.parse import parse_qs

from db import connect, row_to_dict, rows_to_list, now, UPLOAD_DIR, COMPANY_TYPES
from common import PAGE_SIZE, check_rate_limit, create_notification, store_upload

class MarketMixin:
    def api_makers(self):
        with connect() as conn:
            makers = [self.public_user(row_to_dict(row)) for row in conn.execute(
                "SELECT users.*, regions.name AS region_name FROM users LEFT JOIN regions ON regions.id = users.region_id WHERE role = 'maker' AND is_public = 1 AND is_moderation_hidden = 0 ORDER BY name"
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
        where = ["role = 'maker'", "is_public = 1", "is_moderation_hidden = 0"]
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
                    FROM reviews WHERE company_id IN ({stat_placeholders}) AND is_hidden = 0 GROUP BY company_id
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
            viewer = self.current_user(conn)
            if not user["is_public"] and not (viewer and (viewer["id"] == company_id or viewer["role"] == "admin")):
                return self.send_error_json(404, "Компания не найдена")
            if user["is_moderation_hidden"] and not (viewer and (viewer["id"] == company_id or viewer["role"] == "admin")):
                return self.send_error_json(404, "Компания не найдена")
            services = rows_to_list(conn.execute("SELECT * FROM services WHERE user_id = ? ORDER BY created_at DESC", (company_id,)).fetchall())
            gallery = rows_to_list(conn.execute("SELECT * FROM company_gallery WHERE user_id = ? ORDER BY id", (company_id,)).fetchall())
            orders_count = conn.execute("SELECT COUNT(*) FROM orders WHERE client_id = ?", (company_id,)).fetchone()[0]
            responses_count = conn.execute("SELECT COUNT(*) FROM responses WHERE maker_id = ?", (company_id,)).fetchone()[0]
            reviews = rows_to_list(conn.execute(
                "SELECT reviews.*, users.name AS reviewer_name FROM reviews JOIN users ON users.id = reviews.reviewer_id WHERE reviews.company_id = ? AND reviews.is_hidden = 0 ORDER BY reviews.created_at DESC",
                (company_id,),
            ).fetchall())
            avg_rating = conn.execute("SELECT AVG(rating) FROM reviews WHERE company_id = ? AND is_hidden = 0", (company_id,)).fetchone()[0]
            docs = rows_to_list(conn.execute(
                "SELECT id, original_name, doc_type, size FROM company_documents WHERE user_id = ? ORDER BY created_at DESC",
                (company_id,),
            ).fetchall())
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
            for s in services:
                s["params"] = [
                    {"name": p["name"], "value": p["value"]}
                    for p in conn.execute(
                        "SELECT name, value FROM service_params WHERE service_id = ? ORDER BY sort_order, id",
                        (s["id"],),
                    ).fetchall()
                ]
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
            params = rows_to_list(conn.execute(
                "SELECT name, value FROM service_params WHERE service_id = ? ORDER BY sort_order, id",
                (service_id,),
            ).fetchall())
        data = dict(service)
        data["files"] = [{"id": f["id"], "name": f["original_name"], "url": f"/uploads/{f['stored_name']}"} for f in files]
        data["params"] = params
        self.send_json(200, {"service": data})

    def api_create_service(self):
        try:
            with connect() as conn:
                user = self.require_user(conn)
                if not user:
                    return
                if user["role"] != "maker":
                    return self.send_error_json(403, "Услуги может добавлять только производитель")
                fields, files = self.read_multipart()
                cur = conn.execute(
                    "INSERT INTO services (user_id, title, description, price_type, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user["id"], fields.get("title", "").strip(), fields.get("description", "").strip(), fields.get("price_type", "").strip(), now()),
                )
                service_id = cur.lastrowid
                self._save_service_params(conn, service_id, fields.get("params", ""))
                for file in files:
                    stored, original = store_upload(f"svc_{service_id}", file["filename"], file["content"])
                    conn.execute(
                        "INSERT INTO service_files (service_id, original_name, stored_name, size, mime, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (service_id, original, stored, len(file["content"]), file["mime"], now()),
                    )
        except Exception as exc:
            return self.send_error_json(400, str(exc))
        self.send_json(200, {"ok": True, "service_id": service_id})

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
            self._save_service_params(conn, service_id, data.get("params", ""))
        self.send_json(200, {"ok": True})

    def _save_service_params(self, conn, service_id, params_raw):
        """Replace service_params from JSON list [{name,value},...] or empty."""
        import json as _json
        items = []
        if isinstance(params_raw, str) and params_raw.strip():
            try:
                items = _json.loads(params_raw)
            except ValueError:
                items = []
        elif isinstance(params_raw, list):
            items = params_raw
        conn.execute("DELETE FROM service_params WHERE service_id = ?", (service_id,))
        order = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()[:80]
            value = str(item.get("value", "")).strip()[:200]
            if not name:
                continue
            order += 1
            conn.execute(
                "INSERT INTO service_params (service_id, name, value, sort_order) VALUES (?, ?, ?, ?)",
                (service_id, name, value, order),
            )

    def api_delete_service(self, service_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            service = conn.execute("SELECT * FROM services WHERE id = ?", (service_id,)).fetchone()
            if not service or service["user_id"] != user["id"]:
                return self.send_error_json(403, "Нет доступа к услуге")
            conn.execute("DELETE FROM service_files WHERE service_id = ?", (service_id,))
            conn.execute("DELETE FROM service_params WHERE service_id = ?", (service_id,))
            conn.execute("DELETE FROM services WHERE id = ?", (service_id,))
        self.send_json(200, {"ok": True})

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
                WHERE reviews.company_id = ? AND reviews.is_hidden = 0
                ORDER BY reviews.created_at DESC
                """,
                (int(company_id),),
            ).fetchall())
            avg = conn.execute(
                "SELECT AVG(rating) FROM reviews WHERE company_id = ? AND is_hidden = 0",
                (int(company_id),),
            ).fetchone()[0]
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
                "SELECT id, name, city, company_type, about FROM users WHERE role = 'maker' AND is_public = 1 AND is_moderation_hidden = 0 AND (LOWER(name) LIKE ? OR LOWER(about) LIKE ?) LIMIT 10",
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
        if not target_id:
            return self.send_error_json(400, "Некорректный объект скрытия")
        if target_type == "company":
            table, column = "users", "is_moderation_hidden"
        elif target_type == "review":
            table, column = "reviews", "is_hidden"
        elif target_type in ("order", "service"):
            table, column = {"order": "orders", "service": "services"}[target_type], "is_hidden"
        else:
            return self.send_error_json(400, "Некорректный объект скрытия")
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            cur = conn.execute(f"UPDATE {table} SET {column} = ? WHERE id = ?", (hidden, target_id))
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

