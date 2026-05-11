from bot.streaming import _split


def test_split_short_returns_self() -> None:
    assert _split("hello", 100) == ["hello"]


def test_split_empty_returns_empty_list() -> None:
    assert _split("", 100) == []


def test_split_respects_newline_boundary() -> None:
    text = "abc\n" + "x" * 30 + "\n" + "y" * 30
    chunks = _split(text, 40)
    assert all(len(c) <= 40 for c in chunks)
    assert "".join(chunks).replace("\n", "").replace(" ", "") == text.replace(
        "\n", ""
    ).replace(" ", "")


def test_split_falls_back_to_hard_cut() -> None:
    text = "x" * 250
    chunks = _split(text, 100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks) == text
