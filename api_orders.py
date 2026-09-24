"""Order lifecycle API mixin: list/create, stages, accept, proposals, funnel, deadlines."""

import sqlite3
from urllib.parse import parse_qs

from db import connect, row_to_dict, rows_to_list, now, ensure_thread
from common import PAGE_SIZE, check_rate_limit, create_notification, store_upload, parse_deadline_days, csv_safe
from auth_util import create_pending_token  # noqa: F401  (kept for potential email flows)

class OrderMixin:
    DEFAULT_ORDER_STAGES = ("Замер", "Проект", "Производство", "Монтаж")

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

    def _order_participant_ids(self, conn, order):
        return {order["client_id"], order["selected_maker_id"] or -1}

    def _require_order_access(self, conn, order, user):
        """Owner client, selected maker, or admin."""
        if user["role"] == "admin":
            return True
        if order["client_id"] == user["id"]:
            return True
        if order["selected_maker_id"] and order["selected_maker_id"] == user["id"]:
            return True
        return False

    def _seed_order_stages(self, conn, order_id):
        count = conn.execute("SELECT COUNT(*) FROM order_stages WHERE order_id = ?", (order_id,)).fetchone()[0]
        if count:
            return
        for i, name in enumerate(self.DEFAULT_ORDER_STAGES):
            conn.execute(
                "INSERT INTO order_stages (order_id, name, position, done, created_at) VALUES (?, ?, ?, 0, ?)",
                (order_id, name, i, now()),
            )

    def api_order_stages(self, order_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not order:
                return self.send_error_json(404, "Заказ не найден")
            if not self._require_order_access(conn, order, user):
                return self.send_error_json(403, "Нет доступа к заказу")
            if order["status"] == "progress":
                self._seed_order_stages(conn, order_id)
            stages = rows_to_list(conn.execute(
                "SELECT order_stages.*, users.name AS done_by_name FROM order_stages "
                "LEFT JOIN users ON users.id = order_stages.done_by "
                "WHERE order_id = ? ORDER BY position, id",
                (order_id,),
            ).fetchall())
        self.send_json(200, {"stages": stages})

    def api_add_order_stage(self, order_id):
        data = self.read_json()
        name = (data.get("name") or "").strip()
        if not name or len(name) > 80:
            return self.send_error_json(400, "Название этапа обязательно (до 80 символов)")
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not order:
                return self.send_error_json(404, "Заказ не найден")
            if not self._require_order_access(conn, order, user):
                return self.send_error_json(403, "Нет доступа к заказу")
            if order["status"] != "progress":
                return self.send_error_json(400, "Этапы добавляются только для заказа в работе")
            self._seed_order_stages(conn, order_id)
            max_pos = conn.execute(
                "SELECT COALESCE(MAX(position), -1) FROM order_stages WHERE order_id = ?",
                (order_id,),
            ).fetchone()[0]
            cur = conn.execute(
                "INSERT INTO order_stages (order_id, name, position, done, created_at) VALUES (?, ?, ?, 0, ?)",
                (order_id, name, max_pos + 1, now()),
            )
            stage_id = cur.lastrowid
        self.send_json(200, {"ok": True, "id": stage_id})

    def api_update_order_stage(self, order_id, stage_id):
        data = self.read_json()
        deleted = False
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not order:
                return self.send_error_json(404, "Заказ не найден")
            if not self._require_order_access(conn, order, user):
                return self.send_error_json(403, "Нет доступа к заказу")
            if order["status"] != "progress":
                return self.send_error_json(400, "Этапы доступны только для заказа в работе")
            stage = conn.execute(
                "SELECT * FROM order_stages WHERE id = ? AND order_id = ?",
                (stage_id, order_id),
            ).fetchone()
            if not stage:
                return self.send_error_json(404, "Этап не найден")
            if data.get("delete") or data.get("action") == "delete":
                conn.execute("DELETE FROM order_stages WHERE id = ?", (stage_id,))
                deleted = True
            else:
                if "name" in data:
                    new_name = (data.get("name") or "").strip()
                    if not new_name or len(new_name) > 80:
                        return self.send_error_json(400, "Некорректное название этапа")
                    conn.execute("UPDATE order_stages SET name = ? WHERE id = ?", (new_name, stage_id))
                if "done" in data:
                    done = 1 if data.get("done") else 0
                    if "name" not in data:
                        new_name = stage["name"]
                    else:
                        new_name = (data.get("name") or stage["name"]).strip()[:80] or stage["name"]
                    conn.execute(
                        "UPDATE order_stages SET done = ?, done_by = ?, done_at = ?, name = ? WHERE id = ?",
                        (done, user["id"] if done else None, now() if done else None, new_name, stage_id),
                    )
                    self.log_order_change(
                        conn, order_id, user["id"], "stage",
                        stage["name"], f"{new_name}: {'done' if done else 'open'}",
                    )
        if deleted:
            return self.send_json(200, {"ok": True, "deleted": True})
        self.send_json(200, {"ok": True})

    def api_accept_order(self, order_id):
        """Client acceptance act: close when all stages done, or force with reason."""
        data = self.read_json() or {}
        force = bool(data.get("force"))
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not order:
                return self.send_error_json(404, "Заказ не найден")
            is_client = order["client_id"] == user["id"]
            is_admin = user["role"] == "admin"
            if not (is_client or is_admin):
                return self.send_error_json(403, "Принять работу может только заказчик")
            if order["status"] != "progress":
                return self.send_error_json(400, "Принять можно только заказ в работе")
            self._seed_order_stages(conn, order_id)
            stages = conn.execute(
                "SELECT * FROM order_stages WHERE order_id = ?", (order_id,)
            ).fetchall()
            pending = [s for s in stages if not s["done"]]
            if pending and not (force and (is_client or is_admin)):
                names = ", ".join(s["name"] for s in pending)
                return self.send_error_json(400, f"Не завершены этапы: {names}. Отметьте их или подтвердите приёмку без этапов.")
            conn.execute(
                "UPDATE orders SET status = 'closed', warranty_until = COALESCE(warranty_until, ?) WHERE id = ?",
                (self._warranty_date(14), order_id),
            )
            self.log_order_change(conn, order_id, user["id"], "status", "progress", "closed")
            if order["selected_maker_id"]:
                create_notification(
                    conn, order["selected_maker_id"], "order_status",
                    "Работа принята",
                    f"Заказ «{order['title']}» принят заказчиком. Можно оставить отзыв.",
                    f"/orders/{order_id}",
                )
        self.send_json(200, {"ok": True, "warranty_days": 14})

    @staticmethod
    def _warranty_date(days: int) -> str:
        import datetime
        return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()

    def api_estimate(self):
        data = self.read_json()
        try:
            width = float(data.get("width", 0))
            height = float(data.get("height", 0))
            depth = float(data.get("depth", 0))
            qty = int(data.get("qty", 1))
        except (TypeError, ValueError):
            return self.send_error_json(400, "Некорректные размеры")
        if width <= 0 or height <= 0 or depth <= 0 or qty < 1 or qty > 500:
            return self.send_error_json(400, "Размеры и количество должны быть положительными")
        if width > 10000 or height > 10000 or depth > 10000:
            return self.send_error_json(400, "Слишком большие размеры (макс. 10000 мм)")
        complexity = str(data.get("complexity", "medium"))
        mult = {"simple": 1.0, "medium": 1.25, "complex": 1.5}.get(complexity)
        if mult is None:
            return self.send_error_json(400, "Сложность: simple, medium или complex")
        hardware_level = str(data.get("hardware", "standard"))
        hardware_base = {"basic": 1500, "standard": 4000, "premium": 9000}.get(hardware_level)
        if hardware_base is None:
            return self.send_error_json(400, "Фурнитура: basic, standard или premium")
        material_price = 0.0
        material_name = (data.get("material_name") or "").strip()
        material_id = data.get("material_id")
        with connect() as conn:
            if material_id:
                mat = conn.execute(
                    "SELECT name, price_per_m2 FROM materials WHERE id = ? AND is_active = 1",
                    (int(material_id),),
                ).fetchone()
                if not mat:
                    return self.send_error_json(404, "Материал не найден")
                material_price = float(mat["price_per_m2"])
                material_name = mat["name"]
            else:
                try:
                    material_price = float(data.get("material_price", 0))
                except (TypeError, ValueError):
                    return self.send_error_json(400, "Некорректная цена материала")
                if material_price < 0:
                    return self.send_error_json(400, "Цена материала не может быть отрицательной")
                if not material_name:
                    material_name = "Свой материал"
        # mm → m; box-like panel area (sides + top/bottom + front/back)
        w, h, d = width / 1000.0, height / 1000.0, depth / 1000.0
        area = 2 * (w * h + w * d + h * d)
        material_cost = area * material_price * mult
        assembly_cost = material_cost * 0.35
        hardware_cost = hardware_base
        unit_total = material_cost + assembly_cost + hardware_cost
        total = unit_total * qty
        return self.send_json(200, {
            "area_m2": round(area, 3),
            "material_name": material_name,
            "material_price": round(material_price, 2),
            "complexity": complexity,
            "complexity_mult": mult,
            "hardware": hardware_level,
            "material_cost": round(material_cost, 2),
            "assembly_cost": round(assembly_cost, 2),
            "hardware_cost": hardware_cost,
            "unit_cost": round(unit_total, 2),
            "qty": qty,
            "total": round(total, 2),
            "warranty_days": 14,
        })

    def api_list_proposals(self, order_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not order:
                return self.send_error_json(404, "Заказ не найден")
            if user["id"] not in (order["client_id"], order["selected_maker_id"]) and user["role"] != "admin":
                has_response = conn.execute(
                    "SELECT 1 FROM responses WHERE order_id = ? AND maker_id = ? LIMIT 1",
                    (order_id, user["id"]),
                ).fetchone()
                if not has_response:
                    return self.send_error_json(403, "Нет доступа к заказу")
            proposals = rows_to_list(conn.execute(
                """SELECT proposals.*, users.name AS maker_name
                   FROM proposals JOIN users ON users.id = proposals.maker_id
                   WHERE proposals.order_id = ? ORDER BY proposals.created_at DESC, proposals.id DESC""",
                (order_id,),
            ).fetchall())
            import json as _json
            for p in proposals:
                try:
                    p["items"] = _json.loads(p.get("items") or "[]")
                except ValueError:
                    p["items"] = []
        self.send_json(200, {"proposals": proposals})

    def api_create_proposal(self, order_id):
        data = self.read_json()
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            if user["role"] != "maker":
                return self.send_error_json(403, "КП может создать только производитель")
            if not check_rate_limit(f"proposal:{user['id']}", 30, 3600):
                return self.send_error_json(429, "Слишком много КП. Попробуйте позже.")
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not order:
                return self.send_error_json(404, "Заказ не найден")
            if order["status"] not in ("open", "progress"):
                return self.send_error_json(409, "КП можно создать только для открытого или активного заказа")
            has_response = conn.execute(
                "SELECT 1 FROM responses WHERE order_id = ? AND maker_id = ? LIMIT 1",
                (order_id, user["id"]),
            ).fetchone()
            if not has_response and order["selected_maker_id"] != user["id"] and user["role"] != "admin":
                return self.send_error_json(403, "Сначала откликнитесь на заказ")
            try:
                amount = max(0, int(data.get("amount", 0)))
                days = max(0, int(data.get("days", 0)))
            except (TypeError, ValueError):
                return self.send_error_json(400, "Некорректная сумма или срок")
            message = str(data.get("message", "")).strip()[:2000]
            import json as _json
            raw_items = data.get("items") or []
            if isinstance(raw_items, str):
                try:
                    raw_items = _json.loads(raw_items)
                except ValueError:
                    raw_items = []
            items = []
            for it in raw_items[:50]:
                if not isinstance(it, dict):
                    continue
                name = str(it.get("name", "")).strip()[:200]
                if not name:
                    continue
                try:
                    qty = max(1, int(it.get("qty", 1)))
                    price = max(0, int(it.get("price", 0)))
                except (TypeError, ValueError):
                    qty, price = 1, 0
                items.append({"name": name, "qty": qty, "price": price})
            if amount <= 0 and items:
                amount = sum(i["qty"] * i["price"] for i in items)
            if amount <= 0:
                return self.send_error_json(400, "Укажите сумму КП или позиции")
            cur = conn.execute(
                "INSERT INTO proposals (order_id, maker_id, amount, days, message, items, status, created_at) VALUES (?, ?, ?, ?, ?, ?, 'sent', ?)",
                (order_id, user["id"], amount, days, message, _json.dumps(items, ensure_ascii=False), now()),
            )
            create_notification(conn, order["client_id"], "order_status",
                "Новое КП", f"{user['name']} отправил коммерческое предложение на заказ «{order['title']}»",
                f"/order/{order_id}")
            proposal_id = cur.lastrowid
        self.send_json(200, {"ok": True, "proposal_id": proposal_id, "amount": amount, "days": days, "items": items, "message": message, "status": "sent"})

    def api_update_proposal_status(self, proposal_id):
        data = self.read_json()
        status = str(data.get("status", "")).strip().lower()
        if status not in ("accepted", "rejected", "sent"):
            return self.send_error_json(400, "Статус: accepted, rejected или sent")
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            proposal = conn.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
            if not proposal:
                return self.send_error_json(404, "КП не найдено")
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (proposal["order_id"],)).fetchone()
            if not order:
                return self.send_error_json(404, "Заказ не найден")
            if status in ("accepted", "rejected"):
                if user["id"] != order["client_id"] and user["role"] != "admin":
                    return self.send_error_json(403, "Принять/отклонить КП может только заказчик")
                if status == "accepted":
                    conn.execute(
                        "UPDATE orders SET selected_maker_id = ?, status = CASE WHEN status = 'open' THEN 'progress' ELSE status END WHERE id = ?",
                        (proposal["maker_id"], proposal["order_id"]),
                    )
                    self._seed_order_stages(conn, proposal["order_id"])
                    thread_id = ensure_thread(conn, proposal["order_id"], order["client_id"], proposal["maker_id"])
                    conn.execute(
                        "INSERT INTO messages (thread_id, author_id, body, created_at) VALUES (?, ?, ?, ?)",
                        (thread_id, order["client_id"], f"Принято КП на сумму {proposal['amount']} ₽", now()),
                    )
                    create_notification(conn, proposal["maker_id"], "chosen",
                        "КП принято", f"Ваше КП по заказу «{order['title']}» принято",
                        f"/order/{proposal['order_id']}")
            else:
                if user["id"] != proposal["maker_id"] and user["role"] != "admin":
                    return self.send_error_json(403, "Изменить КП может только автор")
                if proposal["status"] not in ("sent", "rejected"):
                    return self.send_error_json(409, "Нельзя изменить принятое КП")
            conn.execute("UPDATE proposals SET status = ? WHERE id = ?", (status, proposal_id))
        self.send_json(200, {"ok": True, "status": status})

    def api_order_contract(self, order_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            order = conn.execute(
                """SELECT orders.*, clients.name AS client_name, clients.email AS client_email,
                          clients.phone AS client_phone, clients.inn AS client_inn, clients.ogrn AS client_ogrn,
                          clients.city AS client_city, clients.about AS client_about,
                          makers.name AS maker_name, makers.email AS maker_email, makers.phone AS maker_phone,
                          makers.inn AS maker_inn, makers.ogrn AS maker_ogrn, makers.city AS maker_city
                   FROM orders
                   JOIN users clients ON clients.id = orders.client_id
                   LEFT JOIN users makers ON makers.id = orders.selected_maker_id
                   WHERE orders.id = ?""",
                (order_id,),
            ).fetchone()
            if not order:
                return self.send_error_json(404, "Заказ не найден")
            if not self._require_order_access(conn, order, user):
                return self.send_error_json(403, "Нет доступа к договору")
            inv = conn.execute(
                "SELECT * FROM invoices WHERE order_id = ? ORDER BY created_at DESC LIMIT 1",
                (order_id,),
            ).fetchone()
            stages = rows_to_list(conn.execute(
                "SELECT name, position, done, done_at FROM order_stages WHERE order_id = ? ORDER BY position, id",
                (order_id,),
            ).fetchall())
        payload = dict(order)
        payload["stages"] = stages
        payload["invoice"] = dict(inv) if inv else None
        self.send_json(200, {"contract": payload})

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
                    INSERT INTO orders (client_id, title, type, quantity, city, budget, deadline, details, status, created_at, due_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{status}', ?, ?)
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
                        self._due_at(fields.get("deadline", "")),
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
                payload = {"order": self.order_payload(conn, order)}
            except Exception as exc:
                conn.rollback()
                return self.send_error_json(400, str(exc))
        self.send_json(200, payload)

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
                if (user.get("plan") or "free") == "free":
                    month_prefix = now()[:7] + "-01"
                    used = conn.execute(
                        "SELECT COUNT(*) FROM responses WHERE maker_id = ? AND created_at >= ?",
                        (user["id"], month_prefix),
                    ).fetchone()[0]
                    if used >= 5:
                        return self.send_error_json(403, "Лимит free-тарифа: 5 откликов в месяц. Перейдите на Pro на странице /tariffs")
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
            self._seed_order_stages(conn, order_id)
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
        data = self.read_json() or {}
        force = bool(data.get("force"))
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not order:
                return self.send_error_json(404, "Заказ не найден")
            is_client = order["client_id"] == user["id"]
            is_maker = order["selected_maker_id"] == user["id"]
            is_admin = user["role"] == "admin"
            if not (is_client or is_maker or is_admin):
                return self.send_error_json(403, "Завершить заказ могут только участники сделки")
            if order["status"] != "progress":
                return self.send_error_json(400, "Завершить можно только заказ в работе")
            self._seed_order_stages(conn, order_id)
            stages = conn.execute("SELECT * FROM order_stages WHERE order_id = ?", (order_id,)).fetchall()
            pending = [s for s in stages if not s["done"]]
            if pending and not force and not is_admin:
                names = ", ".join(s["name"] for s in pending)
                return self.send_error_json(400, f"Сначала завершите этапы: {names}. Либо примите работу с подтверждением.")
            conn.execute(
                "UPDATE orders SET status = 'closed', warranty_until = COALESCE(warranty_until, ?) WHERE id = ?",
                (self._warranty_date(14), order_id),
            )
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
            conn.execute("UPDATE orders SET status = 'open', due_at = COALESCE(due_at, ?) WHERE id = ?",
                         (self._due_at(order["deadline"]), order_id))
            self.log_order_change(conn, order_id, user["id"], "status", "draft", "open")
            makers = conn.execute("SELECT id FROM users WHERE role = 'maker'").fetchall()
            for m in makers:
                create_notification(conn, m["id"], "new_order",
                    "Новый заказ", f"{user['name']} опубликовал заказ: {order['title']}",
                    "/market")
        self.send_json(200, {"ok": True})

    @staticmethod
    def _due_at(deadline_str: str):
        days = parse_deadline_days(deadline_str or "")
        if days <= 0:
            return None
        import datetime
        return (datetime.date.today() + datetime.timedelta(days=days)).isoformat()

    def api_duplicate_order(self, order_id):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            if user["role"] != "client":
                return self.send_error_json(403, "Дублировать заказ может только заказчик")
            if not check_rate_limit(f"duplicate_order:{user['id']}", 20, 600):
                return self.send_error_json(429, "Слишком много дублей. Подождите немного.")
            src = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
            if not src:
                return self.send_error_json(404, "Заказ не найден")
            if src["client_id"] != user["id"] and user["role"] != "admin":
                return self.send_error_json(403, "Можно дублировать только свои заказы")
            if src["status"] == "draft":
                return self.send_error_json(400, "Черновик копировать не нужно — опубликуйте его")
            cur = conn.execute(
                """INSERT INTO orders (client_id, title, type, quantity, city, budget, deadline, details, status, created_at, due_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)""",
                (
                    user["id"],
                    f"{src['title']} (копия)",
                    src["type"],
                    src["quantity"],
                    src["city"],
                    src["budget"],
                    src["deadline"],
                    src["details"],
                    now(),
                    self._due_at(src["deadline"]),
                ),
            )
            new_id = cur.lastrowid
            for f in conn.execute(
                "SELECT original_name, stored_name, size, mime FROM order_files WHERE order_id = ?",
                (order_id,),
            ).fetchall():
                conn.execute(
                    "INSERT INTO order_files (order_id, original_name, stored_name, size, mime, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (new_id, f["original_name"], f["stored_name"], f["size"], f["mime"], now()),
                )
            order = conn.execute(
                "SELECT orders.*, users.name AS client_name, NULL AS selected_maker_name FROM orders JOIN users ON users.id = orders.client_id WHERE orders.id = ?",
                (new_id,),
            ).fetchone()
            payload = {"order": self.order_payload(conn, order)}
        self.send_json(200, payload)

    def api_maker_funnel(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            if user["role"] != "maker":
                return self.send_error_json(403, "Только для производителя")
            mid = user["id"]
            available = rows_to_list(conn.execute(
                """SELECT orders.id, orders.title, orders.budget, orders.city, orders.deadline, orders.created_at
                   FROM orders
                   WHERE orders.status = 'open' AND orders.is_hidden = 0
                     AND orders.id NOT IN (SELECT order_id FROM responses WHERE maker_id = ?)
                   ORDER BY orders.created_at DESC LIMIT 50""",
                (mid,),
            ).fetchall())
            sent = rows_to_list(conn.execute(
                """SELECT orders.id, orders.title, orders.budget, orders.city, orders.deadline,
                          orders.status, orders.selected_maker_id, responses.created_at AS responded_at
                   FROM responses JOIN orders ON orders.id = responses.order_id
                   WHERE responses.maker_id = ? AND orders.selected_maker_id IS NULL
                   ORDER BY responses.created_at DESC LIMIT 50""",
                (mid,),
            ).fetchall())
            chosen = rows_to_list(conn.execute(
                """SELECT orders.id, orders.title, orders.budget, orders.city, orders.deadline, orders.status, orders.warranty_until
                   FROM orders
                   WHERE orders.selected_maker_id = ? AND orders.status IN ('open', 'progress')
                   ORDER BY orders.created_at DESC LIMIT 50""",
                (mid,),
            ).fetchall())
            in_progress = rows_to_list(conn.execute(
                """SELECT orders.id, orders.title, orders.budget, orders.city, orders.deadline, orders.status, orders.warranty_until
                   FROM orders
                   WHERE orders.selected_maker_id = ? AND orders.status = 'progress'
                   ORDER BY orders.created_at DESC LIMIT 50""",
                (mid,),
            ).fetchall())
            closed = rows_to_list(conn.execute(
                """SELECT orders.id, orders.title, orders.budget, orders.city, orders.deadline, orders.status, orders.warranty_until
                   FROM orders
                   WHERE orders.selected_maker_id = ? AND orders.status = 'closed'
                   ORDER BY orders.created_at DESC LIMIT 50""",
                (mid,),
            ).fetchall())
            lost = rows_to_list(conn.execute(
                """SELECT orders.id, orders.title, orders.budget, orders.city, orders.deadline, orders.status
                   FROM responses JOIN orders ON orders.id = responses.order_id
                   WHERE responses.maker_id = ?
                     AND orders.selected_maker_id IS NOT NULL
                     AND orders.selected_maker_id != ?
                   ORDER BY responses.created_at DESC LIMIT 50""",
                (mid, mid),
            ).fetchall())
        self.send_json(200, {
            "stages": [
                {"id": "available", "label": "Доступные", "orders": available},
                {"id": "sent", "label": "Отправлен отклик", "orders": sent},
                {"id": "chosen", "label": "Выбраны", "orders": chosen},
                {"id": "in_progress", "label": "В работе", "orders": in_progress},
                {"id": "closed", "label": "Завершены", "orders": closed},
                {"id": "lost", "label": "Не выбрали", "orders": lost},
            ],
            "totals": {
                "available": len(available),
                "sent": len(sent),
                "chosen": len(chosen),
                "in_progress": len(in_progress),
                "closed": len(closed),
                "lost": len(lost),
            },
        })

    def api_check_deadlines(self):
        """Send deadline reminders; callable by scheduler and POST (admin)."""
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            if user["role"] != "admin":
                return self.send_error_json(403, "Только для администратора")
            sent = self.run_deadline_reminders(conn)
        self.send_json(200, {"ok": True, "sent": sent})

    @staticmethod
    def run_deadline_reminders(conn) -> int:
        import datetime
        today = datetime.date.today()
        sent = 0
        rows = conn.execute(
            """SELECT id, client_id, selected_maker_id, title, due_at, status, deadline_notified
               FROM orders
               WHERE status IN ('open', 'progress') AND due_at IS NOT NULL"""
        ).fetchall()
        for row in rows:
            try:
                due = datetime.date.fromisoformat(row["due_at"])
            except ValueError:
                continue
            days_left = (due - today).days
            stage = None
            if days_left < 0:
                stage = "overdue"
            elif days_left <= 1:
                stage = "1d"
            elif days_left <= 3:
                stage = "3d"
            if not stage:
                continue
            priority = {"3d": 1, "1d": 2, "overdue": 3}
            already = row["deadline_notified"] or ""
            if already and priority.get(already, 0) >= priority[stage]:
                continue
            if stage == "overdue":
                title = "Срок по заказу истёк"
                body = f"Заказ «{row['title']}»: срок {row['due_at']} уже прошёк."
            elif stage == "1d":
                title = "Дедлайн завтра"
                body = f"Заказ «{row['title']}»: срок сдачи {row['due_at']} (остался 1 день)."
            else:
                title = "Дедлайн через 3 дня"
                body = f"Заказ «{row['title']}»: срок сдачи {row['due_at']}."
            targets = [row["client_id"]]
            if row["selected_maker_id"]:
                targets.append(row["selected_maker_id"])
            for uid in targets:
                if uid:
                    create_notification(conn, uid, "order_status", title, body, f"/orders/{row['id']}")
            conn.execute(
                "UPDATE orders SET deadline_notified = ? WHERE id = ?",
                (stage, row["id"]),
            )
            sent += 1
        return sent

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

