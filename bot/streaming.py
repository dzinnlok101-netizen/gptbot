"""Helpers for streaming LLM output to a Telegram message.

Telegram tightly rate-limits ``editMessageText`` (roughly one edit per second
per chat). To stream tokens into a single message we accumulate text and
flush at most ~`EDIT_INTERVAL` seconds apart, and only when there's enough
new content to bother.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator

from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

logger = logging.getLogger(__name__)

# Tunables.
EDIT_INTERVAL_SEC = 0.9
MIN_DELTA_CHARS = 24
TELEGRAM_MESSAGE_LIMIT = 4096
SAFE_MESSAGE_LIMIT = 4000  # leave headroom for our suffix "▌"
TYPING_CURSOR = " ▌"


async def stream_into_message(
    placeholder: Message,
    tokens: AsyncIterator[str],
    *,
    notify_truncation: bool = True,
) -> str:
    """Consume the async iterator of text deltas and edit ``placeholder``.

    Returns the full accumulated reply. If the reply grows past
    ``SAFE_MESSAGE_LIMIT``, the placeholder is finalised with the head of
    the response and subsequent text is sent as new messages.
    """
    buffer = ""
    last_sent = ""
    last_edit_at = 0.0
    overflowed = False

    async for piece in tokens:
        if not piece:
            continue
        buffer += piece

        if overflowed:
            continue

        if len(buffer) > SAFE_MESSAGE_LIMIT:
            # Truncate the placeholder to a safe length and continue
            # buffering the remainder for follow-up messages.
            head = buffer[:SAFE_MESSAGE_LIMIT]
            await _safe_edit(placeholder, head)
            last_sent = head
            overflowed = True
            continue

        now = time.monotonic()
        if (
            now - last_edit_at >= EDIT_INTERVAL_SEC
            and len(buffer) - len(last_sent) >= MIN_DELTA_CHARS
        ):
            await _safe_edit(placeholder, buffer + TYPING_CURSOR)
            last_sent = buffer
            last_edit_at = now

    # Final flush.
    if overflowed:
        rest = buffer[SAFE_MESSAGE_LIMIT:]
        for chunk in _split(rest, SAFE_MESSAGE_LIMIT):
            await placeholder.answer(chunk)
        if notify_truncation:
            logger.info("streaming reply truncated and continued in follow-ups")
    else:
        if buffer != last_sent:
            await _safe_edit(placeholder, buffer)

    return buffer


async def _safe_edit(message: Message, text: str) -> None:
    if not text:
        return
    try:
        await message.edit_text(text)
    except TelegramAPIError as exc:
        # Common harmless errors: "message is not modified", flood control.
        msg = str(exc).lower()
        if "not modified" in msg:
            return
        if "flood" in msg or "too many requests" in msg:
            await asyncio.sleep(1.5)
            return
        logger.warning("edit_text failed: %s", exc)


def _split(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks
