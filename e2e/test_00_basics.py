"""Cheap checks: no model call, so they run without E2E_LIVE."""
import e2e_helpers as h


def test_health(anon):
    r = anon.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_chat_requires_api_key(anon):
    r = anon.post("/v1/chat/completions", json={"model": h.MODEL, "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code in (401, 403)


def test_chat_rejects_wrong_api_key(anon):
    r = anon.post("/v1/chat/completions", headers={"Authorization": "Bearer sk-definitely-wrong"},
                  json={"model": h.MODEL, "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code in (401, 403)


def test_messages_requires_api_key(anon):
    r = anon.post("/v1/messages", json={"model": h.MODEL, "max_tokens": 16,
                                        "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code in (401, 403)


def test_models_lists_argon(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    ids = {m["id"] for m in r.json()["data"]}
    assert "gemini-4-argon" in ids, sorted(ids)


def test_empty_messages_is_client_error(client):
    r = client.post("/v1/chat/completions", json={"model": h.MODEL, "messages": []})
    assert 400 <= r.status_code < 500
