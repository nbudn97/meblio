# Meblio — Площадка для заказа мебели

## Описание
B2B-маркетплейс для общения заказчиков мебели и мебельных производств. Аналог materix.ru, адаптированный под мебельную отрасль.

## Технологии
- **Бэкенд**: Python stdlib (`http.server`, `ThreadingHTTPServer`)
- **WebSocket**: Python stdlib (`socket`, `hashlib`, `struct`) — RFC 6455
- **БД**: SQLite3
- **Фронтенд**: Vanilla JS (SPA), CSS
- **Сервер**: `python app.py` на порту 8000, WebSocket на порту 8001

## Структура файлов
```
NIK2/
├── app.py          — HTTP-сервер, API, обработчики
├── ws_server.py    — WebSocket-сервер (real-time чат)
├── db.py           — БД, миграции, seed-данные, хеши паролей
├── script.js       — Фронтенд (SPA), все view и компоненты
├── styles.css      — Стили (редизайн от 21.06.2026)
├── index.html      — HTML-оболочка
├── meblio.png      — Логотип/фавикон
├── start.bat       — Запуск (python app.py)
├── meblio.db       — SQLite база (auto-generate)
├── uploads/        — Загруженные файлы
├── .gitignore      — Исключения git
└── project.md      — Этот файл
```

## База данных (28 таблиц)
| Таблица | Назначение |
|---------|-----------|
| `users` | Пользователи (role, company_type, region_id, logo, ...) |
| `sessions` | Cookie-сессии |
| `orders` | Заказы (title, type, budget, deadline, status, ...) |
| `order_files` | Файлы к заказам |
| `responses` | Отклики производителей (price, days, message) |
| `threads` | Треды чатов |
| `messages` | Сообщения |
| `regions` | 22 региона РФ |
| `services` | Услуги производителей |
| `service_files` | Файлы к услугам |
| `company_gallery` | Галерея работ компаний |
| `favorites` | Избранные компании |
| `notifications` | Уведомления (type, title, body, link, is_read) |
| `notification_preferences` | Настройки уведомлений пользователя (7 типов + push/email) |
| `reviews` | Отзывы (rating 1-5, text, reviewer_id, company_id, order_id) |
| `email_verifications` | Токены верификации email |
| `company_documents` | Документы компаний (сертификаты, лицензии, портфолио) |
| `csrf_tokens` | CSRF-токены (token, session_token, expires_at) |
| `admin_activity` | Журнал действий администратора (action, target, details) |
| `materials` | Каталог материалов (ЛДСП, МДФ, фанера, столешницы) |
| `order_templates` | Шаблоны заказов пользователя |
| `order_history` | История изменений заказа (audit trail) |
| `delivery_tracking` | Отслеживание доставки заказа |
| `invoices` | Счета на оплату |
| `tfa_secrets` | Секреты 2FA (TOTP) |
| `suppliers` | Каталог поставщиков материалов |
| `file_versions` | Версии файлов |
| `time_tracking` | Трекинг времени по заказам |
| `company_certificates` | Сертификаты компаний |
| `client_ratings` | Оценки заказчиков от производителей |
| `api_tokens` | JWT-токены для мобильного API |
| `ai_messages` | Диалог пользователя с AI-ассистентом |

