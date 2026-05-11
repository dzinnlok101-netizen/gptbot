from bot.personas import (
    CUSTOM_SLUG,
    PERSONAS,
    default_persona,
    get_persona,
    list_personas,
    resolve_system_prompt,
)


def test_personas_have_required_fields() -> None:
    for p in PERSONAS:
        assert p.slug and p.title and p.emoji and p.system_prompt
    # All slugs must be unique.
    slugs = [p.slug for p in PERSONAS]
    assert len(slugs) == len(set(slugs))
    # The reserved 'custom' slug must NOT be a preset.
    assert CUSTOM_SLUG not in slugs


def test_default_persona_is_assistant() -> None:
    assert default_persona().slug == "assistant"


def test_get_persona_known_and_unknown() -> None:
    assert get_persona("assistant") is PERSONAS[0]
    assert get_persona("nope") is None
    assert get_persona(None) is None


def test_resolve_system_prompt_falls_back_to_default() -> None:
    # Unknown persona slug → fallback wins.
    assert (
        resolve_system_prompt(
            persona_slug="nope", custom_prompt=None, fallback="DEFAULT"
        )
        == "DEFAULT"
    )


def test_resolve_system_prompt_uses_persona_preset() -> None:
    out = resolve_system_prompt(
        persona_slug="coder", custom_prompt=None, fallback="DEFAULT"
    )
    assert "разработчик" in out or "Разработчик" in out or "программ" in out.lower()


def test_resolve_system_prompt_custom_overrides() -> None:
    out = resolve_system_prompt(
        persona_slug=CUSTOM_SLUG, custom_prompt="Ты пират.", fallback="DEFAULT"
    )
    assert out == "Ты пират."


def test_resolve_custom_empty_falls_back() -> None:
    out = resolve_system_prompt(
        persona_slug=CUSTOM_SLUG, custom_prompt="   ", fallback="DEFAULT"
    )
    assert out == "DEFAULT"


def test_list_personas_matches_constant() -> None:
    assert list_personas() == PERSONAS
