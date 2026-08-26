"""AI assistant API mixin: POST /api/ai/chat, GET /api/ai/history, DELETE /api/ai/history.

Two providers:
- LLM: any OpenAI-compatible chat completions endpoint (AI_API_KEY / AI_BASE_URL / AI_MODEL).
- Builtin offline engine (no key): rule-based answers grounded in the user's DB data.
"""
import json
import os
import urllib.error
import urllib.request

from db import connect, now
from common import check_rate_limit

HISTORY_LIMIT = 40
STORE_LIMIT = 60
REPLY_LIMIT = 30
MAX_MESSAGE_LEN = 4000
LLM_TIMEOUT = 45

PLATFORM_PROMPT = """Ты — встроенный AI-ассистент маркетплейса Meblio (mebl.io) — B2B-площадки,
где заказчики мебели размещают заказы, а мебельные производства откликаются.
Помогай кратко и по делу на русском языке: составление заказа, подбор материалов,
работа с откликами, счетами, доставкой, чатами и отзывами. Ты не юрист и не даёшь
юридических гарантий; при спорах советуй обратиться в поддержку платформы.
Не выдумывай функции, которых нет на платформе."""

ROLE_NAMES = {"client": "заказчик", "maker": "производитель", "admin": "администратор"}


def _ai_config():
    api_key = os.environ.get("AI_API_KEY", "").strip()
    base_url = (os.environ.get("AI_BASE_URL") or "https://api.openai.com/v1").strip().rstrip("/")
    model = os.environ.get("AI_MODEL", "").strip() or "gpt-4o-mini"
    return api_key, base_url, model


def ai_provider_name():
    return "llm" if _ai_config()[0] else "builtin"


def call_llm(messages):
    api_key, base_url, model = _ai_config()
    if not api_key:
        return None
    payload = json.dumps({"model": model, "messages": messages, "temperature": 0.4}).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"].strip() or None
    except Exception:
        return None


def user_context(conn, user):
    """Short factual summary of the user and their data for the system prompt."""
    lines = [f"Пользователь: {user['name']} ({ROLE_NAMES.get(user['role'], user['role'])})."]
    if user.get("city"):
        lines.append(f"Город: {user['city']}.")
    if user.get("company_type"):
        lines.append(f"Тип компании: {user['company_type']}.")
    orders = conn.execute(
        "SELECT title, status FROM orders WHERE client_id = ? ORDER BY id DESC LIMIT 5",
        (user["id"],),
    ).fetchall()
    if orders:
        listing = "; ".join(f"«{o[0]}» ({o[1]})" for o in orders)
        lines.append(f"Последние заказы клиента: {listing}.")
    else:
        lines.append("У пользователя пока нет заказов.")
    cats = conn.execute(
        "SELECT category, COUNT(*), ROUND(MIN(price_per_m2)), ROUND(MAX(price_per_m2)) "
        "FROM materials WHERE is_active = 1 GROUP BY category ORDER BY category"
    ).fetchall()
    if cats:
        parts = ", ".join(f"{c[0]}: {c[1]} шт. ({c[2]}–{c[3]} руб/м²)" for c in cats)
        lines.append(f"Каталог материалов: {parts}.")
    return "\n".join(lines)


