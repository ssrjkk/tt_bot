# Architecture

Карта системы. Код: `src/`, данные: sqlite (volume /data).

## Поток

```
Telegram ──► handlers.py (aiogram 3 Router)
                │  cmd_start → cb_home → cb_create_start
                ▼
        FSM (states.py): title → category → endpoint/dedline
                │
        services.py (бизнес-логика, напоминания)
                │
        database.py (aiosqlite, персистентное соединение)
                │  WAL · synchronous=NORMAL · cache 64MB · temp в RAM
        models.py / keyboards.py
                ▼
        tasks(user_id), tasks(status), execution_logs(task_id)
```

## Ключевые решения и их причины

1. **Один handlers.py** — масштаб проекта это оправдывает (KISS): ~100
   операторов, каждый сценарий виден целиком.
2. **aiosqlite + WAL** вместо postgres: курсовая живёт на дешёвом VPS,
   volume /data; PRAGMA-набор устраняет «database is locked» при
   WORKERS_COUNT>1.
3. **FSM через aiogram states.py**: шаги приёмки не теряют состояние между
   сообщениями; отмена — колбэком в меню.
4. **Сетевой-guard в CI**: тесты не обращаются наружу даже случайно;
   pip_audit --strict гоняет весь граф зависимостей.
5. **Деплой двумя путями**: Docker (volume /data) или systemd
   (deploy/systemd_bot.service, venv в /opt/coursework_var18_bot).

## Инварианты
- FSM-сценарий не теряет состояние между шагами.
- Ни одного лаборанта-пользователя не оставляют без ответа: каждый хендлер
  закрыт и отвечает на ввод.
- Формат env (BOT_TOKEN/DB_PATH/WORKERS_COUNT) стабилен.
