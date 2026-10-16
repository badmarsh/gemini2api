"""T1 cases: tool calls whose JSON the model emitted with raw control characters, plus other broken shapes.

Argon sometimes puts REAL newlines / tabs inside the JSON string values of the prompt-simulated tool protocol
(``{"status":"tool_use","tool_calls":[{"name":...,"arguments":{...}}]}``). Strict ``json.loads`` rejects that;
app/utils/tools.py decodes with ``strict=False``. Contract on every endpoint, stream and non-stream:

* such a reply becomes a proper tool call whose arguments are valid JSON with the newline restored, from ONE
  upstream call, and the raw envelope never leaks to the client as text;
* a reply that is unrecoverable even with ``strict=False`` is regenerated exactly ONCE, then degrades to the plain
  text notice (never invalid tool arguments, never a retry loop); a valid second reply is used.

Run:  py -3.14 -m pytest evals/cases/test_tool_call_json.py -q -p no:cacheprovider
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from evals.cases.helpers import PROTOS, send
from evals.fake_upstream import scenarios as sc
from evals.harness import InProcessSUT

ARGON, FLASH = "gemini-4-argon", "gemini-3-flash"
TOOL = "write_file"
SCHEMA = {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
          "required": ["path", "content"]}
ARGS = {"path": "notes/a.txt", "content": "line1\nline2\n"}

# Open gaps as {(protocol, stream): reason}; they run as xfail(strict=True) and go red once the app is fixed.
# (native streamGenerateContent with tools used to stream the raw tool_use envelope as text; it now emits functionCall.)
KNOWN_GAPS: dict = {}


def _marks(proto: str, stream: bool) -> list:
    reason = KNOWN_GAPS.get((proto, stream))
    return [pytest.mark.xfail(strict=True, reason=reason)] if reason else []

# finish reason the runner can observe, per (protocol, stream); None = not observable, not asserted
FINISH = {("chat", False): "tool_calls", ("chat", True): "tool_calls", ("anthropic", False): "tool_use",
          ("anthropic", True): "tool_use", ("responses", False): "completed", ("gemini", False): "STOP"}

_RAW_TAB_CR = ('{"status":"tool_use","tool_calls":[{"name":"write_file","arguments":'
               '{"path":"t.txt","content":"a\tb\r\nc"}}]}')
# arguments delivered as a JSON *string* (double encoded) that itself holds a raw newline
_ARGS_AS_STRING = ('{"status":"tool_use","tool_calls":[{"name":"write_file","arguments":'
                   '"{\\"path\\":\\"a.txt\\",\\"content\\":\\"l1\nl2\\"}"}]}')
_RAW = sc.tool_call_text([(TOOL, ARGS)], raw_newlines=True)
_TWO = sc.tool_call_text([(TOOL, {"path": "a", "content": "x\ny"}), (TOOL, {"path": "b", "content": "p\nq"})],
                         raw_newlines=True)

# name -> (model text, expected tool calls)
VARIANTS = {
    "raw_newline": (_RAW, [(TOOL, ARGS)]),
    "raw_tab_cr": (_RAW_TAB_CR, [(TOOL, {"path": "t.txt", "content": "a\tb\r\nc"})]),
    "code_fence": ("```json\n" + _RAW + "\n```", [(TOOL, ARGS)]),
    "prose_before": ("Sure, writing it now.\n" + _RAW, [(TOOL, ARGS)]),
    "arguments_as_string": (_ARGS_AS_STRING, [(TOOL, {"path": "a.txt", "content": "l1\nl2"})]),
    "two_calls": (_TWO, [(TOOL, {"path": "a", "content": "x\ny"}), (TOOL, {"path": "b", "content": "p\nq"})]),
}
NEEDS_LOOSE_JSON = ("raw_newline", "raw_tab_cr", "arguments_as_string", "two_calls")

# Unrecoverable even with strict=False
TRUNCATED = _RAW[: _RAW.index("line2")]
INNER_QUOTE = ('{"status":"tool_use","tool_calls":[{"name":"write_file","arguments":'
               '{"path":"a.txt","content":"say "hi" now\nbye"}}]}')


def _norm(rec: dict) -> list[dict]:
    return [{"name": t["name"], "arguments": json.loads(t["arguments"] or "{}")} for t in rec["tool_calls"]]


def _want(calls) -> list[dict]:
    return [{"name": n, "arguments": a} for n, a in calls]


def _request(proto: str, model: str, stream: bool) -> tuple[str, dict]:
    if proto == "chat":
        return "/v1/chat/completions", {
            "model": model, "stream": stream, "messages": [{"role": "user", "content": "write the file"}],
            "tools": [{"type": "function", "function": {"name": TOOL, "description": "write", "parameters": SCHEMA}}]}
    if proto == "responses":
        return "/v1/responses", {
            "model": model, "stream": stream, "input": "write the file",
            "tools": [{"type": "function", "name": TOOL, "description": "write", "parameters": SCHEMA}]}
    if proto == "anthropic":
        return "/v1/messages", {
            "model": model, "stream": stream, "max_tokens": 64,
            "messages": [{"role": "user", "content": "write the file"}],
            "tools": [{"name": TOOL, "description": "write", "input_schema": SCHEMA}]}
    body = {"contents": [{"role": "user", "parts": [{"text": "write the file"}]}],
            "tools": [{"functionDeclarations": [{"name": TOOL, "description": "write", "parameters": SCHEMA}]}]}
    method = "streamGenerateContent" if stream else "generateContent"
    return f"/v1beta/models/{model}:{method}", body


async def _one(proto: str, model: str, stream: bool, first: str, then: str | None = None) -> dict:
    """``first`` is the model's reply; ``then`` (optional, Flash only) is the reply to the single regeneration."""
    async with InProcessSUT(accounts=2) as sut:
        if then is None:
            sut.upstream.default("argon", sc.ok(first, n_frames=3))
            sut.upstream.default("other", sc.ok(first, n_frames=3))
        else:
            sut.upstream.program("other", sc.ok(first, n_frames=3))
            sut.upstream.default("other", sc.ok(then, n_frames=3))
        path, body = _request(proto, model, stream)
        async with httpx.AsyncClient(base_url=sut.base_url, headers=sut.auth(), timeout=60) as client:
            rec = await send(client, proto, path, body, stream, timeout_s=30)
        await asyncio.sleep(0.05)
        rec["upstream_calls"] = len(sut.upstream.calls)
        rec["leaked_slots"] = sut.active_slots()
        return rec