def _offline_reply(conn, user, message):
    text = message.lower()

    def has_any(keys):
        return any(k in text for k in keys)

    if has_any(("статус", "мои заказы", "где мой заказ")):
        orders = conn.execute(
            "SELECT title, status, budget, city FROM orders WHERE client_id = ? ORDER BY id DESC LIMIT 5",
            (user["id"],),
        ).fetchall()
        if not orders:
            if user["role"] == "maker":
                return ("Вы производитель — статус заказов смотрите во вкладке «Отклики» кабинета. "
                        "Активные сделки видны в чатах с заказчиками.")
            return "У вас пока нет заказов. Разместите первый через «Кабинет → Заказы → Создать заказ»."
        rows = "\n".join(f"— «{t}»: {s}, бюджет {b} руб., {c}" for t, s, b, c in orders)
        return f"Ваши последние заказы:\n{rows}\n\nПодробности и таймлайн доставки — в карточке заказа."

    if has_any(("материал", "лдсп", "мдф", "фанер", "шпон", "столешниц")):
        cats = conn.execute(
            "SELECT category, COUNT(*) FROM materials WHERE is_active = 1 GROUP BY category ORDER BY category"
        ).fetchall()
        names = {"ldsp": "ЛДСП", "mdf": "МДФ", "other": "Другое"}
        listing = ", ".join(f"{names.get(c, c)} ({n})" for c, n in cats) or "каталог пуст"
        return (
            f"В каталоге материалов: {listing}. Для каждого материала указаны цена за м², толщина, цвет и бренд.\n"
            "Совет: для корпусов обычно берут ЛДСП 16–18 мм, для фасадов — МДФ с эмалью или шпон. "
            "Смотрите раздел «Материалы» и калькулятор стоимости в карточке материала."
        )

    if has_any(("поставщик", "фурнитур", "blum", "egger", "kronospan", "hettich")):
        sup = conn.execute("SELECT name, materials, city FROM suppliers WHERE is_active = 1 LIMIT 5").fetchall()
        listing = "; ".join(f"{n} ({m}, {c})" for n, m, c in sup) or "каталог пуст"
        return f"Каталог поставщиков: {listing}. Полный список с поиском — в разделе «Поставщики»."

    if has_any(("привет", "здравств", "добрый день", "добрый вечер", "доброе утро")):
        return (f"Здравствуйте, {user['name']}! Я ассистент Meblio. Могу помочь составить заказ, "
                "подобрать материал, рассказать про отклики, счета и доставку. Что подсказать?")

    if has_any(("отклик", "отозваться", "исполнител", "подрядчик")):
        if user["role"] == "maker":
            return ("Находите открытые заказы в разделе «Биржа», жмите «Откликнуться»: укажите цену, срок и сообщение. "
                    "После выбора вас исполнителем появится чат с заказчиком.")
        return ("Разместите заказ — производители пришлют отклики с ценой и сроком. "
                "Сравните их в карточке заказа (сортировка по цене/срокам) и выберите исполнителя кнопкой «Выбрать».")

    if has_any(("оплат", "счёт", "счет", "денег")):
        return ("Оплата проходит напрямую между сторонами: производитель выставляет счёт "
                "(Кабинет → Счета → Выставить счёт), статусы: ожидает → оплачен/отменён. "
                "Meblio не держит деньги на счету и не берёт комиссию со сделки.")

    if has_any(("доставк", "логистик")):
        return ("Статусы доставки: производство → готов → отгружен → доставка → доставлен. "
                "Их обновляет исполнитель кнопкой 🚚 в карточке заказа; вся история сохраняется в таймлайне.")

    if has_any(("безопасн", "мошенн", "обман", "надёжн", "надежн")):
        return ("Правила безопасности Meblio:\n"
                "— контакты открываются только после выбора исполнителя;\n"
                "— предоплату переводите только по реквизитам из счёта;\n"
                "— фиксируйте условия в чате платформы — история сохраняется;\n"
                "— жалоба на пользователя: кнопка «Пожаловаться», модерация проверит обращение.")

    if has_any(("отзыв", "рейтинг", "звёзд", "звезд")):
        return ("Отзывы можно оставить только участникам завершённой сделки (после «Завершить»). "
                "Оценка 1–5 звёзд + текст; рейтинг отображается в каталоге компаний. "
                "Производитель также может оценить заказчика.")

    if has_any(("создать заказ", "разместить", "новый заказ", "как заказать", "оформить заказ")):
        return ("Как разместить заказ:\n1. Кабинет → Заказы → «Создать заказ».\n"
                "2. Укажите тип изделия, количество, город, бюджет и дедлайн.\n"
                "3. Приложите чертежи или эскизы (до 25 МБ).\n"
                "4. Опубликуйте — производители увидят заявку в бирже и начнут откликаться.\n"
                "Можно сохранить черновик или шаблон для повторных заказов.")

    return ("Я могу помочь с:\n"
            "— созданием и оформлением заказа;\n"
            "— подбором материалов (ЛДСП, МДФ, фанера, столешницы);\n"
            "— откликами, выбором исполнителя и сделками;\n"
            "— счетами, доставкой и безопасностью платежей;\n"
            "— статусом ваших заказов.\n"
            "Спросите, например: «Как создать заказ?» или «Подбери материал для фасадов».")


class AiMixin:
    def api_ai_chat(self):
        data = self.read_json()
        message = (data.get("message") or "").strip()
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            ip = self.address_string()
            if not check_rate_limit(f"ai:{user['id']}", 20, 300):
                return self.send_error_json(429, "Слишком много запросов. Попробуйте позже.")
            if not message:
                return self.send_error_json(400, "Сообщение пустое")
            if len(message) > MAX_MESSAGE_LEN:
                return self.send_error_json(400, f"Сообщение слишком длинное (максимум {MAX_MESSAGE_LEN} символов)")

            conn.execute(
                "INSERT INTO ai_messages (user_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
                (user["id"], message, now()),
            )
            history = conn.execute(
                "SELECT role, content FROM ai_messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user["id"], HISTORY_LIMIT),
            ).fetchall()
            history = [{"role": role, "content": content} for role, content in reversed(history)]

            provider = ai_provider_name()
            reply = None
            if provider == "llm":
                context = user_context(conn, user)
                llm_messages = [
                    {"role": "system", "content": f"{PLATFORM_PROMPT}\n\nКонтекст пользователя:\n{context}"}
                ] + [{"role": r, "content": c} for r, c in history]
                reply = call_llm(llm_messages)
                if reply is None:
                    provider = "builtin-fallback"
            if reply is None:
                reply = _offline_reply(conn, user, message)

            conn.execute(
                "INSERT INTO ai_messages (user_id, role, content, created_at) VALUES (?, 'assistant', ?, ?)",
                (user["id"], reply, now()),
            )
            conn.execute(
                "DELETE FROM ai_messages WHERE user_id = ? AND id NOT IN "
                "(SELECT id FROM ai_messages WHERE user_id = ? ORDER BY id DESC LIMIT ?)",
                (user["id"], user["id"], STORE_LIMIT),
            )
        self.send_json(200, {"reply": reply, "provider": provider})

    def api_ai_history(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            rows = conn.execute(
                "SELECT role, content, created_at FROM ai_messages WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                (user["id"], REPLY_LIMIT),
            ).fetchall()
        messages = [{"role": r, "content": c, "created_at": ts} for r, c, ts in reversed(rows)]
        self.send_json(200, {"messages": messages, "provider": ai_provider_name()})

    def api_ai_clear(self):
        with connect() as conn:
            user = self.require_user(conn)
            if not user:
                return
            conn.execute("DELETE FROM ai_messages WHERE user_id = ?", (user["id"],))
        self.send_json(200, {"ok": True})
