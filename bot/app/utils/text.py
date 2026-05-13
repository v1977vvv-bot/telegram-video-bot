from __future__ import annotations

from html import escape


def safe_html(value: object | None, max_len: int | None = None) -> str:
    """Return text safe for Telegram HTML parse mode."""
    if value is None:
        return ""

    text = _strip_control_chars(str(value))
    if max_len is not None and len(text) > max_len:
        text = f"{text[:max_len]}..."
    return escape(text, quote=False)


def safe_text(value: object | None, max_len: int | None = None) -> str:
    """Backward-compatible alias for HTML-safe Telegram text."""
    return safe_html(value, max_len=max_len)


def _strip_control_chars(value: str) -> str:
    return "".join(
        char
        for char in value
        if char in {"\n", "\r", "\t"} or not (ord(char) < 32 or ord(char) == 127)
    )
