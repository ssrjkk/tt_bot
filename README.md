# Telegram Task & Monitoring Bot

[![CI](https://github.com/ssrjkk/tt_bot/actions/workflows/ci.yml/badge.svg)](https://github.com/ssrjkk/tt_bot/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)



Курсовая работа по дисциплине «Технология программирования» (ГУАП, вариант 18).

Телеграм-бот для управления задачами и мониторинга сервисов. Пользователь создаёт задачи с привязкой к целевому эндпоинту, запускает проверки через пул асинхронных воркеров, получает аналитику и выгружает отчёты в CSV.

## Возможности

- Создание задач через FSM-диалог (название → категория → эндпоинт → приоритет)
- Категории: Backend API, QA AutoTest, RPC Node, DevOps / VPS
- Пул асинхронных воркеров на `asyncio.Queue` для выполнения проверок
- Хранение в SQLite (WAL-режим, индексы, 64 MB кэш)
- Аналитическая сводка: всего задач, выполнено, ожидание, ошибки, средний latency
- Экспорт реестра задач в CSV
- Inline-клавиатуры для навигации и управления задачами

## Стек

- Python 3.11+
- aiogram 3.x — асинхронный Telegram-ботфреймворк
- aiosqlite — асинхронный драйвер SQLite
- pytest + pytest-asyncio + pytest-cov — тесты и покрытие
- Docker, systemd — развёртывание

## Структура проекта

```
src/
  main.py          — точка входа: инициализация БД, воркеров, polling
  config.py        — загрузка настроек из переменных окружения
  database.py      — репозиторий: схема БД, CRUD, аналитика
  models.py        — дата-классы DTO (User, Task, ExecutionLog)
  handlers.py      — роутер aiogram: команды, FSM, callback-обработчики
  keyboards.py     — inline-клавиатуры
  services.py      — AsyncWorkerPool, ReportGeneratorService
  states.py        — FSM-состояния для создания задачи
tests/
  conftest.py      — фикстуры (tmp_path БД, worker pool)
  test_database.py — CRUD, каскадное удаление, фильтрация, аналитика
  test_handlers.py — все callback-и и FSM-переходы
  test_services.py — воркер-пул, немедленное выполнение, CSV-отчёт
```

## Быстрый старт

```bash
# Клонировать репозиторий
git clone https://github.com/ssrjkk/tt_bot.git
cd tt_bot

# Установить зависимости
pip install -r requirements.txt

# Задать переменные окружения
export BOT_TOKEN="your_bot_token_here"
export DB_PATH="coursework_var18.db"
export WORKERS_COUNT=3

# Запустить
python -m src.main
```

## Тесты

```bash
pytest --cov=src --cov-report=term-missing
```

24 теста, покрытие 96%:

| Модуль | Покрытие |
|---|---|
| database.py | 94% |
| handlers.py | 94% |
| keyboards.py | 100% |
| models.py | 100% |
| services.py | 97% |
| states.py | 100% |

## Развёртывание

### Docker

```bash
docker build -t tt_bot .
docker run -d --name tt_bot \
  -e BOT_TOKEN=your_token \
  -e DB_PATH=/data/bot.db \
  -v bot_data:/data \
  tt_bot
```

### systemd

Скопировать `systemd_bot.service` в `/etc/systemd/system/`, создать `.env` в `/opt/coursework_var18_bot/`:

```ini
BOT_TOKEN=your_token_here
DB_PATH=/opt/coursework_var18_bot/bot.db
WORKERS_COUNT=3
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now guap_var18_bot.service
```

## Оптимизации производительности

- Одно персистентное соединение с БД вместо открытия/закрытия на каждый запрос
- WAL-режим (`PRAGMA journal_mode=WAL`) — устраняет блокировки `database is locked`
- `PRAGMA synchronous=NORMAL` — баланс между надёжностью и скоростью записи
- `PRAGMA cache_size=-64000` — 64 MB кэш в памяти
- `PRAGMA temp_store=MEMORY` — временные таблицы в RAM
- Три индекса: `tasks(user_id)`, `tasks(status)`, `execution_logs(task_id)`
- Результат: время отклика 125–438 мс вместо 10–20 секунд


## Installation

```bash
git clone https://github.com/ssrjkk/tt_bot.git
cd tt_bot
pip install -r requirements.txt
```

## Usage

```bash
python bot.py
```

## Testing

```bash
pytest
```
