# Деплой Meblio в продакшен

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

### Linux (systemd)

`/etc/systemd/system/meblio.service`:

```ini
[Unit]
Description=Meblio marketplace
After=network.target

[Service]
WorkingDirectory=/opt/meblio
ExecStart=/usr/bin/python3 app.py
Restart=always
User=meblio
Environment=MEBLIO_DEV=0

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now meblio
```

### Windows (Task Scheduler)

```powershell
schtasks /Create /TN "Meblio" /SC ONSTART /RU SYSTEM ^
  /TR "python F:\AI_work\NIK2\app.py"
```

## 4. HTTPS через nginx

Cookie помечены `Secure`, PWA требует TLS — вне localhost нужен HTTPS.

```nginx
server {
    listen 443 ssl http2;
    server_name meblio.example.com;

    ssl_certificate     /etc/letsencrypt/live/meblio/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/meblio/privkey.pem;

    client_max_body_size 30m;   # загрузки до 25 МБ + запас

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }

    # WebSocket
    location ~ ^/(ws)?$ {
        proxy_pass http://127.0.0.1:8001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
    }
}

server {
    listen 80;
    server_name meblio.example.com;
    return 301 https://$host$request_uri;
}
```

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
