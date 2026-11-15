# Changelog

Все заметные изменения проекта. Формат — Keep a Changelog.

## [1.1.0] — 2026-11-16

### Fixed
- README: systemd-инструкция стала выполнимой — добавлены шаги mkdir/venv/
  pip install и явное копирование юнита с переименованием (юнит исполняет
  venv/bin/python, без venv команда enable падала).
- README: метрики покрытия приведены к реальному прогону (96%, database 95%).
- SECURITY.md: добавлены конкретные каналы репорта (GitHub Private
  Vulnerability Reporting — включён на репо — и email владельца).

### Added
- CI: `--cov-fail-under=90` — покрытие больше не может сгнить незаметно.
- og:image/Twitter-карточки шаринга (docs/og-card.png), favicon, canonical,
  robots.txt, sitemap.xml.

## [1.0.0] — до 2026-11
Начальный бот: FSM-приёмка лабораторных, статусы задач, напоминания,
мониторинг URL, sqlite с WAL-оптимизациями.
