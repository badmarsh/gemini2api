"""issue #10-B：工具调用 JSON 畸形时自动重新生成一次。

报告人在 Claude Code 里看到的是我们自己发的降级提示
「（模型返回的工具调用格式有误，已忽略。请重试或换用 gemini-flash。）」，
日志里的畸形片段是被截断的 MCP 工具调用（WorldQuant BRAIN，alpha 表达式很长）。

**明确不实现「截断 JSON 自动修补」**：补全一个被截断的工具调用等于拿猜出来的参数
去执行工具，风险远大于收益。这里只做「重新生成一次」，且最多一次。
"""

import asyncio
import json

from app.utils.tools import (
    MALFORMED_TOOL_NOTICE,
    is_malformed_tool_result,
    parse_tool_response,
    parse_tool_response_with_retry,
)

_AUTH = {"Authorization": "Bearer sk-test-key"}

# 报告人日志里的畸形片段（截断在 arguments 中间）
TRUNCATED = (
    '```json\n{"status":"tool_use","tool_calls":[{"name":"mcp__brain__create_multi_simulation",'
    '"arguments":{"alpha_expre'
)
VALID = '{"status":"tool_use","tool_calls":[{"name":"run_shell","arguments":{"cmd":"ls"}}]}'
PLAIN = "just a normal answer"


def _run(coro):
    return asyncio.run(coro)


class _Regen:
    """可控的重新生成闭包：按序返回给定文本，并记录调用次数。"""

    def __init__(self, *texts, raises=None):
        self.texts = list(texts)
        self.raises = raises
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.texts.pop(0) if self.texts else ""


# ---------------------------------------------------------------------------
# 1. 判定函数
# ---------------------------------------------------------------------------

def test_truncated_tool_json_is_detected_as_malformed():
    parsed = parse_tool_response(TRUNCATED)
    assert parsed == {"type": "text", "content": MALFORMED_TOOL_NOTICE}
    assert is_malformed_tool_result(parsed) is True


def test_valid_and_plain_results_are_not_malformed():
    assert is_malformed_tool_result(parse_tool_response(VALID)) is False
    assert is_malformed_tool_result(parse_tool_response(PLAIN)) is False
    assert is_malformed_tool_result(None) is False
    assert is_malformed_tool_result({"type": "text"}) is False


# ---------------------------------------------------------------------------
# 2. parse_tool_response_with_retry 的四条约束
# ---------------------------------------------------------------------------

def test_malformed_then_valid_retries_exactly_once_and_succeeds():
    regen = _Regen(VALID)
    parsed = _run(parse_tool_response_with_retry(TRUNCATED, regen))
    assert parsed["type"] == "tool_calls"
    assert parsed["tool_calls"][0]["name"] == "run_shell"
    assert regen.calls == 1, "重新生成必须恰好被调用一次"


def test_valid_first_time_never_retries():
    """首次即合法就一次都不重试——不能平白给每个工具请求加倍延迟。"""
    regen = _Regen(VALID)
    parsed = _run(parse_tool_response_with_retry(VALID, regen))
    assert parsed["type"] == "tool_calls"
    assert regen.calls == 0


def test_plain_text_never_retries():
    regen = _Regen(VALID)
    parsed = _run(parse_tool_response_with_retry(PLAIN, regen))
    assert parsed == {"type": "text", "content": PLAIN}
    assert regen.calls == 0


def test_malformed_twice_returns_notice_and_retries_at_most_once():
    regen = _Regen(TRUNCATED, VALID)
    parsed = _run(parse_tool_response_with_retry(TRUNCATED, regen))
    assert parsed == {"type": "text", "content": MALFORMED_TOOL_NOTICE}
    assert regen.calls == 1, "最多一次；绝不递归重试到第三次"


def test_retry_exception_is_swallowed_and_first_result_returned():
    """重试自身抛异常时必须吞掉——不能把一次'降级成功'变成 500。"""
    regen = _Regen(raises=RuntimeError("pool busy"))
    parsed = _run(parse_tool_response_with_retry(TRUNCATED, regen))
    assert parsed == {"type": "text", "content": MALFORMED_TOOL_NOTICE}
    assert regen.calls == 1


