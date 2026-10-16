"""Live E2E suite for the Argon model, run against a RUNNING gemini2api (default http://127.0.0.1:5918).

Not part of CI: pyproject `testpaths = ["tests"]`, so this folder only runs when pointed at explicitly.

    $env:G2A_API_KEY = "<your API_KEY>"        # never read from .env by the tests
    py -m pytest e2e -q                          # cheap checks only (health, models, auth)
    $env:E2E_LIVE = "1"
    py -m pytest e2e -q -m core                  # one call per area (~10 Argon calls)
    py -m pytest e2e -q                          # everything (can take well over an hour)

Env: E2E_BASE_URL, E2E_MODEL (default gemini-4-argon), E2E_TIMEOUT (s per request, default 480).
"""
from __future__ import annotations

import httpx
import pytest

import e2e_helpers as h


def pytest_configure(config):
    config.addinivalue_line("markers", "live: calls the model (needs E2E_LIVE=1 and G2A_API_KEY)")
    config.addinivalue_line("markers", "core: smallest representative subset, one live call per area")


def pytest_collection_modifyitems(config, items):
    skip_live = pytest.mark.skip(reason="set E2E_LIVE=1 to run tests that call the model")
    for item in items:
        if "live" in item.keywords and not h.LIVE:
            item.add_marker(skip_live)


@pytest.fixture(scope="session", autouse=True)
def server_up():
    try:
        r = httpx.get(f"{h.BASE_URL}/health", timeout=5)
    except httpx.HTTPError as e:
        pytest.skip(f"gemini2api not reachable at {h.BASE_URL}: {e}", allow_module_level=False)
    assert r.status_code == 200


@pytest.fixture(scope="session")
def anon():
    with httpx.Client(base_url=h.BASE_URL, timeout=15) as c:
        yield c


@pytest.fixture(scope="session")
def client():
    if not h.API_KEY:
        pytest.skip("set G2A_API_KEY (or E2E_API_KEY) to run authenticated tests")
    timeout = httpx.Timeout(connect=10, read=h.TIMEOUT, write=30, pool=30)
    with httpx.Client(base_url=h.BASE_URL, headers={"Authorization": f"Bearer {h.API_KEY}"}, timeout=timeout) as c:
        yield c
