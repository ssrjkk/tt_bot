# DESIGN.md — Strict Tactile Engineer's Notebook & Blueprint (`tt_bot` v2.4)

Дизайн-спецификация сайта-блокнота (`docs/index.html`). Жёсткий, чёткий инженерный блокнот без «AI-slop».

## 1. Визуальная система (`data-theme`)

- **Блокнот (`paper`, по умолчанию)**: жёсткие `2px` контуры `#111827`, hard-offset тени `3px 3px 0`, лист в линейку `#fffdf7`, красное поле `#df7d8a`, нумерация строк `01..34`, металлические кольца и закладки-индексы справа.
- **Чертёж (`blueprint`)**: чертёжная сетка `#0b1930` с циановыми акцентами `#38bdf8`; выбор сохраняется в `localStorage`.

## 2. Строгий UI/UX стандарт

- **Клавиатурная навигация**: `⌘K` / `Ctrl+K` открывает Command Palette; `R` — прогон проверок, `T` — переключение темы, `I` — смена чернил, `Esc` — закрытие модалок.
- **Zero-CLS и `tabular-nums`**: все счётчики SLA, P50, P95 и задержек используют `font-variant-numeric: tabular-nums`.
- **Обратная связь**: стек тактильных тостов, кликабельные диагностические карточки строк и график задержки с линией SLA `100 мс` и hover-точками.
- **Только монохром**: ни один символ витрины не имеет права отрисоваться цветной пиктограммой. Запрещены символы со свойством Unicode `Emoji=Yes` (`U+2705`, `U+274C`, `U+1F7E2`, `U+1F504`, `U+1F3E0` …) и `U+FE0F`, который просит emoji-представление для текстового глифа (`U+25AA U+FE0F`, `U+2716 U+FE0F`). Цифры, `#` и `*` в списке не числятся — пиктограммой они становятся только в keycap-последовательности с `U+20E3`, который запрещён отдельно. Гейт `test_product_ui_has_no_pictographs` сверяет все `src/*.py` и страницы `docs/` с таблицей свойства `Emoji` из Unicode `emoji-data.txt` (147 диапазонов) и раскладывает HTML-сущности, чтобы `&#9998;` не объехал проверку. Словарь статусов: `✓ UP`, `✗ DOWN`, `· PENDING`, `… RUNNING`; шкалы — `█`/`░`; маркер строки — `·`; навигация — `←` `→` `↻` `▸` `‖`.
- **SEO & Accessibility**: чистая статика без бандлера, `prefers-reduced-motion: reduce`, `:focus-visible`, один `<h1>` на страницу, JSON-LD `SoftwareApplication` и карточка `docs/og-card.png` (1200×630). Её исходник — `docs/og-card.html` с `noindex`; рендер: `chrome --headless=new --hide-scrollbars --window-size=1200,630 --screenshot=docs/og-card.png docs/og-card.html`. Штампы обеих витрин сверяет `test_landing_page_version_claims_match_the_bot`, поэтому PNG не может отстать от релиза незаметно. Скриншоты README (`docs/screenshots/*.jpg`) рендерятся из `docs/index.html` через `playwright` + Chromium: вьюпорты `1440×900` и `390×844 @2`, JPEG quality 85, для `content.jpg` — скролл до `#checklist` со смещением `330px`. Провенанс всех бинарных витрин закрепляет `test_binary_proof_assets_match_their_text_sources` по `docs/proof-manifest.json`: правка HTML без перерендера картинки ломает CI.
