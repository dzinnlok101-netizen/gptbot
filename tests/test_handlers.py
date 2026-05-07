from bot.handlers import _parse_referrer, _split_message


def test_split_short_message() -> None:
    assert _split_message("hello", limit=10) == ["hello"]


def test_split_long_message_by_newline() -> None:
    text = "abc\n" + "x" * 50 + "\n" + "y" * 50
    chunks = _split_message(text, limit=60)
    assert len(chunks) >= 2
    assert "".join(chunks).replace("\n", "").replace(" ", "") == text.replace("\n", "").replace(" ", "")


def test_split_long_message_no_separator() -> None:
    text = "x" * 250
    chunks = _split_message(text, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks) == text


def test_parse_referrer_valid() -> None:
    assert _parse_referrer("ref_42") == 42


def test_parse_referrer_invalid() -> None:
    assert _parse_referrer(None) is None
    assert _parse_referrer("") is None
    assert _parse_referrer("ref_abc") is None
    assert _parse_referrer("foo") is None