## API Эндпоинты
| Метод | Путь | Описание |
|-------|------|----------|
| GET | `/api/session` | Текущий пользователь |
| POST | `/api/register` | Регистрация |
| POST | `/api/login` | Вход |
| POST | `/api/logout` | Выход |
| GET | `/api/orders` | Список заказов (фильтры: type, city, status, page) |
| POST | `/api/orders` | Создание заказа (multipart) |
| POST | `/api/orders/{id}/responses` | Отклик |
| POST | `/api/orders/{id}/choose` | Выбор исполнителя |
| GET | `/api/companies` | Каталог компаний (фильтры: type, region, search, page) |
| GET | `/api/companies/{id}` | Страница компании |
| GET | `/api/services` | Список услуг (фильтр: user_id) |
| POST | `/api/services` | Создание услуги |
| PUT | `/api/services/{id}` | Редактирование услуги |
| DELETE | `/api/services/{id}` | Удаление услуги |
| GET | `/api/regions` | Список регионов |
| GET | `/api/company-types` | Типы компаний |
| GET | `/api/makers` | Список производителей |
| GET | `/api/threads` | Треды чатов |
| GET | `/api/threads/{id}/messages` | Сообщения треда |
| POST | `/api/threads/{id}/messages` | Отправка сообщения |
| POST | `/api/profile` | Обновление профиля |
| POST | `/api/companies/logo` | Загрузка логотипа |
| GET | `/api/favorites` | Список избранного |
| POST | `/api/favorites` | Добавить в избранное |
| DELETE | `/api/favorites/{id}` | Удалить из избранного |
| GET | `/api/admin/stats` | Статистика (только admin) |
| GET | `/api/admin/analytics` | Аналитика (типы, статусы, топ, выручка) |
| GET | `/api/admin/activity` | Журнал действий администратора |
| GET | `/api/admin/users` | Список пользователей (фильтры: role, search) |
| POST | `/api/admin/users` | Создание пользователя администратором |
| GET | `/api/admin/users/{id}` | Детали пользователя |
| PUT | `/api/admin/users/{id}` | Редактирование пользователя |
| DELETE | `/api/admin/users/{id}` | Удаление пользователя |
| GET | `/api/admin/orders` | Список заказов (фильтры: status, search) |
| GET | `/api/admin/orders/{id}` | Детали заказа |
| POST | `/api/admin/orders/status` | Изменение статуса заказа |
| DELETE | `/api/admin/orders/{id}` | Удаление заказа |
| GET | `/api/admin/services` | Список услуг (фильтр: search) |
| GET | `/api/admin/services/{id}` | Детали услуги |
| DELETE | `/api/admin/services/{id}` | Удаление услуги |
| GET | `/api/notifications` | Уведомления (с непрочитанными) |
| POST | `/api/notifications/{id}` | Отметить как прочитанное |
| POST | `/api/notifications/read-all` | Отметить все как прочитанные |
| DELETE | `/api/notifications/{id}` | Удалить уведомление |
| DELETE | `/api/notifications/clear-all` | Удалить все уведомления |
| GET | `/api/notifications/preferences` | Получить настройки уведомлений |
| POST | `/api/notifications/preferences` | Обновить настройки уведомлений |
| GET | `/api/reviews?company_id=N` | Отзывы компании |
| POST | `/api/reviews` | Создать отзыв (rating 1-5, text, company_id) |
| GET | `/api/search?q=text` | Глобальный поиск (заказы, компании, услуги) |
| GET | `/api/documents` | Документы текущей компании |
| POST | `/api/documents` | Загрузить документ (multipart, doc_type) |
| DELETE | `/api/documents/{id}` | Удалить документ |
| POST | `/api/csrf-token` | Получить CSRF-токен |
| GET | `/api/materials` | Каталог материалов (фильтр: category) |
| GET | `/api/materials/{id}` | Детали материала |
| POST | `/api/materials` | Создать материал (admin) |
| PUT | `/api/materials/{id}` | Обновить материал (admin) |
| DELETE | `/api/materials/{id}` | Удалить материал (admin) |
| GET | `/api/templates` | Шаблоны заказов текущего пользователя |
| POST | `/api/templates` | Создать шаблон |
| PUT | `/api/templates/{id}` | Обновить шаблон |
| DELETE | `/api/templates/{id}` | Удалить шаблон |
| GET | `/api/invoices` | Счета пользователя |
| GET | `/api/invoices/{id}` | Детали счёта |
| POST | `/api/invoices` | Выставить счёт |
| GET | `/api/delivery?order_id=N` | История доставки заказа |
| POST | `/api/delivery` | Добавить статус доставки |
| GET | `/api/order-history?order_id=N` | История изменений заказа |
| POST | `/api/admin/bulk-orders` | Массовые операции с заказами |
| POST | `/api/admin/bulk-users` | Массовые операции с пользователями |
| GET | `/api/tfa/status` | Статус 2FA |
| POST | `/api/tfa/setup` | Настроить 2FA (секрет) |
| POST | `/api/tfa/verify` | Верифицировать код 2FA |
| GET | `/api/suppliers` | Каталог поставщиков (поиск) |
| GET | `/api/suppliers/{id}` | Детали поставщика |
| POST | `/api/suppliers` | Создать поставщика (admin) |
| PUT | `/api/suppliers/{id}` | Обновить поставщика (admin) |
| DELETE | `/api/suppliers/{id}` | Удалить поставщика (admin) |
| GET | `/api/certificates` | Сертификаты компании |
| GET | `/api/certificates/{id}` | Детали сертификата |
| POST | `/api/certificates` | Добавить сертификат (multipart) |
| PUT | `/api/certificates/{id}` | Обновить сертификат |
| DELETE | `/api/certificates/{id}` | Удалить сертификат |
| GET | `/api/time-entries` | Записи времени |
| POST | `/api/time-entries` | Добавить запись времени |
| DELETE | `/api/time-entries/{id}` | Удалить запись времени |
| GET | `/api/client-ratings?client_id=N` | Оценки заказчика |
| POST | `/api/client-ratings` | Оценить заказчика (maker only) |
| GET | `/api/export/excel` | Экспорт заказов в CSV/Excel |
| POST | `/api/auth/token` | Получить JWT-токен для мобильного API |
| POST | `/api/ai/chat` | Сообщение AI-ассистенту (ответ + провайдер) |
| GET | `/api/ai/history` | История диалога с ассистентом (последние 30) |
| DELETE | `/api/ai/history` | Очистить диалог ассистента |

## Типы компаний
- `client` — Заказчик
- `designer` — Проектировщик
- `manufacturer` — Производитель
- `serial` — Серийное производство
- `supplier` — Поставщик

## Роли
- `client` — Заказчик (создаёт заказы)
- `maker` — Производитель (откликается, добавляет услуги)
- `admin` — Администратор

## Страницы фронтенда
- **Главная** — герой, статистика, CTA
- **Заказы** — список с фильтрами, таймеры дедлайнов
- **Компании** — каталог с фильтрами по типу/региону/поиску
- **Страница компании** — описание, услуги, галерея, статистика
- **Кабинет** — обзор, заказы, отклики, услуги, чат, профиль
- **Чат** — polling каждые 5 сек

## Демо-данные
- `client@meblio.ru` / `client123` — Анна Орлова (заказчик)
- `maker@meblio.ru` / `maker123` — Modul Pro (производитель, Москва)
- `linea@meblio.ru` / `linea123` — Linea Wood (производитель, СПб)
- `admin@meblio.ru` / `admin123` — Администратор Meblio

## История изменений

### 21.06.2026 — Начальная версия + улучшения
- Проанализирован код, найдены проблемы
- Добавлен `.gitignore`
- Исправлен путь к Python в `start.bat`
- Добавлен debounce на фильтр города
- Заменены `alert()` на toast-уведомления
- Разделение `app.py` → `db.py` + `app.py`
- Добавлена пагинация API
- Добавлен `Secure` flag на cookie
- Исправлен XSS в `escapeHtml`

### 21.06.2026 — Автообновление чата
- Polling каждые 5 секунд
- Обновление только `.messages` div без re-render
- Auto-scroll при новых сообщениях

### 21.06.2026 — Фичи из Materix
- Типы компаний (5 типов)
- 22 региона РФ
- Каталог компаний с фильтрами
- Страница компании (услуги, галерея, статистика)
- CRUD услуг
- Таймер обратного отсчёта до дедлайна

