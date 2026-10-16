"""Anthropic Messages API (/v1/messages) against Argon: text, system, streaming, tool_use round trip."""
import json

import pytest

import e2e_helpers as h

pytestmark = pytest.mark.live

WEATHER = {"name": "get_weather", "description": "Get the current weather for a city.",
           "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}


@pytest.mark.core
def test_messages_basic(client):
    resp = h.claude(client, {"messages": [{"role": "user", "content": "Reply with exactly one word: OKAPI"}]})
    assert resp["type"] == "message" and resp["role"] == "assistant"
    assert resp["stop_reason"] == "end_turn"
    assert "okapi" in h.claude_text(resp).lower()


def test_messages_system_prompt(client):
    resp = h.claude(client, {"system": "Whatever the user says, answer with the single word TAPIR.",
                             "messages": [{"role": "user", "content": "What is the capital of France?"}]})
    assert "tapir" in h.claude_text(resp).lower()


def test_messages_streaming_event_sequence(client):
    res = h.claude_stream(client, {"messages": [{"role": "user", "content": "Reply with exactly one word: NARWHAL"}]})
    assert "narwhal" in res["text"].lower()
    assert res["stop_reason"] == "end_turn"
    assert "content_block_delta" in res["events"]


@pytest.mark.core
def test_messages_tool_use(client):
    resp = h.claude(client, {"tools": [WEATHER],
                             "messages": [{"role": "user", "content": "What's the weather in Bratislava right now?"}]})
    assert resp["stop_reason"] == "tool_use"
    uses = [b for b in resp["content"] if b["type"] == "tool_use"]
    assert uses and uses[0]["name"] == "get_weather" and uses[0]["id"]
    assert "bratislava" in uses[0]["input"]["city"].lower()


def test_messages_tool_result_round_trip(client):
    msgs = [{"role": "user", "content": "What's the weather in Kosice? Use the tool, then give me the temperature."}]
    first = h.claude(client, {"tools": [WEATHER], "messages": msgs})
    uses = [b for b in first["content"] if b["type"] == "tool_use"]
    assert uses
    msgs += [{"role": "assistant", "content": first["content"]},
             {"role": "user", "content": [{"type": "tool_result", "tool_use_id": uses[0]["id"],
                                           "content": '{"temperature_c": 23.4, "condition": "sunny"}'}]}]
    final = h.claude(client, {"tools": [WEATHER], "messages": msgs})
    assert final["stop_reason"] == "end_turn"
    assert "23.4" in h.claude_text(final)


def test_messages_streaming_tool_use(client):
    res = h.claude_stream(client, {"tools": [WEATHER],
                                   "messages": [{"role": "user", "content": "What's the weather in Prague right now?"}]})
    assert res["stop_reason"] == "tool_use"
    assert res["tool_uses"] and res["tool_uses"][0]["name"] == "get_weather"
    assert "prague" in json.loads(res["tool_uses"][0]["json"])["city"].lower()
