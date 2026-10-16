"""Request-local debug context shared by middleware and the account pool."""
from __future__ import annotations

from contextvars import ContextVar
from hashlib import sha256


_state: ContextVar[object | None] = ContextVar("gemini2api_request_state", default=None)


def bind(state):
    """Bind a Starlette request state object and return a token for resetting it."""
    return _state.set(state)


def reset(token) -> None:
    _state.reset(token)


def current():
    return _state.get()


def set_account(account_id: str) -> str:
    """Expose only a short one-way account hash to the request diagnostics."""
    digest = sha256(str(account_id).encode("utf-8")).hexdigest()[:12]
    state = current()
    if state is not None:
        state.account_hash = digest
    return digest


def set_field(name: str, value) -> None:
    state = current()
    if state is not None:
        setattr(state, name, value)
