"""Fault-injecting stand-in for the curl_cffi ``AsyncSession`` used by ``GeminiWebClient``.

Delays are expressed in *virtual* seconds and executed at ``time_scale`` real seconds per virtual second,
so a 240 s Argon silence runs in well under a second. The matching asyncio timeouts inside the client are
scaled by the harness (see ``evals.harness``).
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

ARGON_MODEL_ID = "95a4c0abcba24364"
MODEL_HEADER_KEY_FALLBACK = "x-goog-ext-525001261-jspb"


try:  # use the REAL curl_cffi hierarchy: router `except (RuntimeError, ...)` tuples depend on it
    from curl_cffi.requests.exceptions import ConnectionError as _CurlConnError
    from curl_cffi.requests.exceptions import Timeout as _CurlTimeout
except Exception:  # pragma: no cover - curl_cffi missing: degrade to plain exceptions
    _CurlTimeout = _CurlConnError = Exception


class FakeCurlTimeout(_CurlTimeout):
    """curl_cffi Timeout for CURLE_OPERATION_TIMEDOUT (code 28); real MRO: Timeout>RequestException>CurlError>OSError."""

    def __init__(self, msg: str = "Operation timed out"):
        try:
            super().__init__(msg, 28)
        except TypeError:
            super().__init__(msg)
        self.code = 28


class FakeConnectionReset(_CurlConnError):
    """Connection reset (CURLE_RECV_ERROR, code 56) as curl_cffi raises it."""

    def __init__(self, msg: str = "connection reset"):
        try:
            super().__init__(msg, 56)
        except TypeError:
            super().__init__(msg)
        self.code = 56


async def _hang(timeout: float | None, scale: float) -> None:
    """Block like a stalled socket: curl would abort after ``timeout`` (virtual s); with none, effectively never."""
    await asyncio.sleep(timeout * scale if timeout else 3600)
    raise FakeCurlTimeout()


# ---- scenario steps --------------------------------------------------------------------------------
@dataclass
class Sleep:
    seconds: float  # virtual


@dataclass
class Chunk:
    data: bytes


@dataclass
class Raise:
    exc: BaseException


@dataclass
class Hang:
    """Never produce another byte (until the client's timeout fires)."""


@dataclass
class Scenario:
    name: str
    steps: list[Any] = field(default_factory=list)
    status: int = 200
    status_body: str = ""
    kind: str = "ok"  # semantic label: ok | http_error | empty | web_error | malformed | timeout | disconnect ...
    headers: dict = field(default_factory=dict)


class FakeResponse:
    def __init__(self, status: int, text: str = "", headers: dict | None = None):
        self.status_code = status
        self.headers = headers or {}
        self._text = text

    @property
    def text(self) -> str:
        return self._text

    async def atext(self) -> str:
        return self._text


class _StreamResponse(FakeResponse):
    def __init__(self, scenario: Scenario, owner: "FakeSession", timeout: float | None):
        super().__init__(scenario.status, scenario.status_body, scenario.headers)
        self._sc = scenario
        self._owner = owner
        self._timeout = timeout

    async def aiter_content(self):
        t0 = time.monotonic()
        scale = self._owner.up.time_scale
        for step in self._sc.steps:
            if isinstance(step, Sleep):
                await asyncio.sleep(step.seconds * scale)
            elif isinstance(step, Chunk):
                yield step.data
            elif isinstance(step, Raise):
                raise step.exc
            elif isinstance(step, Hang):
                await _hang(self._timeout, scale)
            if self._timeout and (time.monotonic() - t0) / scale > self._timeout:
                raise FakeCurlTimeout()


class _StreamCtx:
    def __init__(self, resp: _StreamResponse, owner: "FakeSession"):
        self._resp = resp
        self._owner = owner

    async def __aenter__(self):
        self._owner.up.active_streams += 1
        return self._resp

    async def __aexit__(self, *exc):
        self._owner.up.active_streams -= 1
        self._owner.up.finished_streams += 1
        return False


@dataclass
class Call:
    kind: str  # "post" | "stream"
    model_id: str
    model_kind: str
    timeout: float | None
    t: float
    scenario: str


class FakeUpstream:
    """Programmable upstream. ``program(kind, scenarios)`` queues scenarios per model kind ('argon'/'other');
    when a queue is empty ``default`` for that kind is used. All sessions share one call log."""

    def __init__(self, time_scale: float = 0.002):
        self.time_scale = time_scale
        self.queues: dict[str, list[Scenario]] = {"argon": [], "other": []}
        self.defaults: dict[str, Scenario | None] = {"argon": None, "other": None}
        self.calls: list[Call] = []
        self.active_streams = 0
        self.finished_streams = 0

    def program(self, kind: str, *scenarios: Scenario) -> "FakeUpstream":
        self.queues[kind].extend(scenarios)
        return self

    def default(self, kind: str, scenario: Scenario) -> "FakeUpstream":
        self.defaults[kind] = scenario
        return self

    def _next(self, kind: str) -> Scenario:
        if self.queues[kind]:
            return self.queues[kind].pop(0)
        sc = self.defaults[kind]
        if sc is None:
            raise AssertionError(f"FakeUpstream: no scenario programmed for model kind '{kind}'")
        return sc

    @staticmethod
    def model_of(headers: dict | None) -> tuple[str, str]:
        """(model_id, 'argon'|'other') decoded from the model header the client sends."""
        for k, v in (headers or {}).items():
            if k.lower() == MODEL_HEADER_KEY_FALLBACK:
                try:
                    mid = json.loads(v)[4]
                except Exception:  # noqa: BLE001
                    mid = ""
                return mid, ("argon" if mid == ARGON_MODEL_ID else "other")
        return "", "other"

    def session(self) -> "FakeSession":
        return FakeSession(self)

    def calls_for(self, kind: str) -> list[Call]:
        return [c for c in self.calls if c.model_kind == kind]


class FakeJar:
    """Cookie jar stub: no disk I/O, no cookie rotation."""

    def get_all(self):
        return {"__Secure-1PSID": "fake", "__Secure-1PSIDTS": "fake"}

    def set(self, *a, **k):
        pass

    def update_from_response(self, resp):
        pass


class FakeSession:
    def __init__(self, up: FakeUpstream):
        self.up = up

    async def close(self):
        pass

    def _begin(self, kind: str, headers, timeout) -> Scenario:
        mid, mk = self.up.model_of(headers)
        sc = self.up._next(mk)
        self.up.calls.append(Call(kind, mid, mk, timeout, time.monotonic(), sc.name))
        return sc

    async def post(self, url, **kwargs):
        timeout = kwargs.get("timeout")
        sc = self._begin("post", kwargs.get("headers"), timeout)
        scale = self.up.time_scale
        t0 = time.monotonic()
        parts: list[bytes] = []
        for step in sc.steps:
            if isinstance(step, Sleep):
                await asyncio.sleep(step.seconds * scale)
            elif isinstance(step, Chunk):
                parts.append(step.data)
            elif isinstance(step, Raise):
                raise step.exc
            elif isinstance(step, Hang):
                await _hang(timeout, scale)
            if timeout and (time.monotonic() - t0) / scale > timeout:
                raise FakeCurlTimeout()
        body = b"".join(parts).decode("utf-8", "replace") if sc.status < 400 else sc.status_body
        return FakeResponse(sc.status, body, sc.headers)

    def stream(self, method, url, **kwargs):
        timeout = kwargs.get("timeout")
        sc = self._begin("stream", kwargs.get("headers"), timeout)
        return _StreamCtx(_StreamResponse(sc, self, timeout), self)
