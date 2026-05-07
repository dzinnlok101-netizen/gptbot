"""Thin wrapper around the OpenAI-compatible Codex Sale API."""

from __future__ import annotations

import base64
import logging
from collections.abc import Iterable

from openai import AsyncOpenAI

from bot.storage import Message

logger = logging.getLogger(__name__)


class AIClient:
    def __init__(self, api_key: str, base_url: str) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat(
        self,
        *,
        model: str,
        system_prompt: str,
        history: Iterable[Message],
    ) -> str:
        """Send a chat completion request and return the assistant's reply text."""
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(msg.to_openai() for msg in history)

        response = await self._client.chat.completions.create(
            model=model,
            messages=messages,  # type: ignore[arg-type]
        )
        if not response.choices:
            raise RuntimeError("API returned no choices")
        content = response.choices[0].message.content or ""
        return content.strip()

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
