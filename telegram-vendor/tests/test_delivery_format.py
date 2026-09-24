"""The delivered item must be tap-to-copy and safe to render as HTML."""
from __future__ import annotations

from bot.handlers.payment import _code


def test_item_is_wrapped_for_tap_to_copy() -> None:
    assert _code("AAAA-BBBB") == "<code>AAAA-BBBB</code>"


def test_html_special_characters_are_escaped() -> None:
    """A key containing < & > must not break the message or inject markup."""
    out = _code("a<b>&c")
    assert out == "<code>a&lt;b&gt;&amp;c</code>"
    assert "<b>" not in out


def test_non_string_content_is_accepted() -> None:
    assert _code(12345) == "<code>12345</code>"
