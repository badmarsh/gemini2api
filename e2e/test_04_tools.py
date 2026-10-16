"""Function calling on /v1/chat/completions: detection, forced choice, selection, round trip, streaming."""
import pytest

import e2e_helpers as h

pytestmark = pytest.mark.live

WEATHER = h.tool("get_weather", "Get the current weather for a city.", {"city": {"type": "string"}})
STOCK = h.tool("get_stock_price", "Get the latest stock price for a ticker symbol.", {"ticker": {"type": "string"}})
CONVERT = h.tool("convert_currency", "Convert an amount between currencies.",
                 {"amount": {"type": "number"}, "from_currency": {"type": "string"}, "to_currency": {"type": "string"}})


def u(text):
    return [{"role": "user", "content": text}]


@pytest.mark.core
def test_model_calls_the_tool(client):
    resp = h.chat(client, u("What's the weather in Bratislava right now?"), tools=[WEATHER])
    calls = h.tool_calls_of(resp)
    assert resp["choices"][0]["finish_reason"] == "tool_calls"
    assert len(calls) >= 1 and calls[0]["function"]["name"] == "get_weather"
    assert calls[0]["id"] and calls[0]["type"] == "function"
    assert "bratislava" in h.args_of(calls[0])["city"].lower()


def test_picks_the_right_tool_among_several(client):
    resp = h.chat(client, u("What is the latest price of NVDA stock?"), tools=[WEATHER, STOCK, CONVERT])
    calls = h.tool_calls_of(resp)
    assert calls and calls[0]["function"]["name"] == "get_stock_price"
    assert h.args_of(calls[0])["ticker"].upper() == "NVDA"


def test_typed_arguments(client):
    resp = h.chat(client, u("Convert 250 euros to US dollars."), tools=[WEATHER, CONVERT])
    calls = h.tool_calls_of(resp)
    assert calls and calls[0]["function"]["name"] == "convert_currency"
    args = h.args_of(calls[0])
    assert float(args["amount"]) == 250
    assert args["from_currency"].upper().startswith("EUR") and args["to_currency"].upper() in ("USD", "US DOLLAR", "US DOLLARS")


def test_no_tool_call_when_irrelevant(client):
    resp = h.chat(client, u("What is 2 + 2? Answer with the number only."), tools=[WEATHER, STOCK])
    assert not h.tool_calls_of(resp)
    assert "4" in h.text_of(resp)


def test_forced_tool_choice(client):
    forced = {"type": "function", "function": {"name": "get_weather"}}
    resp = h.chat(client, u("Tell me something about Vienna."), tools=[WEATHER, STOCK], tool_choice=forced)
    calls = h.tool_calls_of(resp)
    assert calls and calls[0]["function"]["name"] == "get_weather"


def test_tool_result_round_trip(client):
    msgs = u("What's the weather in Kosice? Use the tool, then tell me the temperature.")
    first = h.chat(client, msgs, tools=[WEATHER])
    calls = h.tool_calls_of(first)
    assert calls, "model did not request the tool"
    msgs += [{"role": "assistant", "content": None, "tool_calls": calls},
             {"role": "tool", "tool_call_id": calls[0]["id"],
              "content": '{"city": "Kosice", "temperature_c": 23.4, "condition": "sunny"}'}]
    final = h.chat(client, msgs, tools=[WEATHER])
    assert not h.tool_calls_of(final)
    assert "23.4" in h.text_of(final)


def test_tool_error_result_is_reported_not_retried_forever(client):
    msgs = u("What's the weather in Atlantis? Use the tool.")
    first = h.chat(client, msgs, tools=[WEATHER])
    calls = h.tool_calls_of(first)
    assert calls
    msgs += [{"role": "assistant", "content": None, "tool_calls": calls},
             {"role": "tool", "tool_call_id": calls[0]["id"], "content": "ERROR: unknown city 'Atlantis'"}]
    final = h.chat(client, msgs, tools=[WEATHER])
    text = h.text_of(final).lower()
    assert text and ("atlantis" in text or "unknown" in text or "error" in text or "couldn" in text or "unable" in text)


@pytest.mark.core
def test_streaming_tool_call(client):
    res = h.chat_stream(client, u("What's the weather in Prague right now?"), tools=[WEATHER])
    assert res.finish_reason == "tool_calls" and res.saw_done
    assert res.tool_calls and res.tool_calls[0]["name"] == "get_weather"
    import json
    assert "prague" in json.loads(res.tool_calls[0]["arguments"])["city"].lower()


def test_parallel_tool_calls_are_all_valid(client):
    resp = h.chat(client, u("Get the weather in Rome AND the weather in Madrid."), tools=[WEATHER])
    calls = h.tool_calls_of(resp)
    assert calls
    cities = {h.args_of(c)["city"].lower() for c in calls}
    assert any("rome" in c for c in cities)
    ids = [c["id"] for c in calls]
    assert len(ids) == len(set(ids)), "tool call ids must be unique"
