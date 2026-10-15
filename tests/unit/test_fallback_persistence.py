"""The fallback toggle must survive a restart, also when compose injects FALLBACK_ENABLED.

Under docker-compose ``env_file`` the host .env becomes a real environment variable, which beats
the container-local /app/.env that ``_update_env_file`` writes. The toggle therefore has to be
persisted in data/settings-overrides.json (replayed over the environment at startup).
"""

import json

import pytest

import app.routers.api_keys as ak
from app.config import Settings, settings
from app.core import settings_overrides as so

_AUTH = {"Authorization": "Bearer sk-test-key"}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(so, "OVERRIDES_PATH", tmp_path / "settings-overrides.json")
    monkeypatch.setattr(ak, "_update_env_file", lambda updates: None)
    old = settings.fallback_enabled
    yield
    object.__setattr__(settings, "fallback_enabled", old)


@pytest.mark.parametrize("target", [True, False])
def test_toggle_survives_a_restart_even_when_the_environment_says_otherwise(
    client, monkeypatch, target
):
    object.__setattr__(settings, "fallback_enabled", not target)

    r = client.patch("/admin/api-keys/fallback", json={"enabled": target}, headers=_AUTH)
    assert r.status_code == 200

    # "Restart": a fresh Settings where compose injected the opposite value as a real env var.
    monkeypatch.setenv("FALLBACK_ENABLED", "false" if target else "true")
    fresh = Settings()
    assert fresh.fallback_enabled is (not target)  # the environment alone would revert the toggle
    applied = so.apply_overrides(fresh)

    assert applied == {"fallback_enabled": target}
    assert fresh.fallback_enabled is target


def test_failed_persistence_leaves_memory_and_overrides_file_untouched(client, monkeypatch):
    so.OVERRIDES_PATH.write_text(json.dumps({"max_retries": 7}), encoding="utf-8")
    object.__setattr__(settings, "fallback_enabled", False)

    def boom(updates):
        raise OSError("read-only .env")

    monkeypatch.setattr(ak, "_update_env_file", boom)

    r = client.patch("/admin/api-keys/fallback", json={"enabled": True}, headers=_AUTH)

    assert r.status_code == 500
    assert settings.fallback_enabled is False
    # all-or-nothing: the overrides file must not keep the value the admin was told failed
    assert json.loads(so.OVERRIDES_PATH.read_text(encoding="utf-8")) == {"max_retries": 7}


def test_fallback_is_persistable_but_not_a_panel_field():
    # Adding it to EDITABLE_FIELDS would expose a duplicate toggle in the settings panel and
    # via POST /admin/settings; the dedicated switch in the API keys section owns it.
    assert "fallback_enabled" not in so.EDITABLE_FIELDS
    assert "fallback_enabled" in so.PERSISTABLE_FIELDS