def run(*a, **kw) -> dict:
    return asyncio.run(_one(*a, **kw))


def _sid(stream: bool) -> str:
    return "stream" if stream else "nonstream"


def _assert_tool_calls(r: dict, calls, proto: str, stream: bool, *, upstream_calls: int = 1) -> None:
    assert r["status"] == 200, r
    assert r["error_class"] == "", r
    assert r["violations"] == [], r
    assert not r["tool_args_invalid"], r
    assert _norm(r) == _want(calls), r
    assert r["content"].strip() == "", f"raw tool envelope leaked as text: {r['content']!r}"
    assert r["upstream_calls"] == upstream_calls, r
    assert r["leaked_slots"] == 0, r
    fin = FINISH.get((proto, stream))
    if fin:
        assert r["finish_reason"] == fin, r


@pytest.mark.parametrize("name", NEEDS_LOOSE_JSON)
def test_fixtures_really_need_non_strict_json(name: str):
    """Guard: if a fixture were valid strict JSON the cases below would test nothing."""
    text = VARIANTS[name][0]
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)
    json.loads(text, strict=False)  # ... yet loose-valid


def _matrix():
    for proto in PROTOS:
        for stream in (False, True):
            for model in (FLASH, ARGON):
                yield pytest.param(proto, stream, model, id=f"{proto}-{_sid(stream)}-{model}",
                                   marks=_marks(proto, stream))


@pytest.mark.parametrize("proto,stream,model", list(_matrix()))
def test_raw_newlines_in_tool_arguments_on_every_endpoint(proto: str, stream: bool, model: str):
    text, calls = VARIANTS["raw_newline"]
    r = run(proto, model, stream, text)
    _assert_tool_calls(r, calls, proto, stream)


@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream"])
@pytest.mark.parametrize("variant", [v for v in VARIANTS if v not in ("raw_newline", "two_calls")])
def test_tool_json_variants_on_chat(variant: str, stream: bool):
    text, calls = VARIANTS[variant]
    r = run("chat", FLASH, stream, text)
    _assert_tool_calls(r, calls, "chat", stream)


def _parallel():
    for proto in ("responses", "anthropic", "gemini"):
        for stream in (False, True):
            yield pytest.param(proto, stream, id=f"{proto}-{_sid(stream)}", marks=_marks(proto, stream))


@pytest.mark.parametrize("proto,stream", list(_parallel()))
def test_parallel_tool_calls_with_raw_newlines(proto: str, stream: bool):
    text, calls = VARIANTS["two_calls"]
    r = run(proto, FLASH, stream, text)
    _assert_tool_calls(r, calls, proto, stream)


def _notice() -> str:
    from app.utils.tools import MALFORMED_TOOL_NOTICE  # lazy: app.* must import after the harness set the env

    return MALFORMED_TOOL_NOTICE


def _assert_degraded(r: dict) -> None:
    assert r["status"] == 200, r
    assert r["error_class"] == "", r
    assert r["violations"] == [], r
    assert not r["tool_args_invalid"] and r["tool_calls"] == [], r
    assert r["content"].strip() == _notice().strip(), r
    assert r["upstream_calls"] == 2, "expected exactly one regeneration, no retry loop"
    assert r["leaked_slots"] == 0, r


@pytest.mark.parametrize("stream", [False, True], ids=["nonstream", "stream"])
@pytest.mark.parametrize("proto", PROTOS)
def test_truncated_tool_json_is_regenerated_once_then_degrades(proto: str, stream: bool):
    _assert_degraded(run(proto, FLASH, stream, TRUNCATED))


def test_complete_unescaped_inner_quotes_are_repaired_once():
    """A complete argument object is repaired; only truncation remains non-repairable."""
    r = run("chat", FLASH, False, INNER_QUOTE)
    assert r["status"] == 200, r
    assert r["error_class"] == "", r
    assert r["violations"] == [], r
    assert r["tool_args_invalid"] is False, r
    assert len(r["tool_calls"]) == 1, r
    args = json.loads(r["tool_calls"][0]["arguments"])
    assert args["content"] == 'say "hi" now\nbye'
    assert r["content"].strip() == ""
    assert r["upstream_calls"] == 1, "complete quote repair must not trigger regeneration"
    assert r["leaked_slots"] == 0, r


# chat covers both modes (its stream path regenerates separately); the others share one code path per router
_REGEN = [("chat", False), ("chat", True), ("responses", False), ("anthropic", False), ("gemini", False)]


@pytest.mark.parametrize("proto,stream", _REGEN, ids=[f"{p}-{_sid(s)}" for p, s in _REGEN])
def test_valid_regeneration_replaces_a_broken_reply(proto: str, stream: bool):
    text, calls = VARIANTS["raw_newline"]
    r = run(proto, FLASH, stream, TRUNCATED, then=text)
    _assert_tool_calls(r, calls, proto, stream, upstream_calls=2)
