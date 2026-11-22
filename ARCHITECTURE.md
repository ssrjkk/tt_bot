# Architecture (v2.3)

Карта системы `tt_bot`. Исходный код: `src/`, хранилище: SQLite WAL (в Docker — volume `/data`).

## Поток данных

```text
Telegram Bot API (/start, /add, /status, /incidents, /stats, /export, /help, /cancel)
       │
       ▼
src/middlewares.py (ThrottleMiddleware — anti-flood per user_id)
       │
       ▼
src/handlers.py (aiogram 3 Router + states.py + keyboards.py)
  • IDOR Guard: все SQL-операции ограничены user_id
  • Весь ввод экранируется через html.escape
  • Zero-dead-end fallback_message (умная подсказка для URL)
       │
       ├── ручной запуск / Проверить все / Перепроверить упавшие ──► AsyncWorkerPool
       │
MonitorScheduler (тик каждые SCHEDULER_TICK_SEC)
  • get_due_tasks() ───────────────────────────────────────────────► AsyncWorkerPool
  • prune_old_logs() (раз в час)                                           │
                                                                           ▼
                                                                   EndpointProber (src/probes.py)
                                                                   • _SafeResolver (anti-rebinding SSRF)
                                                                   • HTTP/HTTPS (allow_redirects=False + body 64KB)
                                                                   • TLS/SSL cert expiry check
                                                                   • TCP (direct validated IP)
                                                                           │
                                                                           ▼
                                                                   DatabaseRepository (src/database.py)
                                                                   • SQLite WAL + busy_timeout=5000
                                                                   • Авто-миграция v1.x -> v2.3
                                                                   • Per-monitor SLA 24ч, история 10, /incidents, P95
                                                                           │
                                                                           ▼
                                                                   build_alert_sender (src/main.py)
                                                                   • Инцидент при consecutive_failures == alert_threshold
                                                                   • Восстановление при возврате в OK
```

## Инварианты

- Ни один обработчик или экспорт не обходит проверку владельца `user_id` (IDOR).
- `_SafeResolver` валидирует IP прямо внутри `aiohttp.TCPConnector` (`ttl_dns_cache=0`), а TCP-пробы коннектятся по уже проверенному IP.
- Каждая кнопка в `src/keyboards.py` имеет обработчик в `src/handlers.py`.
- `ThrottleMiddleware` защищает бота от флуда нажатиями.
- Все тесты проходят строго оффлайн (`--allow-hosts=127.0.0.1,::1 --allow-unix-socket`).