def test_retry_returning_empty_falls_back_to_first_result():
    regen = _Regen("")
    parsed = _run(parse_tool_response_with_retry(TRUNCATED, regen))
    assert parsed == {"type": "text", "content": MALFORMED_TOOL_NOTICE}


def test_none_regenerate_is_accepted_and_behaves_like_plain_parse():
    assert _run(parse_tool_response_with_retry(TRUNCATED, None)) == parse_tool_response(TRUNCATED)
    assert _run(parse_tool_response_with_retry(VALID, None)) == parse_tool_response(VALID)


def test_truncated_json_is_never_repaired():
    """反向钉死：绝不能把截断的工具调用补全后执行。二次仍畸形时，
    结果里不得出现那个被截断的工具名/参数——只能是降级提示。"""
    regen = _Regen(TRUNCATED)
    parsed = _run(parse_tool_response_with_retry(TRUNCATED, regen))
    assert parsed["type"] == "text"
    assert "mcp__brain__create_multi_simulation" not in json.dumps(parsed, ensure_ascii=False)
    assert "alpha_expre" not in json.dumps(parsed, ensure_ascii=False)


# ---------------------------------------------------------------------------
# 3. 端点级接线：7 个真实调用点
# ---------------------------------------------------------------------------

def _sequenced_generate(counter, texts):
    async def fake_generate(prompt, model, conversation_id="", attachments=None,
                            gem_id=None, account_id=None, extended_thinking=False):
        i = counter["n"]
        counter["n"] += 1
        return {"text": texts[min(i, len(texts) - 1)], "conversation_id": "",
                "images": [], "thoughts": ""}
    return fake_generate


