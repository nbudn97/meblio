# Meblio — B2B-маркетплейс услуг производства мебели

Площадка для заказчиков мебели и мебельных производств: заказы, отклики, сделки,
чат с файлами, отзывы, аналитика. 

## Запуск

```bash
python app.py          # HTTP на :8000, WebSocket на :8001
```

Открыть http://127.0.0.1:8000 — база и папка загрузок создаются автоматически.

## Демо-аккаунты

| Роль | Логин | Пароль |
|---|---|---|
| Заказчик | `client@meblio.ru` | `client123` |
| Производитель | `maker@meblio.ru` | `maker123` |
| Производитель (СПб) | `linea@meblio.ru` | `linea123` |
| Администратор | `admin@meblio.ru` | `admin123` |

## Тесты

```bash
python -m unittest tests -v    # 35+ тестов, stdlib only
```

Тесты изолированы: используют временную БД (`MEBLIO_DB`) и не трогают боевую.

## Конфигурация

Скопируйте `.env.example` → `.env`. Основное:

- `PORT` / `WS_PORT` — порты сервисов
- `SMTP_*` — реальная отправка писем (без них — mock в лог)
- `MEBLIO_DEV=1` — возвращать ссылки верификации/сброса в ответах API

Подробности и деплой: [docs/DEPLOY.md](docs/DEPLOY.md).

## Структура

```
app.py           HTTP-сервер, роутинг, заказы, чат, аккаунты
api_admin.py     AdminMixin: статистика, модерация, жалобы, бэк-офис
api_catalog.py   CatalogMixin: материалы, шаблоны, счета, поставщики…
common.py        Общие хелперы (загрузки, rate limit, уведомления)
db.py            Схема SQLite, миграции, seed
ws_server.py     WebSocket (RFC 6455) для real-time чата
mailer.py        Email: SMTP или mock
logger.py        Ротация meblio.log
tests.py         Юнит-тесты (stdlib unittest)
script.js        SPA-фронтенд (vanilla JS)
styles.css       Стили, dark mode
sw.js / manifest.json   PWA
backup_db.py     Онлайн-бэкап SQLite
docs/DEPLOY.md   Инструкция деплоя (nginx, systemd, бэкапы)
```

## Безопасность

PBKDF2-пароли, CSRF-токены, TTL сессий 14 дней, 2FA (TOTP) + доверенные устройства,
whitelist + magic-bytes для загрузок, скрытие контактов до сделки, rate limiting,
жалобы и модерация контента. Подробности в `project.md`.
