"""In-memory per-user state: conversation history and selected model."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Literal

Role = Literal["system", "user", "assistant"]


@dataclass(slots=True)
class Message:
    role: Role
    content: str

    def to_openai(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class UserState:
    model: str
    history: deque[Message] = field(default_factory=deque)


class UserStateStore:
    """Thread-unsafe in-memory store. aiogram dispatcher is single-threaded, so this is fine."""

    def __init__(self, default_model: str, max_history_messages: int) -> None:
        if max_history_messages < 0:
            raise ValueError("max_history_messages must be non-negative")
        self._default_model = default_model
        self._max_history = max_history_messages
        self._states: dict[int, UserState] = {}

    def get(self, user_id: int) -> UserState:
        state = self._states.get(user_id)
        if state is None:
            state = UserState(model=self._default_model, history=deque(maxlen=self._max_history or None))
            self._states[user_id] = state
        return state

    def reset(self, user_id: int) -> None:
        state = self.get(user_id)
        state.history.clear()

    def set_model(self, user_id: int, model: str) -> None:
        state = self.get(user_id)
        state.model = model

    def append_user(self, user_id: int, content: str) -> None:
        self.get(user_id).history.append(Message(role="user", content=content))

    def append_assistant(self, user_id: int, content: str) -> None:
        self.get(user_id).history.append(Message(role="assistant", content=content))
