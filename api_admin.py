"""Admin API mixin: stats, analytics, activity log, users/orders/services CRUD, bulk ops."""
import sqlite3
from urllib.parse import parse_qs

from db import connect, row_to_dict, rows_to_list, now, create_user
from common import PAGE_SIZE


class AdminMixin:
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
                ORDER BY admin_activity.created_at DESC, admin_activity.id DESC LIMIT ? OFFSET ?
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
                ORDER BY users.created_at DESC, users.id DESC
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
                ORDER BY orders.created_at DESC, orders.id DESC
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
        if status not in ("open", "progress", "closed", "cancelled"):
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
                ORDER BY services.created_at DESC, services.id DESC
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
                if status not in ("open", "progress", "closed", "cancelled"):
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
    # --- Reports moderation ---
    def api_admin_reports(self, query):
        params = parse_qs(query)
        status_filter = params.get("status", [""])[0]
        page = max(1, int(params.get("page", ["1"])[0]))
        offset = (page - 1) * PAGE_SIZE
        where = []
        values = []
        if status_filter:
            where.append("reports.status = ?")
            values.append(status_filter)
        where_clause = (" WHERE " + " AND ".join(where)) if where else ""
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            total = conn.execute(f"SELECT COUNT(*) FROM reports{where_clause}", values).fetchone()[0]
            rows = rows_to_list(conn.execute(
                f"""
                SELECT reports.*, users.name AS reporter_name
                FROM reports JOIN users ON users.id = reports.reporter_id
                {where_clause}
                ORDER BY reports.created_at DESC, reports.id DESC
                LIMIT ? OFFSET ?
                """,
                values + [PAGE_SIZE, offset],
            ).fetchall())
        self.send_json(200, {"reports": rows, "total": total, "page": page, "page_size": PAGE_SIZE})

    def api_admin_report_resolve(self, report_id):
        data = self.read_json()
        status = data.get("status", "resolved")
        hide_target = bool(data.get("hide_target"))
        if status not in ("resolved", "rejected"):
            return self.send_error_json(400, "Некорректный статус")
        with connect() as conn:
            admin = self.require_admin(conn)
            if not admin:
                return
            report = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
            if not report or report["status"] != "pending":
                return self.send_error_json(404, "Жалоба не найдена или уже обработана")
            conn.execute("UPDATE reports SET status = ? WHERE id = ?", (status, report_id))
            if hide_target and report["target_type"] in ("order", "service"):
                table = "orders" if report["target_type"] == "order" else "services"
                conn.execute(f"UPDATE {table} SET is_hidden = 1 WHERE id = ?", (report["target_id"],))
                self.log_admin_activity(conn, admin["id"], "hide_content", report["target_type"],
                                        report["target_id"], f"Скрыто из жалобы #{report_id}")
            self.log_admin_activity(conn, admin["id"], "resolve_report", "report", report_id,
                                    f"Жалоба #{report_id} -> {status}")
        self.send_json(200, {"ok": True})
