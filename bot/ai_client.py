"""Thin wrapper around the OpenAI-compatible Codex Sale API."""

from __future__ import annotations

import base64
import logging
from collections.abc import AsyncIterator, Iterable

from openai import AsyncOpenAI

from bot.database import HistoryRow

logger = logging.getLogger(__name__)


class AIClient:
    def __init__(self, api_key: str, base_url: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    def _build_messages(
        self,
        *,
        system_prompt: str,
        history: Iterable[HistoryRow],
        extra_user: list[dict] | None = None,
    ) -> list[dict]:
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(msg.to_openai() for msg in history)
        if extra_user is not None:
            messages.append({"role": "user", "content": extra_user})
        return messages

    async def chat(
        self,
        *,
        model: str,
        system_prompt: str,
        history: Iterable[HistoryRow],
    ) -> str:
        """Send a non-streaming chat completion request."""
        messages = self._build_messages(system_prompt=system_prompt, history=history)
        response = await self._client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
        )
        if not response.choices:
            raise RuntimeError("API returned no choices")
        content = response.choices[0].message.content or ""
        return content.strip()

    async def chat_stream(
        self,
        *,
        model: str,
        system_prompt: str,
        history: Iterable[HistoryRow],
    ) -> AsyncIterator[str]:
        """Stream the assistant's reply token-by-token.

        Yields incremental text deltas. The caller is expected to accumulate
        them and periodically edit a Telegram message with the running text.
        """
        messages = self._build_messages(system_prompt=system_prompt, history=history)
        stream = await self._client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
            stream=True,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            piece = getattr(delta, "content", None)
            if piece:
                yield piece

    async def chat_vision(
        self,
        *,
        model: str,
        system_prompt: str,
        history: Iterable[HistoryRow],
        image_bytes: bytes,
        image_mime: str,
        question: str,
    ) -> str:
        """Send a multimodal request that includes one inline image."""
        data_url = f"data:{image_mime};base64,{base64.b64encode(image_bytes).decode()}"
        user_parts: list[dict] = [
            {"type": "text", "text": question or "Что на этом изображении?"},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
        messages = self._build_messages(
            system_prompt=system_prompt, history=history, extra_user=user_parts
        )
        response = await self._client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
        )
        if not response.choices:
            raise RuntimeError("API returned no choices")
        content = response.choices[0].message.content or ""
        return content.strip()

    async def transcribe(
        self,
        *,
        model: str,
        audio: bytes,
        filename: str = "audio.ogg",
    ) -> str:
        """Transcribe a voice/audio file using Whisper (or compatible)."""
        from io import BytesIO

        # The Async SDK accepts a tuple/file-like for the `file` parameter.
        file_tuple = (filename, BytesIO(audio))
        result = await self._client.audio.transcriptions.create(
            model=model,
            file=file_tuple,  # type: ignore[arg-type]
        )
        text = getattr(result, "text", None)
        if text is None and isinstance(result, dict):
            text = result.get("text")
        return (text or "").strip()

    async def generate_image(self, *, model: str, prompt: str) -> bytes:
        """Generate an image and return the raw PNG bytes."""
        response = await self._client.images.generate(
            model=model,
            prompt=prompt,
            n=1,
            size="1024x1024",
        )
        if not response.data:
            raise RuntimeError("Image API returned no data")
        item = response.data[0]
        if item.b64_json:
            return base64.b64decode(item.b64_json)
        if item.url:
            import httpx

            async with httpx.AsyncClient(timeout=60.0) as http:
                r = await http.get(item.url)
                r.raise_for_status()
                return r.content
        raise RuntimeError("Image API returned neither b64_json nor url")

    async def close(self) -> None:
        await self._client.close()