_OPENAI_TOOLS = [{"type": "function", "function": {
    "name": "run_shell", "description": "run a shell cmd",
    "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}}}}]
_CLAUDE_TOOLS = [{"name": "run_shell", "description": "run a shell cmd",
                  "input_schema": {"type": "object", "properties": {"cmd": {"type": "string"}}}}]
_GEMINI_TOOLS = [{"function_declarations": [{
    "name": "run_shell", "description": "run a shell cmd",
    "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}}}]}]
_RESPONSES_TOOLS = [{"type": "function", "name": "run_shell", "description": "run a shell cmd",
                     "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}}}]


# --- openai.py:411 (非流式) -------------------------------------------------

def test_openai_non_stream_retries_malformed_tool_json(gem_client, monkeypatch):
    import app.routers.openai as oai
    counter = {"n": 0}
    monkeypatch.setattr(oai.gemini_client, "generate",
                        _sequenced_generate(counter, [TRUNCATED, VALID]))

    r = gem_client.post("/v1/chat/completions", json={
        "model": "gemini-flash", "messages": [{"role": "user", "content": "list files"}],
        "tools": _OPENAI_TOOLS,
    }, headers=_AUTH)

    assert r.status_code == 200
    body = r.json()
    assert body["choices"][0]["finish_reason"] == "tool_calls"
    assert body["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "run_shell"
    assert counter["n"] == 2


def test_openai_non_stream_valid_first_time_calls_generate_once(gem_client, monkeypatch):
    import app.routers.openai as oai
    counter = {"n": 0}
    monkeypatch.setattr(oai.gemini_client, "generate", _sequenced_generate(counter, [VALID]))

    r = gem_client.post("/v1/chat/completions", json={
        "model": "gemini-flash", "messages": [{"role": "user", "content": "list files"}],
        "tools": _OPENAI_TOOLS,
    }, headers=_AUTH)

    assert r.status_code == 200
    assert counter["n"] == 1, "零回归：首次合法不得重试"


def test_openai_non_stream_retry_failure_is_not_a_500(gem_client, monkeypatch):
    import app.routers.openai as oai
    calls = {"n": 0}

    async def fake_generate(prompt, model, conversation_id="", attachments=None,
                            gem_id=None, account_id=None, extended_thinking=False):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"text": TRUNCATED, "conversation_id": "", "images": [], "thoughts": ""}
        raise RuntimeError("pool busy")

    monkeypatch.setattr(oai.gemini_client, "generate", fake_generate)
    r = gem_client.post("/v1/chat/completions", json={
        "model": "gemini-flash", "messages": [{"role": "user", "content": "list files"}],
        "tools": _OPENAI_TOOLS,
    }, headers=_AUTH)

    assert r.status_code == 200, "重试失败必须回落到首次的降级结果，不能变成 500"
    assert MALFORMED_TOOL_NOTICE in r.json()["choices"][0]["message"]["content"]
    assert calls["n"] == 2


# --- openai.py:709 (buffered 流式) ------------------------------------------

def test_openai_stream_retries_malformed_tool_json(gem_client, monkeypatch):
    import app.routers.openai as oai
    counter = {"n": 0}
    monkeypatch.setattr(oai.gemini_client, "generate",
                        _sequenced_generate(counter, [TRUNCATED, VALID]))

    r = gem_client.post("/v1/chat/completions", json={
        "model": "gemini-flash", "messages": [{"role": "user", "content": "list files"}],
        "tools": _OPENAI_TOOLS, "stream": True,
    }, headers=_AUTH)

    assert r.status_code == 200
    assert "run_shell" in r.text
    assert '"finish_reason":"tool_calls"' in r.text.replace(" ", "")
    assert counter["n"] == 2


# --- claude.py:145 (非流式) -------------------------------------------------

def test_claude_non_stream_retries_malformed_tool_json(gem_client, monkeypatch):
    import app.routers.claude as cl
    counter = {"n": 0}
    monkeypatch.setattr(cl.gemini_client, "generate",
                        _sequenced_generate(counter, [TRUNCATED, VALID]))

    r = gem_client.post("/claude/v1/messages", json={
        "model": "claude-3-5-sonnet-20241022", "max_tokens": 64,
        "messages": [{"role": "user", "content": "list files"}], "tools": _CLAUDE_TOOLS,
    }, headers=_AUTH)

    assert r.status_code == 200
    body = r.json()
    assert body["stop_reason"] == "tool_use"
    assert body["content"][0]["name"] == "run_shell"
    assert counter["n"] == 2


# --- claude.py:369 (buffered 流式) ------------------------------------------

def test_claude_stream_retries_malformed_tool_json(gem_client, monkeypatch):
    import app.routers.claude as cl
    counter = {"n": 0}
    monkeypatch.setattr(cl.gemini_client, "generate",
                        _sequenced_generate(counter, [TRUNCATED, VALID]))

    r = gem_client.post("/claude/v1/messages", json={
        "model": "claude-3-5-sonnet-20241022", "max_tokens": 64,
        "messages": [{"role": "user", "content": "list files"}],
        "tools": _CLAUDE_TOOLS, "stream": True,
    }, headers=_AUTH)

    assert r.status_code == 200
    assert "tool_use" in r.text and "run_shell" in r.text
    assert counter["n"] == 2


# --- gemini.py:238 (非流式) -------------------------------------------------

def test_gemini_non_stream_retries_malformed_tool_json(gem_client, monkeypatch):
    import app.routers.gemini as gm
    counter = {"n": 0}
    monkeypatch.setattr(gm.gemini_client, "generate",
                        _sequenced_generate(counter, [TRUNCATED, VALID]))

    r = gem_client.post("/v1beta/models/gemini-pro:generateContent", json={
        "contents": [{"role": "user", "parts": [{"text": "list files"}]}],
        "tools": _GEMINI_TOOLS,
    }, headers=_AUTH)

    assert r.status_code == 200
    parts = r.json()["candidates"][0]["content"]["parts"]
    assert parts[0]["functionCall"]["name"] == "run_shell"
    assert counter["n"] == 2


# --- gemini.py:357 (buffered 流式) ------------------------------------------

def test_gemini_stream_retries_malformed_tool_json(gem_client, monkeypatch):
    import app.routers.gemini as gm
    counter = {"n": 0}
    monkeypatch.setattr(gm.gemini_client, "generate",
                        _sequenced_generate(counter, [TRUNCATED, VALID]))

    r = gem_client.post("/v1beta/models/gemini-pro:streamGenerateContent", json={
        "contents": [{"role": "user", "parts": [{"text": "list files"}]}],
        "tools": _GEMINI_TOOLS,
    }, headers=_AUTH)

    assert r.status_code == 200
    assert counter["n"] == 2, "流式工具路径也必须接线"
    assert MALFORMED_TOOL_NOTICE not in r.text
    # 这条路径原样流回文本（不发 functionCall）：必须是重试那段合法 JSON，
    # 绝不能把首次那段被截断的畸形 JSON 漏给客户端。
    assert "run_shell" in r.text
    assert "alpha_expre" not in r.text


def test_gemini_stream_valid_first_time_emits_function_call_without_retry(gem_client, monkeypatch):
    """零回归：首次即合法时不重试，流回的文本与修复前逐字节一致（原始模型文本）。"""
    import app.routers.gemini as gm
    counter = {"n": 0}
    monkeypatch.setattr(gm.gemini_client, "generate", _sequenced_generate(counter, [VALID]))

    r = gem_client.post("/v1beta/models/gemini-pro:streamGenerateContent", json={
        "contents": [{"role": "user", "parts": [{"text": "list files"}]}],
        "tools": _GEMINI_TOOLS,
    }, headers=_AUTH)

    assert r.status_code == 200
    assert counter["n"] == 1
    parts = [
        p
        for line in r.text.splitlines() if line.strip()
        for p in json.loads(line)["candidates"][0]["content"]["parts"]
    ]
    # the client gets a functionCall part (like generateContent), not the raw {"status":"tool_use",...} envelope
    assert [p["functionCall"] for p in parts if "functionCall" in p] == [{"name": "run_shell", "args": {"cmd": "ls"}}]
    assert "".join(p.get("text", "") for p in parts) == ""


# --- responses.py:55 (非流式 + 流式，同一处调用点) ---------------------------

def test_responses_non_stream_retries_malformed_tool_json(gem_client, monkeypatch):
    import app.routers.responses as rr
    counter = {"n": 0}
    monkeypatch.setattr(rr.gemini_client, "generate",
                        _sequenced_generate(counter, [TRUNCATED, VALID]))

    r = gem_client.post("/v1/responses", json={
        "model": "gemini-pro", "input": "list files", "tools": _RESPONSES_TOOLS,
    }, headers=_AUTH)

    assert r.status_code == 200
    item = r.json()["output"][0]
    assert item["type"] == "function_call" and item["name"] == "run_shell"
    assert counter["n"] == 2


def test_responses_stream_retries_malformed_tool_json(gem_client, monkeypatch):
    import app.routers.responses as rr
    counter = {"n": 0}
    monkeypatch.setattr(rr.gemini_client, "generate",
                        _sequenced_generate(counter, [TRUNCATED, VALID]))

    r = gem_client.post("/v1/responses", json={
        "model": "gemini-pro", "input": "list files", "tools": _RESPONSES_TOOLS, "stream": True,
    }, headers=_AUTH)

    assert r.status_code == 200
    assert "function_call" in r.text and "run_shell" in r.text
    assert counter["n"] == 2


def test_responses_valid_first_time_calls_generate_once(gem_client, monkeypatch):
    import app.routers.responses as rr
    counter = {"n": 0}
    monkeypatch.setattr(rr.gemini_client, "generate", _sequenced_generate(counter, [VALID]))

    r = gem_client.post("/v1/responses", json={
        "model": "gemini-pro", "input": "list files", "tools": _RESPONSES_TOOLS,
    }, headers=_AUTH)

    assert r.status_code == 200
    assert counter["n"] == 1
