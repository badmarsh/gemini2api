"""A hung request must fail over at once instead of retrying the same account.

generate() retries any exception on the same account up to settings.max_retries
times. With a 180s request timeout, one hung account therefore cost about three
full timeouts before the pool could try the next account. A timeout means the
account's web session is stuck, so it must propagate on the first attempt;
ordinary transient errors keep their same-account retries.
"""
import asyncio

import pytest
from curl_cffi.requests.errors import RequestsError

import app.core.gemini_client as gc

CURLE_OPERATION_TIMEDOUT = 28


def _make_client(monkeypatch, errors):
    """Client whose _send_request raises each error in turn, counting calls."""
    monkeypatch.setattr(gc.settings, "max_retries", 3)

    async def _no_sleep(*a, **k):
        return None

    monkeypatch.setattr(gc.asyncio, "sleep", _no_sleep)

    async def _no_jitter(*a, **k):
        return None

    monkeypatch.setattr(gc, "apply_jitter", _no_jitter)

    client = gc.GeminiWebClient(psid="psid", psidts="psidts")
    client._healthy = True
    client.send_calls = 0

    async def _send(*a, **k):
        client.send_calls += 1
        raise errors[min(client.send_calls - 1, len(errors) - 1)]

    monkeypatch.setattr(client, "_send_request", _send)
    return client


def _generate(client):
    model = next(iter(gc.GEMINI_MODELS))
    return asyncio.run(client.generate("hi", model))


def test_curl_timeout_fails_over_without_same_account_retry(monkeypatch):
    err = RequestsError("Operation timed out after 180001 milliseconds", CURLE_OPERATION_TIMEDOUT)
    client = _make_client(monkeypatch, [err])

    with pytest.raises(RequestsError):
        _generate(client)

    assert client.send_calls == 1, "a hung account must not be retried on the same account"


def test_asyncio_timeout_fails_over_without_same_account_retry(monkeypatch):
    client = _make_client(monkeypatch, [asyncio.TimeoutError()])

    with pytest.raises(asyncio.TimeoutError):
        _generate(client)

    assert client.send_calls == 1


def test_ordinary_transient_error_still_retries_same_account(monkeypatch):
    client = _make_client(monkeypatch, [ConnectionError("connection reset by peer")])

    with pytest.raises(RuntimeError, match="Exhausted 3 retries"):
        _generate(client)

    assert client.send_calls == 3
