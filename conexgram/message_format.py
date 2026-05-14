"""Telegram message formatting helpers."""

from __future__ import annotations


def split_message(text: str, max_chars: int) -> list[str]:
    """Split text into chunks that fit Telegram message limits."""
    if max_chars <= 100:
        max_chars = 100
    text = text.strip() or "(no output)"
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        split_at = remaining.rfind("\n", 0, max_chars)
        if split_at < max_chars // 2:
            split_at = max_chars
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks
