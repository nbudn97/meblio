# Деплой Meblio в продакшен

## 0. Подключение Git-remote (активирует CI)

Готовые файлы: `.github/workflows/test.yml` (тесты на Python 3.11–3.13) запускаются автоматически при первом push.

**Вариант А — GitHub CLI:**

```powershell
winget install GitHub.cli
gh auth login
gh repo create meblio --private --source . --remote origin --push
```

**Вариант Б — вручную:** создайте пустой репозиторий на github.com, затем:

```bash
git remote add origin https://github.com/<ваш-логин>/meblio.git
git push -u origin master
```

Статус CI: вкладка **Actions** в репозитории.

## 1. Требования

- Python 3.11+ (только stdlib, зависимостей нет)
- Порты: HTTP `8000`, WebSocket `8001` (обе переменные настраиваются)
- SQLite — файл `meblio.db` рядом с кодом (WAL-режим включён автоматически)

## 2. Конфигурация (.env)

Скопируйте `.env.example` в `.env`. Реальные переменные окружения приоритетнее значений из файла.

| Переменная | Назначение |
|---|---|
| `PORT` / `WS_PORT` | порты HTTP/WS |
| `MEBLIO_DB` / `MEBLIO_UPLOADS` | пути к БД и загрузкам |
| `MEBLIO_DEV` | `1` — возвращать verify/reset-ссылки в ответах API. **В проде выключить** |
| `SMTP_HOST/PORT/USER/PASSWORD/FROM` | реальная отправка писем; пусто = mock в лог |

## 3. Запуск как сервис

Готовые файлы: [`deploy/nginx.conf`](../deploy/nginx.conf), [`deploy/meblio.service`](../deploy/meblio.service).

### Linux (systemd)

Скопируйте юнит и запустите:

```bash
sudo cp deploy/meblio.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now meblio
```

### Windows (Task Scheduler)

```powershell
schtasks /Create /TN "Meblio" /SC ONSTART /RU SYSTEM ^
  /TR "python F:\AI_work\NIK2\app.py"
```

## 4. HTTPS через nginx

Готовый конфиг: [`deploy/nginx.conf`](../deploy/nginx.conf). Cookie помечены `Secure`, PWA требует TLS — вне localhost нужен HTTPS.

Сертификат: `sudo certbot --nginx -d meblio.example.com`.

## 5. Бэкапы

```bash
python backup_db.py            # → backups/meblio-YYYYMMDD-HHMMSS.db
```

Cron (ежедневно в 03:00, хранить 14 дней):

```cron
0 3 * * * cd /opt/meblio && python3 backup_db.py && find backups -name '*.db' -mtime +14 -delete
```

## 6. Обновление версии

```bash
git pull
python -m unittest tests       # зелёные тесты перед рестартом
sudo systemctl restart meblio  # или перезапуск процесса на Windows
```

Миграции схемы применяются автоматически при старте (`init_db`).

## 7. Health-check

`GET /healthz` → `{"ok": true}` — для мониторинга/балансировщика.
