"""Validators must accept well-formed streams and flag each deviation with its own violation code."""
from __future__ import annotations

import json

from evals import sse


def _blk(obj, event: str = "") -> str:
    head = f"event: {event}\n" if event else ""
    return f"{head}data: {obj if isinstance(obj, str) else json.dumps(obj)}\n\n"


def _chunk(delta=None, finish=None, usage=None) -> dict:
    d = {"choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    if usage:
        d["usage"] = usage
    return d


GOOD_CHAT = (
    _blk(_chunk({"role": "assistant", "content": ""}))
    + _blk(_chunk({"content": "po"}))
    + _blk(_chunk({"content": "ng"}))
    + _blk(_chunk({}, "stop", {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}))
    + _blk("[DONE]")
)


def test_parse_sse_basics():
    p = sse.parse_sse(": keepalive\n\ndata: a\n\n")
    assert [e.kind for e in p.events] == ["comment", "data"] and p.violations == []
    assert "unterminated_block" in sse.parse_sse("data: a\n\ndata: b").violations
    assert any(v.startswith("unknown_field") for v in sse.parse_sse("bogus: 1\n\n").violations)


def test_chat_good_stream():
    rep = sse.check_openai_chat(GOOD_CHAT)
    assert rep.ok and rep.content == "pong" and rep.finish_reason == "stop" and rep.saw_done


def test_chat_violations():
    no_done = GOOD_CHAT.replace(_blk("[DONE]"), "")
    assert "done_count_0" in sse.check_openai_chat(no_done).violations
    twice = GOOD_CHAT + _blk("[DONE]")
    assert "done_count_2" in sse.check_openai_chat(twice).violations
    after_done = GOOD_CHAT + _blk(_chunk({"content": "x"}))
    assert "data_after_done" in sse.check_openai_chat(after_done).violations
    no_finish = _blk(_chunk({"content": "a"})) + _blk("[DONE]")
    assert "no_finish_reason" in sse.check_openai_chat(no_finish).violations
    late = _blk(_chunk({}, "stop")) + _blk(_chunk({"content": "late"})) + _blk("[DONE]")
    assert "content_after_finish" in sse.check_openai_chat(late).violations
    bad_usage = _blk(_chunk({}, "stop", {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 9})) + _blk("[DONE]")
    assert "usage_sum_mismatch" in sse.check_openai_chat(bad_usage).violations
    assert "data_not_json_object" in sse.check_openai_chat(_blk("not json") + _blk("[DONE]")).violations


def test_chat_tool_calls():
    def tc(idx, name=None, args=None, cid=None):
        fn = {k: v for k, v in (("name", name), ("arguments", args)) if v is not None}
        return _chunk({"tool_calls": [{"index": idx, "id": cid, "function": fn}]})

    good = (
        _blk(tc(0, "f", '{"a":', "call_1")) + _blk(tc(0, None, "1}")) + _blk(_chunk({}, "tool_calls")) + _blk("[DONE]")
    )
    rep = sse.check_openai_chat(good)
    assert rep.ok and rep.tool_calls[0]["arguments"] == '{"a":1}'
    bad_json = _blk(tc(0, "f", '{"a":', "c")) + _blk(_chunk({}, "tool_calls")) + _blk("[DONE]")
    assert "tool_call_arguments_invalid_json" in sse.check_openai_chat(bad_json).violations
    gap = _blk(tc(1, "f", "{}", "c")) + _blk(_chunk({}, "tool_calls")) + _blk("[DONE]")
    assert "tool_call_index_gap" in sse.check_openai_chat(gap).violations


def test_anthropic_good_and_bad():
    good = (
        _blk({"type": "message_start", "message": {}}, "message_start")
        + _blk({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}, "content_block_start")
        + _blk({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}}, "content_block_delta")
        + _blk({"type": "content_block_stop", "index": 0}, "content_block_stop")
        + _blk({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}}, "message_delta")
        + _blk({"type": "message_stop"}, "message_stop")
    )
    rep = sse.check_anthropic(good)
    assert rep.ok and rep.content == "hi" and rep.finish_reason == "end_turn"
    unclosed = good.replace(_blk({"type": "content_block_stop", "index": 0}, "content_block_stop"), "")
    assert "unclosed_content_block" in sse.check_anthropic(unclosed).violations
    assert "first_event_not_message_start" in sse.check_anthropic(good.split("\n\n", 1)[1] + "\n\n").violations


def test_responses_good_and_bad():
    good = (
        _blk({"type": "response.created", "response": {}}, "response.created")
        + _blk({"type": "response.output_text.delta", "delta": "ok"}, "response.output_text.delta")
        + _blk({"type": "response.completed", "response": {"usage": {"input_tokens": 1}}}, "response.completed")
    )
    rep = sse.check_responses(good)
    assert rep.ok and rep.content == "ok"
    twice = good + _blk({"type": "response.completed", "response": {}}, "response.completed")
    assert "terminal_count_2" in sse.check_responses(twice).violations
    assert "terminal_count_0" in sse.check_responses(good.rsplit(_blk({"type": "response.completed", "response": {"usage": {"input_tokens": 1}}}, "response.completed"), 1)[0]).violations
