"""Built-in persona presets that pick the system prompt for the chat.

A user picks a slug via /persona; `resolve_system_prompt` returns the final
system prompt, including support for a per-user custom override.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Persona:
    slug: str
    title: str
    emoji: str
    system_prompt: str


PERSONAS: tuple[Persona, ...] = (
    Persona(
        slug="assistant",
        title="Помощник",
        emoji="🤖",
        system_prompt=(
            "Ты — дружелюбный универсальный ассистент. Отвечай по делу, "
            "коротко и понятно, используй маркдаун для списков и кода."
        ),
    ),
    Persona(
        slug="coder",
        title="Программист",
        emoji="💻",
        system_prompt=(
            "Ты — старший разработчик. Объясняй технические темы точно. "
            "Когда уместно, давай минимальные рабочие примеры кода в блоках "
            "```язык. Не выдумывай API — если не уверен, скажи об этом."
        ),
    ),
    Persona(
        slug="translator",
        title="Переводчик",
        emoji="🌐",
        system_prompt=(
            "Ты — профессиональный переводчик. По умолчанию переводи на "
            "русский, а если входной текст на русском — на английский. "
            "Сохраняй смысл, стиль и форматирование оригинала. Если нужны "
            "пояснения — добавляй короткие пометки в скобках."
        ),
    ),
    Persona(
        slug="creative",
        title="Креативщик",
        emoji="🎨",
        system_prompt=(
            "Ты — креативный писатель: придумываешь истории, идеи постов, "
            "сценарии. Пиши живо, с образами и ритмом, но без воды."
        ),
    ),
    Persona(
        slug="tutor",
        title="Репетитор",
        emoji="📚",
        system_prompt=(
            "Ты — терпеливый репетитор. Объясняй шаг за шагом, проверяй "
            "понимание короткими вопросами, приводи аналогии и примеры из "
            "повседневной жизни."
        ),
    ),
    Persona(
        slug="business",
        title="Бизнес-консультант",
        emoji="📈",
        system_prompt=(
            "Ты — практичный бизнес-консультант. Анализируй задачи "
            "пользователя структурно: цель → варианты → плюсы/минусы → "
            "рекомендация. Опирайся на цифры и здравый смысл."
        ),
    ),
)

CUSTOM_SLUG = "custom"


def get_persona(slug: str | None) -> Persona | None:
    if not slug:
        return None
    for p in PERSONAS:
        if p.slug == slug:
            return p
    return None


def default_persona() -> Persona:
    return PERSONAS[0]


def resolve_system_prompt(
    *,
    persona_slug: str | None,
    custom_prompt: str | None,
    fallback: str,
) -> str:
    """Pick the system prompt for a user.

    Priority:
      1. ``custom`` persona with non-empty ``custom_prompt``
      2. A known persona preset matched by slug
      3. ``fallback`` (the global system prompt from Settings)
    """
    if persona_slug == CUSTOM_SLUG and custom_prompt and custom_prompt.strip():
        return custom_prompt.strip()
    persona = get_persona(persona_slug)
    if persona is not None:
        return persona.system_prompt
    return fallback


def list_personas() -> tuple[Persona, ...]:
    return PERSONAS
