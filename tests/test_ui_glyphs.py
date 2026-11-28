"""Витрина продукта обязана оставаться монохромной.

Символ со свойством Emoji=Yes клиент Telegram или браузер рисует цветной
пиктограммой (U+2705, U+1F7E2, U+25AA + U+FE0F), поэтому «нейтральный» значок в
кнопке — это не нейтральный значок, а цветное изображение в тексте. U+FE0F
просит такое представление для символа, который сам по себе текстовый, а
U+20E3 превращает цифру, # и * в keycap-пиктограмму.

Таблица ниже — свойство Emoji из Unicode emoji-data.txt
(https://www.unicode.org/Public/UCD/latest/ucd/emoji/emoji-data.txt), кроме
цифр, # и *: они попадают в список только как компоненты keycap, а keycap
закрыт запретом на U+20E3. Таблица проверяется отдельным тестом, чтобы её
правка ради зелёного CI была красной.
"""

import html
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_EMOJI_RANGES = (
    "a9,ae,203c,2049,2122,2139,2194-2199,21a9-21aa,231a-231b,2328,23cf,23e9-23f3",
    "23f8-23fa,24c2,25aa-25ab,25b6,25c0,25fb-25fe,2600-2604,260e,2611,2614-2615,2618",
    "261d,2620,2622-2623,2626,262a,262e-262f,2638-263a,2640,2642,2648-2653,265f-2660",
    "2663,2665-2666,2668,267b,267e-267f,2692-2697,2699,269b-269c,26a0-26a1,26a7",
    "26aa-26ab,26b0-26b1,26bd-26be,26c4-26c5,26c8,26ce-26cf,26d1,26d3-26d4,26e9-26ea",
    "26f0-26f5,26f7-26fa,26fd,2702,2705,2708-270d,270f,2712,2714,2716,271d,2721,2728",
    "2733-2734,2744,2747,274c,274e,2753-2755,2757,2763-2764,2795-2797,27a1,27b0,27bf",
    "2934-2935,2b05-2b07,2b1b-2b1c,2b50,2b55,3030,303d,3297,3299,1f004,1f0cf",
    "1f170-1f171,1f17e-1f17f,1f18e,1f191-1f19a,1f1e6-1f1ff,1f201-1f202,1f21a,1f22f",
    "1f232-1f23a,1f250-1f251,1f300-1f321,1f324-1f393,1f396-1f397,1f399-1f39b",
    "1f39e-1f3f0,1f3f3-1f3f5,1f3f7-1f4fd,1f4ff-1f53d,1f549-1f54e,1f550-1f567",
    "1f56f-1f570,1f573-1f57a,1f587,1f58a-1f58d,1f590,1f595-1f596,1f5a4-1f5a5,1f5a8",
    "1f5b1-1f5b2,1f5bc,1f5c2-1f5c4,1f5d1-1f5d3,1f5dc-1f5de,1f5e1,1f5e3,1f5e8,1f5ef",
    "1f5f3,1f5fa-1f64f,1f680-1f6c5,1f6cb-1f6d2,1f6d5-1f6d9,1f6dc-1f6e5,1f6e9",
    "1f6eb-1f6ec,1f6f0,1f6f3-1f6fc,1f7e0-1f7eb,1f7f0,1f90c-1f93a,1f93c-1f945",
    "1f947-1f9ff,1fa70-1fa7c,1fa80-1fac6,1fac8,1facc-1fadd,1fadf-1faeb,1faef-1fafa",
)

# Явно запрещённые модификаторы: они не входят в свойство Emoji=Yes сами по себе.
_FORBIDDEN = {
    "\u20e3": "COMBINING ENCLOSING KEYCAP",
    "\ufe0f": "VARIATION SELECTOR-16 (просит emoji-представление)",
}

DOCS_SURFACES = (
    "docs/index.html",
    "docs/og-card.html",
    "docs/DESIGN.md",
)


def _ui_surfaces():
    """Каждый модуль бота плюс страницы витрины: новый файл не ускользнёт."""
    modules = sorted(
        p.relative_to(ROOT).as_posix() for p in (ROOT / "src").glob("*.py")
    )
    return tuple(modules) + DOCS_SURFACES


def _emoji_table():
    table = []
    for chunk in _EMOJI_RANGES:
        for token in chunk.split(","):
            low, _, high = token.partition("-")
            table.append((int(low, 16), int(high or low, 16)))
    return table


def _is_pictograph(char, table):
    code = ord(char)
    return char in _FORBIDDEN or any(low <= code <= high for low, high in table)


def test_pictogram_table_is_the_unicode_emoji_property():
    """Гейт бессмысленен без корректной таблицы: проверяем её края на фактах."""
    table = _emoji_table()
    codes = {code for low, high in table for code in range(low, high + 1)}
    assert len(table) == 147, len(table)
    assert len(codes) == 1435, len(codes)
    # Цветные пиктограммы, которые жили в витрине до гейта.
    for emoji in ("\u2705", "\u274c", "\U0001f7e2", "\U0001f7e9", "\U0001f3e0"):
        assert ord(emoji) in codes, emoji
    # Монохромная типографика, которой гейт разрешает оставаться.
    for glyph in (
        "\u2713",
        "\u2717",
        "\u00b7",
        "\u2026",
        "\u25b8",
        "\u2016",
        "\u2192",
        "\u21bb",
        "\u2588",
        "\u2591",
        "\u25a1",
        "\u2318",
    ):
        assert ord(glyph) not in codes, glyph


def test_pictogram_gate_scans_a_real_surface():
    """Зелёный гейт, который ничего не прочитал, — это слепой зонд."""
    surfaces = _ui_surfaces()
    assert len(surfaces) >= 10, surfaces
    total = 0
    non_empty = 0
    for rel in surfaces:
        path = ROOT / rel
        assert path.is_file(), rel
        non_empty += 1 if path.read_text(encoding="utf-8").strip() else 0
        total += path.stat().st_size
    assert non_empty >= 8, non_empty
    assert total > 60_000, total


def test_product_ui_has_no_pictographs():
    """Ни одного символа, который клиент отрисует цветной пиктограммой."""
    table = _emoji_table()
    hits = []
    for rel in _ui_surfaces():
        lines = (ROOT / rel).read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, 1):
            # &#9999; (U+270F) рисует пиктограмму так же верно, как сам символ.
            probe = line + html.unescape(line)
            found = {char for char in probe if _is_pictograph(char, table)}
            if found:
                glyphs = " ".join(f"U+{ord(char):04X}" for char in sorted(found))
                hits.append(f"{rel}:{number}: [{glyphs}] {line.strip()[:60]}")
    assert not hits, "\n".join(hits)
