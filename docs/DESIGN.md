# DESIGN.md — Strict Tactile Engineer's Notebook & Blueprint (`tt_bot` v2.3)

Дизайн-спецификация сайта-блокнота (`docs/index.html` и `docs/showcase/index.html`). Жёсткий, чёткий инженерный блокнот без «AI-slop».

## 1. Визуальная система (`data-theme`)

- **📄 Блокнот (`paper`, по умолчанию)**: жёсткие `2px` контуры `#111827`, hard-offset тени `3px 3px 0`, лист в линейку `#fffdf7`, красное поле `#df7d8a`, нумерация строк `01..34`, металлические кольца и закладки-индексы справа.
- **📐 Чертёж (`blueprint`)**: чертёжная сетка `#0b1930` с циановыми акцентами `#38bdf8` и синхронизацией `localStorage` между `/` и `/showcase/`.

## 2. Строгий UI/UX стандарт

- **Клавиатурная навигация**: `⌘K` / `Ctrl+K` открывает Command Palette на обеих страницах; `R` — прогон проверок, `T` — переключение темы, `I` — смена чернил, `Esc` — закрытие модалок.
- **Zero-CLS и `tabular-nums`**: все счётчики SLA, P50, P95 и задержек используют `font-variant-numeric: tabular-nums`.
- **Обратная связь**: стек тактильных тостов, кликабельные диагностические карточки строк, график задержки с линией SLA `100 мс` и hover-точками, а в витрине — реальный чат-фид, модалка `show_alert=True` (IDOR) и 3-вкладочный инспектор (`Execution Trace` / `SQLite WAL` / `JSON Logs`).
- **SEO & Accessibility**: чистая статика без бандлера, `prefers-reduced-motion: reduce`, `:focus-visible`, один `<h1>` на страницу, JSON-LD `SoftwareApplication` и карточка `docs/og-card.png` (1200×630).