### 21.06.2026 — Редизайн + UX
- Новая цветовая палитра (синий #3b82f6)
- Обновлённые тени, скругления, шрифты
- Анимации (fadeIn, slideUp, hover-эффекты)
- Skeleton-скриншоты при загрузке
- Улучшенные пустые состояния с CTA
- Мобильная адаптация

### 21.06.2026 — Dark mode + Rate limiting
- Dark mode с переключателем в хедере
- Сохранение темы в localStorage
- Rate limiting на login/register (5 попыток / 5 мин)
- Кнопка переключения темы (солнце/луна)

### 21.06.2026 — Избранные компании
- Таблица `favorites` (user_id, company_id)
- API: GET/POST/DELETE /api/favorites
- Кнопка ♥ на карточках компаний
- Вкладка «Избранные» в кабинете
- Добавление/удаление из избранного без перезагрузки

### 22.06.2026 — Админ-панель
- Demo-админ: `admin@meblio.ru` / `admin123`
- Обзор: статистика по пользователям, заказам, услугам, сообщениям
- Управление пользователями: список, редактирование роли/имени, удаление
- Управление заказами: список, изменение статуса, удаление
- Управление услугами: список, удаление
- Таблицы с сортировкой и actions-кнопками

### 05.07.2026 — WebSocket для real-time чата
- Новый файл `ws_server.py` — WebSocket-сервер на Python stdlib (RFC 6455)
- Аутентификация по сессионной cookie
- Подписка/отписка на треды
- Heartbeat (ping/pong каждые 30 сек)
- Автоматическое переподключение при обрыве
- Оптимистичное обновление UI при отправке сообщений
- Убран polling каждые 5 сек

### 08.07.2026 — Система уведомлений v2
- Таблица `notification_preferences` (7 типов + push/email каналы)
- Реалтайм доставка уведомлений через WebSocket (`send_to_user`)
- Фильтрация уведомлений по типам (вкладка, unread, все типы)
- Настройки уведомлений: переключатели для каждого типа + каналы
- Удаление уведомлений: по одному или очистить все
- Push-уведомления в браузере при реалтайм доставке
- Email-уведомления: mock (лог в консоль, ready for SMTP)
- Уведомления создаются с учётом предпочтений пользователя
- API: GET/POST/DELETE для уведомлений, preferences CRUD

### 08.07.2026 — Массовое добавление фич v2 (8 шт)
- **PWA**: service worker (cache-first для статики, network-first для API), manifest.json, push-уведомления, офлайн-режим
- **Экспорт в Excel**: кнопка в кабинете, генерация CSV с BOM для Excel, скачивание файла
- **Двусторонний рейтинг**: производитель оценивает заказчика (1-5 звёзд, комментарий), уведомление
- **REST API с JWT**: эндпоинт `/api/auth/token` для получения токена, `api_tokens` таблица
- **Каталог поставщиков**: 6seed-поставщиков (Egger, Kronospan, Sonae, Blum, Hettich), поиск по названию/материалам/городу
- **Версионирование файлов**: таблица `file_versions` для хранения предыдущих версий
- **Трекинг времени**: учёт времени по заказам (задача, часы, дата, заметки), суммарная статистика
- **Сертификаты компаний**: загрузка/удаление сертификатов (качество, безопасность, ISO), хранение файлов
- Seed: 6 поставщиков, 14 материалов

### 08.07.2026 — Массовое добавление фич (8 шт)
- **Каталог материалов**: 14 позиций (ЛДСП Egger, МДФ эмаль, шпон, фанера, столешницы), CRUD, фильтр по категориям
- **Шаблоны заказов**: сохранение шаблонов, один клик для заполнения формы заказа, CRUD
- **Калькулятор стоимости**: автоматический расчёт по материалам и размерам (встроен в каталог)
- **Счета-фактуры**: генерация счёта между заказчиком и производителем, статусы (pending/paid/cancelled), печать
- **Отслеживание доставки**: timeline с 5 статусами (производство → готов → отгружен → доставка → доставлен)
- **История изменений заказа**: audit trail кто что изменил (статус, исполнитель, бюджет и т.д.)
- **Массовые операции**: bulk delete/status для заказов и users (админ)
- **2FA (двухфакторка)**: TOTP-аутентификация через Google Authenticator, настройка/верификация
- Новые таблицы: materials, order_templates, order_history, delivery_tracking, invoices, tfa_secrets
- Seed: 14 материалов (ЛДСП Egger 5 цветов, МДФ эмаль 2, шпон 2, фанера 2, ДСП, столешницы 2)
- Кнопки 🚚 и 📋 на карточках заказов для доставки и истории

### 08.07.2026 — Расширенная аналитика админа
- KPI-карточки: конверсия, завершаемость, откликов/заказ, сообщений/чат
- Графики: заказы по типам/статусам/городам/регионам, типы производителей
- Активность по часам и дням недели (столбчатые диаграммы)
- Рост пользователей и заказов (SVG линейные графики)
- Топ производителей и заказчиков
- Средний бюджет по типам заказов
- Выручка по месяцам (таблица + экспортируемая)
- Экспорт аналитики в CSV
- Сводка платформы (услуги, чаты, сообщения)
- API: `GET /api/admin/analytics` расширен (by_city, by_region, by_hour, by_dow, growth, completion_rate, conversion_rate)

### 08.07.2026 — Улучшенная админ-панель
- KPI-карточки (пользователи, заказы, бюджет, рейтинг) с визуальными барами
- Вкладка «Аналитика»: заказы по типам/статусам, топ производителей, выручка по месяцам
- Вкладка «Журнал»: лог действий администратора (создание/редактирование/удаление)
- Поиск и фильтры в таблицах пользователей (роль, поиск) и заказов (статус, поиск)
- Модальное окно создания пользователя (роль, email, пароль, тип компании)
- Детальный просмотр пользователя (статистика, компетенции, описание)
- Детальный просмотр заказа (файлы, отклики, описание)
- Activity logging: каждое действие админа записывается в `admin_activity`
- API: `GET /api/admin/analytics`, `GET /api/admin/activity`, `POST /api/admin/users`

### 08.07.2026 — Массовое обновление фич
- **CSRF-защита**: Генерация токенов, валидация на сервере, эндпоинт `/api/csrf-token`
- **Email-верификация**: Таблица `email_verifications`, токены с expiration
- **Push-уведомления**: Браузерные Notification API, запрос разрешения, push при новом сообщении
- **Уведомления в приложении**: Таблица `notifications`, API (список, прочитать, все прочитанные), UI (колокольчик, панель, вкладка в кабинете)
- **Экспорт заказов**: Кнопка 📥 на карточках заказов, экспорт в HTML/PDF через `window.print()`
- **Отзывы и рейтинги**: Таблица `reviews`, API (создание, список), UI (звезды, форма отзыва, отображение на профиле компании)
- **Расширенный поиск заказов**: Фильтр по статусу, бюджету (от/до), типу, городу
- **Загрузка документов**: Таблица `company_documents`, API (загрузка, удаление, список), UI в профиле компании
- **Глобальный поиск**: API `/api/search` (заказы, компании, услуги), поисковая строка в хедере, выпадающий результат, поиск на главной
- Уведомления автоматически создаются при: создании заказа, отклике, выборе исполнителя, новом сообщении
- Рейтинги отображаются на карточках компаний и в каталоге
- Добавлены таблицы: notifications, reviews, email_verifications, company_documents, csrf_tokens

### 24.08.2026 — Аудит безопасности и доработки
- **WebSocket**: проверка участия в треде перед подпиской (раньше любой юзер мог читать чужие чаты), ошибка `subscribe_error`
- **CSRF**: реальное включение — заголовок `X-CSRF-Token` обязателен для POST/PUT/DELETE (кроме login/register/csrf-token), токен многоразовый до истечения (2 ч), фронтенд шлёт токен в `api()`, единое локальное время вместо utcnow
- **Загрузки**: whitelist расширений (png/jpg/webp/gif/pdf/txt/csv/xlsx/docx/zip/dwg/dxf), SVG и HTML запрещены; отдача с `X-Content-Type-Options: nosniff`, не-медиа файлы — `attachment`
- **Сессии**: TTL 14 дней (`SESSION_TTL_DAYS`), чистка протухших при логине/регистрации/init_db и в WS-аутентификации
- **CSV-экспорт**: экранирование formula injection (`=+-@`, таб, CR → префикс `'`)
- **Rate limiting**: периодическая чистка словаря (устранение утечки памяти)
- **Git**: удалены cookies.txt/cookies2.txt с живыми токенами, добавлены в `.gitignore`
- **Seed**: исправлено имя материала «ЛДСП Egger白» → «ЛДСП Egger белый» (+ патч существующей БД)
- manifest.json/sw.js проверены — на диске корректный UTF-8

### 24.08.2026 — Архитектура и тесты
- **Разбиение app.py** (~2450 строк): вынесены `api_admin.py` (AdminMixin, 18 методов) и `api_catalog.py` (CatalogMixin, 32 метода), общие хелперы — в `common.py`; в app.py осталось ~1460 строк (роутинг, аутентификация, заказы, чат)
- **N+1**: `api_orders` и `api_companies` теперь батч-загружают файлы/отклики и рейтинги одним запросом вместо запроса в цикле
- **Тесты**: `tests.py` на чистом stdlib (unittest) — 12 тестов: регистрация/вход, CSRF, полный жизненный цикл заказа, whitelist загрузок, TTL сессий, авторизация WS-подписок, админка, CSV-экспорт. Запуск: `python -m unittest tests -v`
- **Изоляция БД**: `MEBLIO_DB` / `MEBLIO_UPLOADS` env-переменные для тестовой БД и папки загрузок
- **Unicode-поиск**: SQLite `LOWER()`/`UPPER()` ASCII-only → переопределены на Unicode (кириллические фильтры по городу/имени работали впервые; раньше `%москва%` не находил `Москва`)
- **Багфиксы**: `/api/companies` — ambiguous column `name` (каталог был сломан с первой версии); `api_tfa_verify` — отсутствующий `import base64` (NameError при проверке кода 2FA)

### 24.08.2026 — Надёжность (фаза 0)
- **SQLite WAL + busy_timeout**; 11 индексов (messages.thread_id, notifications(user_id,is_read), orders(status,created_at), orders.city и др.)
- **connect() → context manager**: коммит/откат/закрытие соединения (устранена утечка соединений на каждый запрос)
- **500 → JSON**: все хендлеры обёрнуты, traceback в лог
- **Логгер**: `logger.py` — ротация `meblio.log` (5 МБ × 3), логирование ошибок HTTP/WS
- **Security-заголовки** на статике: CSP, X-Frame-Options: DENY, Referrer-Policy, nosniff
- **Cookie Max-Age=7 дней**; **`/healthz`** без авторизации
- **Загрузки**: magic-bytes валидация (png/jpg/gif/pdf/webp/zip/docx/xlsx), хранение в `uploads/YYYY/MM`
- **Rate limiting** на создание заказов/откликов/сообщений/отзывов/поиск
- **Пагинация** с tie-breaker `id DESC`

### 24.08.2026 — Аутентификация (фаза 1)
- **`mailer.py`**: SMTP при `SMTP_HOST` + fallback mock-письма в лог; `MEBLIO_DEV=1` возвращает ссылку в ответе API
- **Email-верификация**: `users.is_verified`, токен при регистрации, `GET /api/verify-email`, повторная отправка `/api/resend-verification`, баннер в UI
- **2FA при входе**: двухшаговый логин (`pending_tfa`, `POST /api/tfa/login`), поле кода в модалке
- **Восстановление пароля**: `/api/forgot-password` + `/api/reset-password` (страница `/reset-password?token=`)
- **Смена пароля**: `/api/change-password` (проверка старого, инвалидация всех сессий)

### 24.08.2026 — Сделки и модерация (фаза 2)
- **Отзывы только участникам** завершённых сделок (клиент→мастер и мастер→клиент)
- **Отмена заказа**: статус `cancelled`, `/api/orders/{id}/cancel`, история + уведомление исполнителя
- **Жалобы**: таблица `reports`, `/api/reports`, админ: список `/api/admin/reports` + `/resolve`
- **Контакты скрыты до сделки**: в каталоге/карточке email/phone пустые; открываются участникам треда/сделки и админам

### 24.08.2026 — Чат и портал мастера (фазы 3–4)
- **Файлы в чате**: `message_files`, `/api/threads/{id}/files` (multipart), WS-broadcast, кнопка прикрепления
- **Аналитика мастера**: `/api/maker/stats` (конверсия, выручка, рейтинг, часы, по месяцам), вкладка «Аналитика»
- **Портфолио**: `/api/gallery` CRUD (загрузка/удаление работ), блок в профиле
- **Сравнение откликов**: сортировка по цене/срокам в карточке заказа

### 24.08.2026 — Ops (фаза 5)
- **CI**: `.github/workflows/test.yml` (Python 3.11–3.13, `python -m unittest tests`)
- **Бэкапы**: `backup_db.py` (online backup API SQLite → `backups/`)
- **SW-кэш**: bumped до v3

### 24.08.2026 — Продуктовая целостность и запуск (волна 3)
- **Закрытие заказа участниками**: `POST /api/orders/{id}/close` (клиент или выбранный исполнитель, `progress → closed`) + кнопка «Завершить» — больше не нужен админ для завершения сделки
- **Фильтр бюджета**: `budget_min/budget_max` на сервере (раньше фильтровалась только первая страница клиентски)
- **Trusted devices 2FA**: HMAC-cookie `meblio_device` на 30 дней, повторный вход без кода; секрет в `app_config`
- **Модерация контента**: `is_hidden` у orders/services, `POST /api/admin/hide`, авто-скрытие при resolve жалобы (`hide_target`), владелец видит свои скрытые услуги
- **Аккаунт-безопасность**: honeypot-антиспам в регистрации, смена email с повторной верификацией, самоудаление аккаунта (обезличивание)
- **Инфраструктура**: загрузка `.env`, `.env.example`, ETag/Last-Modified/304 + Cache-Control на статике, лог медленных запросов (>1 c), `docs/DEPLOY.md` (nginx+TLS, systemd/schtasks, бэкапы)
- **Полировка**: README, обогащённый seed (закрытая сделка + отзывы + галерея + счёт), title per view, SW v4

### 24.08.2026 — Структура и SEO по образцу Materix (волна 4)
Проанализирована структура materix.ru (843 URL: 381 компаний, 158 услуг, фильтр-лендинги type×region, статьи) и реализован паритет:

- **History API роутинг**: рабочие URL `/`, `/market`, `/companies/{id}`, `/services/{id}`, `/articles/{slug}`, `/chat`, `/dashboard/…` — back/forward, прямые ссылки, ссылки из уведомлений работают
- **SPA-fallback на сервере**: любой путь отдаёт index.html с инъекцией меты
- **Динамическое SEO**: title/description/canonical/OG + JSON-LD (Organization для компании, Service для услуги) генерируются из БД
- **robots.txt** (закрыты /api, кабинет, admin) и **sitemap.xml** из БД: компании, услуги, статьи + фильтр-комбинации `?type=×?region=` как у Materix
- **Публичный каталог услуг**: `/services` (листинг) и `/services/{id}` (страница с CTA на компанию)
- **Статьи**: таблица articles, публичный листинг/страница (`/articles/{slug}`), админ-CRUD, мини-markdown рендер, 3 seed-статьи
- **Слаги регионов**: все субъекты РФ (~85), транслитерация, фильтр `/api/companies?region={slug}`
- **Черновики заказов**: статус `draft` (виден только владельцу) + публикация — по образцу «Скрыт» у Materix
- **CTA «Запросить расчёт»**: клиент приглашает компанию к своему открытому заказу (тред + уведомление)
- Хлебные крошки на карточках; SW v5

### 24.08.2026 — Редизайн: скандинавский минимализм
- Палитра: белый фон, холодный серый, синий акцент #2563eb; терракота/крем удалены
- Заголовки — Inter 800 с плотным трекингом (Lora убран из Google Fonts)
- Hero: плоский белый, воздух 96px; кнопка primary плоская синяя; логотип SVG в синей гамме
- Dark mode: холодная slate-гамма (#0b0f17/#151a23)
- Бургер-меню, футер и структура компонентов сохранены; SW v7

### 24.08.2026 — Wordmark-логотип mebl.io
- Хедер: текстовый знак «mebl.io» (Comfortaa), точка домена — синий акцент; SVG-знак убран
- Favicon/PWA: синяя плитка с белой строчной «m» (первая буква бренда), сгенерировано tools/gen_favicon.py (64 + 512, any+maskable)
- Старый meblio.png 744 КБ заменён на 293-байтный; SW v10

### 26.08.2026 — AI-ассистент (чат-виджет)
- **`api_ai.py`**: `AiMixin` — POST /api/ai/chat, GET /api/ai/history, DELETE /api/ai/history; таблица `ai_messages` (диалог на пользователя, хвост 60 сообщений), rate limit 20 запросов / 5 мин
- **Два провайдера**: LLM через любой OpenAI-compatible API (`AI_API_KEY` / `AI_BASE_URL` / `AI_MODEL` в .env, urllib без зависимостей) и встроенный офлайн-движок без ключа — rule-based ответы по интентам (заказы, материалы, поставщики, отклики, счета, доставка, безопасность, отзывы) на данных из БД пользователя
- **Заземление контекста**: в system prompt для LLM передаются роль/город пользователя, его последние заказы и сводка каталога материалов; при сбое LLM — автоматический fallback на офлайн-движок (`provider: builtin-fallback`)
- **Фронтенд**: плавающая кнопка-пузырь (FAB) + панель диалога: история при открытии, быстрые подсказки-chips, индикатор «Печатает…», очистка диалога, CTA «Войти» для анонимов; тёмная тема, мобильная адаптация
- Тесты: `AiChatTests` (4 шт. — история/интенты/валидация/очистка); SW v11
- **Багфикс гонки**: `api_create_order` отправлял ответ клиенту до коммита транзакции (ответ внутри `with connect()`) — следующий запрос клиента мог не увидеть только что созданный заказ (флакующий 404 в `test_invite_to_quote`); теперь ответ шлётся после коммита, при ошибке — явный `rollback`

### 26.08.2026 — Восстановлена иллюстрация на главной
- В hero-карточке главной страницы вместо растянутой плитки-фавикона снова фотография производства мебели (извлечена из git-истории, коммит до замены на wordmark)
- Файл сохранён как `hero-workshop.png` (744 КБ, оригинальное качество); `meblio.png` не тронут — остаётся фавиконом/PWA-иконкой и логотипом
- `STATIC_FILES` в app.py дополнен `/hero-workshop.png`; SW v12 (обновлённый script.js)
- **404 для отсутствующей статики**: неизвестные пути с расширением файла (`.png`, `.css`, …) больше не «проглатываются» SPA-fallback — отдаётся честный 404 (раньше отсутствующий файл молча возвращал index.html, и поломка выглядела как «картинка не отображается»); тест `test_missing_asset_404_but_spa_routes_work`
- **Багфикс: SW/manifest никогда не обслуживались**: `/sw.js` и `/manifest.json` отсутствовали в `STATIC_FILES` — запросы возвращали HTML через SPA-fallback, `serviceWorker.register()` молча падал (`.catch(()=>{})`), PWA-кэширование не работало с самого внедрения; оба файла добавлены в `STATIC_FILES` (проверены MIME: `text/javascript`, `application/json`)
- **Кэш-бастинг hero-изображения**: `src="/hero-workshop.png?v=2"` — обход HTTP-кэша браузера, в котором застрял HTML-ответ старого сервера для этого URL; SW bump v13 (чистый кэш при первой реальной установке)
- **Версионирование ассетов**: `styles.css?v=13` и `script.js?v=13` в index.html — обход `Cache-Control: max-age=3600` у статики (браузер мог час не запрашивать обновлённый script.js); SW v14: прекэш версионных URL через `new Request(url, {cache: "reload"})` — установка SW всегда берёт файлы с сервера, а не из HTTP-кэша браузера
- **Hero-изображение встроено в script.js** (data URI): после упорных проблем с кэшами на клиенте фото производства конвертировано в JPEG q82 (761 КБ → 81 КБ, Pillow) и вшито как `const HERO_IMG = "data:image/jpeg;base64,…"` — отдельный сетевой запрос за картинкой исключён, CSP `img-src data:` разрешает; `/hero-workshop.png` остался на диске без использования; SW v15, `script.js?v=14`

### 26.08.2026 — Реквизиты компании
- Поля профиля для всех ролей (включая заказчиков): **ИНН** (10/12 цифр), **ОГРН** (13/15 цифр), **веб-сайт** (авто-`https://`, валидация http(s)), чекбокс **«Публичный профиль»** (`users.is_public`)
- Скрытые профили: исключены из каталога `/api/companies`, списка мастеров, глобального поиска и sitemap; детальная карточка отдаёт 404 не-владельцам (владелец и админ видят, на фронте бейдж «Профиль скрыт из каталога»)
- Карточка компании: блок **«Реквизиты»** (ИНН, ОГРН, регион, веб-сайт-ссылка); компетенции (`skills`) остаются чипсами в «О компании»
- Миграция `ensure_column` (inn/ogrn/website/is_public, DEFAULT 1 — существующие профили публичны); тесты `CompanyRequisitesTests` (3 шт.: сохранение/отдача, валидация, скрытие); SW v16, `script.js?v=15`

### 23.09.2026 — Прод-готовность и багфиксы
- **`MEBLIO_DEV` default 0**: verify/reset-ссылки больше не утекают в API-ответы, если переменная не задана (раньше dev-режим был включён по умолчанию); хелпер `is_dev_mode()` в `common.py`, тесты явно ставят `MEBLIO_DEV=1`
- **Публичный URL**: `public_host()`/`public_base_url()` — host из `MEBLIO_HOST` > `Host` > `meblio.local`, схема из `X-Forwarded-Proto` > `MEBLIO_SCHEME`; canonical, sitemap, robots и email-ссылки (регистрация, верификация, сброс пароля) больше не захардкожены на `http://`/`meblio.local`
- **CSP `connect-src` динамический**: `wss://<Host>` (прокси nginx) + `ws://127.0.0.1:<WS_PORT>` (локальный дев); раньше был захардкожен только `ws://127.0.0.1:8001` и блокировал чат за nginx
- **WebSocket в проде**: фронт на HTTPS подключается к `wss://<домен>/ws` (nginx `location = /ws` → :8001); на http — прямой порт из `/config.js` (динамический `window.MEBLIO_CONFIG`, `Cache-Control: no-store`, мимо кэша SW)
- **nginx-баг**: regex `location ~ ^/(ws)?$` матчил `/` и уводил главную страницу на WS-порт 8001 → заменён на `location = /ws`
- **Trusted device → привязка к паролю**: HMAC trusted-device cookie включает `password_hash` — смена/сброс пароля и удаление аккаунта инвалидируют `meblio_device` на всех устройствах (раньше cookie жила 30 дней после смены пароля). Старые cookies невалидны — пользователи с 2FA один раз введут код
- **SW-регистрация**: перенесена из inline-скрипта index.html в `script.js` — inline блокируется CSP `script-src 'self'`, PWA не регистрировалась; SW v17, `script.js?v=16`, `/config.js` не кэшируется
- **Доки**: `.env.example`/`DEPLOY.md` — пути `NIK2` → `Meblio`, документированы `MEBLIO_HOST`/`MEBLIO_SCHEME`, дефолт `MEBLIO_DEV=0`, WS через `/ws`
- Тесты: `DevModeTests`, trusted-device после смены пароля, `/config.js`, динамический CSP; `MEBLIO_HOST=meblio.local` зафиксирован в tests.py

### 23.09.2026 — Политика конфиденциальности и оферта (152-ФЗ)
- **SPA-страницы** `/privacy` и `/offer` (роуты `privacy`/`offer`, SEO-заголовки в `view_titles`, sitemap `/privacy` + `/offer`)
- **Тексты документов**: Политика обработки персональных данных (152-ФЗ, оператор — заглушки реквизитов) и Публичная оферта (ст. 437 ГК РФ); перекрёстные ссылки, футер «Правовая информация»
- **Согласие при регистрации**: чекбокс `consent_pd` (обязательный) + серверная проверка в `api_register` (400 без согласия); миграция `users.consent_pd_at` (TEXT), `create_user(..., consent_pd_at=now())`
- **Cookie-баннер**: `initCookieBanner()` в `script.js` — уведомление о не-обязательных cookie со ссылкой на политику, отказ/принятие в localStorage
- **Фикс `[data-nav]`**: глобальный click-хендлер не содержал `[data-nav]` в `closest` — клики по ссылкам футера/документов не работали
- **Опечатка**: «Данные活動ности» → «Данные активности» в тексте политики
- Ассеты: `styles.css?v=14`, `script.js?v=17`; SW v18 (`STATIC_ASSETS` → v14/v17)
- Тесты (58/58): `test_register_requires_consent`, `test_register_stores_consent_timestamp`, `test_privacy_and_offer_spa_seo`, sitemap-проверки `/privacy`+`/offer`; хелпер `Client.register` шлёт `consent_pd=1`
- Исправлен флак: `test_2fa_login_flow` падал при полном прогоне (TOTP-окно ±1 шаг уже в `verify_totp`; при повторных прогонах — OK)

### 23.09.2026 — Волна P0: ядро сделки (этапы, приёмка, договор, счета, модерация)
- **Этапы заказа**: таблица `order_stages`, дефолты «Замер/Производство/Проект/Монтаж» при выборе исполнителя; API `GET/POST /api/orders/{id}/stages`, `PUT .../stages/{sid}` (done/delete/rename); UI-чек-лист на карточке заказа (кнопка «Этапы», прогресс-бар, +/- этапы)
- **Акт приёмки**: `POST /api/orders/{id}/accept` — только заказчик; закрывает `progress → closed` при всех done-этапах, иначе 400; `force: true` — принять с подтверждением; `warranty_until` = +14 дней; уведомление исполнителю
- **Закрытие с этапами**: `POST .../close` без done-этапов → 400; `force`/admin — в обход; UI предлагает force при ошибке
- **Договор**: `GET /api/orders/{id}/contract` (реквизиты сторон, этапы, счёт); печатная HTML-версия `openContractPrint()` + кнопка «Договор» на карточке; доступ только участникам/админу
- **Статусы счетов**: `PUT /api/invoices/{id}` — только участники; transitions `pending→paid|cancelled`, `paid→cancelled`, иначе 409; уведомление контрагенту; кнопки в карточке счёта
- **Модерация компаний/отзывов**: `users.is_moderation_hidden`, `reviews.is_hidden`; `POST /api/admin/hide` + resolve жалобы с `hide_target`; фильтры в companies/makers/search/sitemap/reviews
- **Админка**: таб «Жалобы» (`/api/admin/reports`, resolve/reject + скрыть)
- Миграции: `order_stages`, `ensure_column` users.is_moderation_hidden / verified_requisites_at, orders.warranty_until, reviews.is_hidden
- UI: этапы/приёмка/договор на order-card, кнопки статуса счёта, таб «Жалобы»; `.stages-*` стили + print media
- Ассеты: `styles.css?v=15`, `script.js?v=18`; SW v19
- Тесты: `DealLifecycleTests` (stages/accept/force/contract/invoice), `ModerationP0Tests` (hide company/review), обновлён `test_close_order_by_participants`

### 23.09.2026 — Волна P1: дубли, воронка, дедлайны, сравнение откликов
- **Дублировать заказ**: `POST /api/orders/{id}/duplicate` (только владелец, rate-limit) — копия как draft с файлами; кнопка «Дублировать» на карточке заказа
- **Дедлайны**: `orders.due_at` (parse из `deadline` при create/publish), `run_deadline_reminders` (3d/1d/overdue, дедуп через `orders.deadline_notified`), `GET/POST /api/deadlines/check` (admin), daemon-поток в `main()` (каждые 6ч)
- **Воронка мастера**: `GET /api/maker/funnel` — стадии available/sent/chosen/in_progress/closed/lost; таб «Воронка» в кабинете исполнителя (`loadMakerFunnel`/`makerFunnelView`)
- **Сравнение откликов**: чекбоксы на откликах, `state.compareResponses`, модалка `compareResponsesView`, кнопки «Сравнить/Сбросить» в `responsesBlock`
- Миграции: `orders.due_at`, `orders.deadline_notified`
- Стили: `.funnel-*`, `.compare-*`
- Ассеты: `styles.css?v=16`, `script.js?v=19`; SW v20
- Тесты: `P1FeatureTests` (duplicate, due_at+reminders+dedup, funnel) — 68/68; флак `test_2fa_login_flow` — TOTP-окно ±2 + retry по offset

### 23.09.2026 — Волна P2: верификация реквизитов, гарантия, калькулятор сметы
- **Верификация реквизитов**: `POST /api/admin/verify-requisites` (только admin) — `users.verified_requisites_at`; смена ИНН/ОГРН в профиле снимает отметку; бейдж «✓ Реквизиты проверены» на карточке компании и в профиле; кнопки админа «Подтвердить/Снять» в блоке реквизитов
- **Гарантия 14 дней**: бейдж на карточке заказа (`warranty_until`, активна/истекла) через `warrantyActive()`; API уже ставит `warranty_until` при accept/close (P0)
- **Калькулятор сметы**: `POST /api/estimate` — размеры (мм), материал из каталога или свой, сложность (simple/medium/complex ×1.0/1.25/1.5), фурнитура (basic/standard/premium); разбивка материалы/сборка/фурнитура, итог за единицу и общая; таб «Калькулятор» в кабинете обеих ролей (`estimateView`/`runEstimate`)
- `public_user` отдаёт `verified_requisites_at`
- Стили: `.estimate-result`, `.badge-verified`
- Ассеты: `styles.css?v=17`, `script.js?v=20`; SW v21
- Тесты: `P2FeatureTests` (verify+clear-on-change+permissions, estimate calc/validation/404, warranty_until в closed) — **71/71**; флак `test_2fa_login_flow` — retry при «Сессия входа истекла» + TOTP offset ±1

### 23.09.2026 — Волна P3: параметры услуг, КП, тарифы, Telegram/MAX
- **Параметры услуг**: таблица `service_params` (name/value/sort); CRUD через create/update service (JSON `params`); отдача в list/detail; UI-редактор в форме услуги + бейджи-строки на карточке/странице услуги
- **КП (коммерческое предложение)**: таблица `proposals` (amount, days, message, items JSON, status sent/accepted/rejected); `POST/GET /api/orders/{id}/proposals`, `POST /api/proposals/{id}/status`; принятие КП = выбор исполнителя + этапы; UI: «Отправить КП»/«КП» на карточке заказа, модалка создания со списком позиций, список с Принять/Отклонить
- **Тарифы Free/Pro**: `users.plan` (default free); free — 5 откликов/мес (проверка в `api_create_response`); `GET /api/tariff`, `POST /api/tariff/upgrade` (статусный billing без провайдера); публичная SPA-страница **`/tariffs`** (SEO, sitemap, footer); ссылка из обзора кабинета
- **Telegram/MAX**: `users.telegram_chat_id` / `users.max_chat_id`; `POST /api/messenger/link`; в `create_notification` — fire-and-forget `send_telegram_message` / `send_max_message` (`TELEGRAM_BOT_TOKEN`, `MAX_API_TOKEN`, `MAX_API_BASE` в `.env.example`); поля в настройках уведомлений
- `public_user`: `plan`, `telegram_chat_id`, `max_chat_id`
- Стили: `.service-params*`, `.proposal-*`, `.tariff-*`
- Ассеты: `styles.css?v=18`, `script.js?v=21`; SW v22
- Тесты: `P3FeatureTests` (params CRUD, free quota + upgrade, proposal create/accept, messenger link, `/tariffs` SEO)

### 23.09.2026 — Волна P4: сплит app.py, Яндекс.Метрика/CSP, дока
- **Сплит `app.py`** (1070 строк): `api_orders.py` (`OrderMixin` — заказы, этапы, КП, воронка, дедлайны, estimate, export), `api_accounts.py` (`AccountMixin` — auth/2FA/profile/уведомления/тарифы/messenger/favorites/документы), `api_market.py` (`MarketMixin` — компании, услуги, отзывы, поиск, статьи, чат, жалобы), `auth_util.py` (TOTP, trusted devices, pending-токены, CSRF); `MeblioHandler` = Account/Order/Market/Admin/Catalog/Ai mixins
- Фикс сплита: восстановлены `@staticmethod` у `_warranty_date` / `_due_at` / `run_deadline_reminders` в `api_orders.py`
- Фикс гонки: `send_json` после записи, но до commit вынесен **за** `with connect()` — `api_create_service`, `api_login` (tfa_required), `api_update_order_stage` (delete), `api_add_favorite`, `api_upload_document`, `api_upload_logo`, `api_create_certificate` (ответ уходил клиенту до commit → следующий запрос не видел запись)
- **Яндекс.Метрика + CSP**: инлайн-сниппет заменён на внешний `/metrica.js` (`serve_metrica_js`, `Cache-Control: no-store`, 404 без id, 400 если не число); тег инжектится в `render_index` при `MEBLIO_METRICA_ID`; CSP условно добавляет `mc.yandex.ru`/`mc.yandex.net` в script-src/img-src/connect-src только когда id задан; `/metrica.js` не в STATIC_FILES и не в SW-прекэше; env: `MEBLIO_METRICA_ID` в `.env.example`
- **Дока**: `README.md` переписан (новая структура файлов, 78 тестов, env); `docs/DEPLOY.md` — строки `TELEGRAM_BOT_TOKEN`, `MAX_API_*`, `MEBLIO_METRICA_ID`
- Ассеты P4 не менялись (клиент не трогался)
- Тесты: +3 в `ConfigJsTests` (metrica off / external+CSP / non-numeric 400) — **78/78**

### 23.09.2026 — Self-hosted шрифты + фикс SW/HTML-кэша
- Google Fonts убраны; Inter + Comfortaa (woff2 subsets) в `fonts/` + `fonts/fonts.css`; CSP `style-src`/`font-src` только `'self'`
- `render_index` и `/index.html` → `Cache-Control: no-store` (HTML больше не залипает со старыми `?v=`); `sw.js` тоже `no-store` (иначе браузер держал старый SW cache-first)
- SW **v23**: network-first для navigate/`/index.html`, без прекэша `/`, `/fonts/fonts.css` в STATIC_ASSETS
- Ассеты: `styles.css?v=19`, `script.js?v=21`; SW v23
- Тест: `test_self_hosted_fonts`, CSP без googleapis — **79/79**

### 24.09.2026 — Хвосты: 404 PWA-иконки, чистка консоли
- `/meblio-512.png` добавлен в `STATIC_FILES` (файл был на диске, манифест/apple-icon ссылались — 404 в консоли)
- AI-виджет: `/api/ai/history` для бейджа провайдера вызывается только при наличии `meblio_session` (анонимы больше не получают 401 в консоли)
- Ассеты: `script.js?v=22`; SW **v24** (+ `/meblio-512.png` в STATIC_ASSETS)
- Тест: `test_pwa_icons_served` — **80/80**

## Known Issues
- Email через SMTP требует задания переменных окружения в проде
- Мультиорганизации, Telegram/MAX-уведомления, 3D-viewer (как у Materix) — в roadmap
- AI-ассистент без `AI_API_KEY` работает в офлайн-режиме (rule-based, без свободного диалога)
- app.py ~1070 строк (после сплита P4; логика — в api_orders/api_accounts/api_market/auth_util)
- Оплаты — только статусы счетов (без платёжного провайдера)

## Следующие шаги
- [x] Уведомления (email/push)
- [x] CSRF-защита
- [x] Email-верификация (таблица создана, эндпоинт готов)
- [x] Push-уведомления в браузере
- [x] Экспорт заказов в PDF
- [x] Отзывы и рейтинги производителей
- [x] Расширенный поиск заказов (статус, бюджет)
- [x] Загрузка документов к профилю компании
- [x] Глобальный поиск по сайту
