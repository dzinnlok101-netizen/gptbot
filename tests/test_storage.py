from bot.storage import UserStateStore


def test_history_roundtrip() -> None:
    store = UserStateStore(default_model="gpt-5.5", max_history_messages=4)
    store.append_user(1, "hello")
    store.append_assistant(1, "hi")
    state = store.get(1)
    assert [m.role for m in state.history] == ["user", "assistant"]
    assert state.model == "gpt-5.5"


def test_history_capped() -> None:
    store = UserStateStore(default_model="gpt-5.5", max_history_messages=2)
    for i in range(5):
        store.append_user(1, f"u{i}")
        store.append_assistant(1, f"a{i}")
    history = list(store.get(1).history)
    assert len(history) == 2
    assert history[0].content == "a4" or history[-1].content == "a4"


def test_reset_clears_history_only() -> None:
    store = UserStateStore(default_model="gpt-5.5", max_history_messages=10)
    store.set_model(1, "gpt-5.4-mini")
    store.append_user(1, "hello")
    store.reset(1)
    state = store.get(1)
    assert list(state.history) == []
    assert state.model == "gpt-5.4-mini"


def test_users_are_independent() -> None:
    store = UserStateStore(default_model="gpt-5.5", max_history_messages=10)
    store.append_user(1, "a")
    store.append_user(2, "b")
    assert len(list(store.get(1).history)) == 1
    assert len(list(store.get(2).history)) == 1
