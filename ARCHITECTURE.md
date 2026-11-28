# Architecture (v2.4)

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
                                                                   • Авто-миграция v1.x -> v2.4
                                                                   • Дерево: tasks.parent_id, logs.root_task_id/suppressed
                                                                   • Per-monitor SLA 24ч, история 10, /incidents, P95
                                                                           │
                                                                           ▼
                                                                   build_alert_sender (src/main.py)
                                                                   • Инцидент при consecutive_failures == alert_threshold
                                                                   • Восстановление при возврате в OK
                                                                   • Симптом молчит, если выше упал предок (find_root_cause)
```

## Инварианты

- Ни один обработчик или экспорт не обходит проверку владельца `user_id` (IDOR).
- `_SafeResolver` валидирует IP прямо внутри `aiohttp.TCPConnector` (`ttl_dns_cache=0`), а TCP-пробы коннектятся по уже проверенному IP.
- Каждая кнопка в `src/keyboards.py` имеет обработчик в `src/handlers.py`.
- Инцидент принадлежит верхнему упавшему предку: `find_root_cause()` поднимается по
  `parent_id` до самого дальнего `failed` монитора, а все алерты ниже по цепи пишутся с
  `suppressed=1` и `root_task_id` — один сбой базы не превращается в десять уведомлений.
- Связь в дереве проходит проверки «не сам себе», «тот же `user_id`», «без цикла» и
  «длина ≤ `MAX_TREE_DEPTH`»; при удалении монитора дети отвязываются, а цепь, замкнутая
  вручную прямо в БД, не зацикливает обход.
- `/tree` всегда помещается в сообщение Telegram: рендер ограничивает вывод бюджетом
  `TREE_CHAR_BUDGET` по знакам, потому что `html.escape` раздувает заголовок после обрезки.
- `ThrottleMiddleware` защищает бота от флуда нажатиями.
- Все тесты проходят строго оффлайн (`--allow-hosts=127.0.0.1,::1 --allow-unix-socket`).
