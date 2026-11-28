import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MOBILE_BLOCK = re.compile(r"@media\(max-width:900px\)\{(.*?)\n\}", re.S)


class LandmarkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.elements[attrs["id"]] = (tag, attrs)


@pytest.fixture
def landing():
    text = (ROOT / "docs" / "index.html").read_text(encoding="utf-8")
    assert len(text) > 70_000, "a11y gate is not scanning the real landing"
    assert text.count("<button") >= 30 and text.count("<a ") >= 5
    return text


@pytest.fixture
def mobile_css(landing):
    match = MOBILE_BLOCK.search(landing)
    assert match is not None and len(match.group(1)) > 500
    return match.group(1)


def test_mobile_interactive_targets_are_at_least_44px(mobile_css):
    assert "button,input,select,textarea,a[href]{min-height:44px}" in mobile_css
    assert "button,input,select,textarea{min-width:44px}" in mobile_css
    assert ".ink-dot{width:44px;height:44px}" in mobile_css
    assert "min-height:24px" not in mobile_css


def test_mobile_endpoint_input_prevents_ios_auto_zoom(mobile_css):
    assert "#pad-input{font-size:16px}" in mobile_css


def test_mobile_code_bar_keeps_the_copy_action_visible(mobile_css):
    assert ".code-bar{flex-wrap:wrap;gap:8px}" in mobile_css
    assert "#code-caption{flex:1 1 100%}" in mobile_css


def test_scrollable_code_block_is_keyboard_focusable_and_named(landing):
    parser = LandmarkParser()
    parser.feed(landing)
    tag, attrs = parser.elements["code-block"]
    assert tag == "pre"
    assert attrs.get("tabindex") == "0"
    assert attrs.get("role") == "region"
    caption_id = attrs["aria-labelledby"]
    assert caption_id == "code-caption"
    assert re.search(r'<span id="code-caption">[^<]+</span>', landing)


def test_command_palette_restores_trigger_focus(landing):
    assert "let cmdkReturnFocus = null;" in landing
    opened = re.search(r"function openCmdk\(\)\{(.*?)\n  \}", landing, re.S)
    closed = re.search(r"function closeCmdk\(\)\{(.*?)\n  \}", landing, re.S)
    assert opened is not None and closed is not None
    assert (
        "cmdkReturnFocus = document.activeElement instanceof HTMLElement ? "
        "document.activeElement : null;" in opened.group(1)
    )
    body = closed.group(1)
    guard = body.index('if(!backdrop.classList.contains("open")) return;')
    restore = body.index("if(target && target.isConnected) target.focus();")
    assert guard < restore
    assert "cmdkReturnFocus = null;" in body
