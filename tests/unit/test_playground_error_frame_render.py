"""Playground 流式错误帧的渲染行为守卫（issue #11）。

防的缺陷：后端出错时发的是顶层 `{"error":{message,type,code}}` 帧
（app/routers/openai.py::_err_chunk），它没有 `choices`；前端旧代码只读
`chunk.choices[0].delta`，于是 503/529/500/400 全部被静默吃掉，最后一律渲染成
"无响应内容" —— 面板把四种完全不同的故障压成同一句话，根因彻底丢失。

做法：把 app.js 里真正在跑的那几个纯函数 + 流收尾的渲染分支**原样切出来**丢给 node
执行（仓库自带 node），配一套极小的假 DOM，断言最终气泡里到底出现了什么。
不是"断言某行源码存在"的假测试：改坏任何一条分支都会变红（见文件末尾的变异说明）。

⚠️ app.js 含 NUL 字节，必须用 read_text 读，不能 shell 出去 grep。
"""

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_APP_JS = _ROOT / "static" / "app" / "app.js"
_I18N_JS = _ROOT / "static" / "app" / "i18n.js"

_LANGS = ("zh-CN", "en-US", "ja-JP", "ko-KR", "zh-TW")

_NODE = shutil.which("node")
_needs_node = pytest.mark.skipif(_NODE is None, reason="node 不可用，跳过前端行为测试")


def _app_source() -> str:
    return _APP_JS.read_text(encoding="utf-8")


def _extract_function(source: str, name: str) -> str:
    """按大括号配对切出 `function name(...) { ... }` 全文。"""
    head = source.index(f"function {name}(")
    depth = 0
    i = source.index("{", head)
    start = i
    while i < len(source):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[head:i + 1]
        i += 1
    raise AssertionError(f"{name} 的大括号没配对，app.js 格式可能变了")


def _extract_stream_tail(source: str) -> str:
    """切出流收尾后的渲染分支（从 _pgStreamOutcome 调用到 catch 之前）。"""
    marker = "const outcome = _pgStreamOutcome(content, streamError);"
    start = source.index(marker)
    end = source.index("\n    } catch (error) {", start)
    tail = source[start:end]
    assert "outcome.mode" in tail, "没切到收尾渲染分支，app.js 格式可能变了"
    return tail


def _extract_chunk_body(source: str) -> str:
    """切出流循环里**真正处理单个 chunk 的那段**（issue #11 的真正 call site）。

    以前这段是 harness 里等价复刻的，于是把 app.js 里
    `if (info.kind === 'error') { streamError = info; continue; }` 整块删掉、
    或者把 _pgClassifyChunk(chunk) 换回旧的 chunk.choices[0].delta 内联写法，
    都能让 issue #11 原样复发而测试全绿 —— 覆盖边界恰好停在函数入口。

    现在从 app.js 原样切出来执行：上面两种改法一种让 marker 消失（ValueError），
    一种让 streamError 永远是 null（错误帧落回 'empty' 分支），都会当场变红。
    """
    marker = "const info = _pgClassifyChunk(chunk);"
    start = source.index(marker)  # 找不到 = 调用点被改写，直接 ValueError 报红
    end = source.index("\n                } catch {}", start)
    body = source[start:end]
    assert len(body) > 200, f"切出的 chunk 处理段太短，app.js 格式可能变了: {body!r}"
    return body


def _harness() -> str:
    src = _app_source()
    pieces = [
        _extract_function(src, "_pgClassifyChunk"),
        _extract_function(src, "_pgFormatStreamError"),
        _extract_function(src, "_pgStreamOutcome"),
        _extract_function(src, "_pgErrorSpan"),
    ]
    for name, piece in zip(("_pgClassifyChunk", "_pgFormatStreamError", "_pgStreamOutcome", "_pgErrorSpan"), pieces):
        assert piece.strip().startswith(f"function {name}("), f"切出的 {name} 不对: {piece[:80]!r}"

    const_match = re.search(r"^const _PG_ERR_MAX = (\d+);$", src, re.M)
    assert const_match, "没找到 _PG_ERR_MAX 常量，app.js 格式可能变了"

    tail = _extract_stream_tail(src)
    chunk_body = _extract_chunk_body(src)

    return (
        f"const _PG_ERR_MAX = {const_match.group(1)};\n"
        + "\n".join(pieces)
        + "\n"
        + textwrap.dedent("""
        const _I18N = {
            'playground.gatewayError': 'Gateway error',
            'playground.noContent': 'No response content',
        };
        function t(k) { return _I18N[k] || k; }

        function makeEl(tag) {
            return {
                tag, className: '', children: [], _text: null, _html: null,
                set textContent(v) { this._text = String(v); this._html = null; this.children = []; },
                get textContent() { return this._text === null ? '' : this._text; },
                set innerHTML(v) { this._html = String(v); this._text = null; this.children = []; },
                get innerHTML() { return this._html === null ? '' : this._html; },
                appendChild(c) { this.children.push(c); return c; },
            };
        }
        const document = { createElement: makeEl };
        function _pgRenderContent(text) { return '<RENDERED>' + text + '</RENDERED>'; }
        function snap(el) {
            return {
                text: el._text, html: el._html,
                children: el.children.map(c => ({ tag: c.tag, className: c.className, text: c._text, html: c._html })),
            };
        }

        function run(frames, opts) {
            const _pgMessages = [{ role: 'user', content: 'q' }];
            const aiMsg = makeEl('div');
            const aiBubble = makeEl('div');
            const chatContainer = { scrollTop: 0, scrollHeight: 100 };
            let cleared = 0;
            const _pgClearGeneratingState = () => { cleared += 1; };
            const isImageGen = !!(opts && opts.isImageGen);
            let stillWorking = 0;
            const _pgShowStillWorking = () => { stillWorking += 1; };

            let content = '';
            let gotContent = false;
            let streamError = null;
            let reasoning = '';
            const _reasoningBody = {
                get textContent() { return reasoning; },
                set textContent(v) { reasoning = String(v); },
            };
            const ensureReasoningBlock = () => _reasoningBody;

            // ↓↓↓ 从 app.js 原样切出来的逐帧处理段（不是等价复刻）
            for (const chunk of frames) {
        """)
        + textwrap.indent(chunk_body, "        ")
        + textwrap.dedent("""
            }
            // ↑↑↑ 逐帧处理段结束
        """)
        + textwrap.indent(tail, "    ")
        + textwrap.dedent("""
            return { bubble: snap(aiBubble), messages: _pgMessages, reasoning, cleared,
                     classified: frames.map(_pgClassifyChunk), gotContent, stillWorking,
                     sawStreamError: streamError !== null,
                     mode: outcome.mode, errorText: outcome.errorText };
        }

        const argv = JSON.parse(process.argv[2]);
        process.stdout.write(JSON.stringify(run(argv.frames, argv.opts)));
        """)
    )


def _run(frames, tmp_path: Path, **opts) -> dict:
    script = tmp_path / "harness.mjs"
    script.write_text(_harness(), encoding="utf-8")
    proc = subprocess.run(
        [_NODE, str(script), json.dumps({"frames": frames, "opts": opts})],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, f"node 执行失败:\n{proc.stderr}"
    return json.loads(proc.stdout)


def _delta(**delta):
    return {"id": "x", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta}]}


def _err(message, code=503, type_="api_error"):
    return {"error": {"message": message, "type": type_, "code": code}}


# --------------------------------------------------------------------------
# 四种输入的渲染结果
# --------------------------------------------------------------------------

@_needs_node
def test_error_frame_shows_real_message_and_code(tmp_path):
    """纯错误帧：必须显示真实 message + code，绝不能落到"无响应内容"。"""
    out = _run([_delta(role="assistant"), _err("upstream exploded", 529, "overloaded_error")], tmp_path)

    assert out["mode"] == "error"
    assert out["bubble"]["text"] == "", "错误分支应先清空气泡再 appendChild"
    assert len(out["bubble"]["children"]) == 1
    span = out["bubble"]["children"][0]
    assert span["tag"] == "span" and span["className"] == "text-danger"
    assert span["text"] == "Gateway error (529): upstream exploded"
    assert span["html"] is None, "错误文本必须走 textContent，绝不能进 innerHTML"
    assert "No response content" not in json.dumps(out)
    # 没有助手回复 → 用户消息弹回，避免下一轮重发孤儿消息
    assert out["messages"] == []


@_needs_node
def test_normal_frames_render_content_unchanged(tmp_path):
    out = _run([_delta(role="assistant"), _delta(content="Hello "), _delta(content="world")], tmp_path)

    assert out["mode"] == "content"
    assert out["bubble"]["html"] == "<RENDERED>Hello world</RENDERED>"
    assert out["bubble"]["children"] == []
    assert out["messages"][-1] == {"role": "assistant", "content": "Hello world"}


@_needs_node
def test_reasoning_only_reply_still_says_no_content_not_error(tmp_path):
    """只有思考链没有正文是合法场景（勾了 thinking 时很常见），不得伪造成错误。"""
    out = _run([_delta(role="assistant"), _delta(reasoning_content="thinking hard")], tmp_path)

    assert out["mode"] == "empty"
    assert out["reasoning"] == "thinking hard"
    assert out["bubble"]["text"] == "No response content"
    assert out["bubble"]["children"] == []
    assert "Gateway error" not in json.dumps(out)


@_needs_node
def test_error_after_content_appends_and_keeps_what_arrived(tmp_path):
    """已经流出内容后才出错：正文必须原样保住，错误只做追加。"""
    out = _run([_delta(content="partial answer"), _err("connection reset", 500)], tmp_path)

    assert out["mode"] == "partial-error"
    assert out["bubble"]["html"] == "<RENDERED>partial answer</RENDERED>"
    kids = out["bubble"]["children"]
    assert [k["tag"] for k in kids] == ["br", "span"]
    assert kids[1]["className"] == "text-danger"
    assert kids[1]["text"] == "Gateway error (500): connection reset"
    assert kids[1]["html"] is None
    # 已收到的内容仍然进历史，用户消息不弹回
    assert out["messages"][-1] == {"role": "assistant", "content": "partial answer"}


@_needs_node
def test_the_loop_itself_captures_the_error_frame(tmp_path):
    """钉死 call site：逐帧循环必须真的把 error 帧记下来，而不是当普通帧吃掉。

    防的正是"把循环里那几行删掉/改回旧写法，issue #11 原样复发而测试全绿"。
    harness 跑的是从 app.js **原样切出来**的逐帧处理段（_extract_chunk_body），
    所以这条断言直接落在真实调用点上。
    """
    out = _run([_delta(content="hi"), _err("boom", 503), _delta(content="ignored-after-error")], tmp_path)

    assert out["sawStreamError"] is True, "error 帧没被循环捕获 —— 调用点被改坏了"
    # 错误帧不参与正文累积，且不能把它自己变成正文
    assert out["mode"] == "partial-error"
    assert "boom" not in out["bubble"]["html"]
    assert out["bubble"]["children"][-1]["text"] == "Gateway error (503): boom"


@_needs_node
def test_error_frame_alone_does_not_fall_back_to_no_content(tmp_path):
    """issue #11 的原始截图场景：只有 error 帧时绝不能渲染成"无响应内容"。"""
    out = _run([_err("upstream 503", 503)], tmp_path)

    assert out["sawStreamError"] is True
    assert out["gotContent"] is False
    assert out["mode"] == "error"
    assert out["bubble"]["text"] != "No response content"


# --------------------------------------------------------------------------
# 边界与安全
# --------------------------------------------------------------------------

@_needs_node
def test_untrusted_message_is_never_html_parsed(tmp_path):
    """message 是上游 str(exc) 原文：必须原样躺在 textContent 里，不得被当 HTML 解析。"""
    payload = '<img src=x onerror="alert(1)">'
    out = _run([_err(payload, 400)], tmp_path)

    span = out["bubble"]["children"][0]
    assert span["text"] == f"Gateway error (400): {payload}"
    assert span["html"] is None
    assert "<RENDERED>" not in json.dumps(out), "错误文本绝不能过 _pgRenderContent（它会把 URL 还原成 <img>）"


@_needs_node
def test_long_message_is_clamped(tmp_path):
    """str(exc) 可能是几 KB 的上游 HTML 正文，不能把聊天气泡撑爆。"""
    out = _run([_err("x" * 5000, 502)], tmp_path)

    text = out["bubble"]["children"][0]["text"]
    assert len(text) < 600, f"错误文本没被钳制: {len(text)}"
    assert text.endswith("…")


@_needs_node
def test_error_frame_without_code_still_renders(tmp_path):
    """responses/gemini/claude 路由的 error 帧没有 code，必须容忍缺失。"""
    out = _run([{"error": {"message": "boom", "type": "api_error"}}], tmp_path)

    assert out["mode"] == "error"
    assert out["bubble"]["children"][0]["text"] == "Gateway error: boom"
    # 缺失的 code 必须在分类时就归一成 null，不能把 undefined 漏给渲染层
    assert out["classified"][0] == {"kind": "error", "message": "boom", "code": None}


@_needs_node
def test_tool_calls_frame_is_not_mistaken_for_an_error(tmp_path):
    """delta.tool_calls 帧没有 content，但它不是错误帧。"""
    out = _run([_delta(tool_calls=[{"index": 0, "function": {"name": "f"}}])], tmp_path)

    assert out["mode"] == "empty"
    assert out["classified"][0]["kind"] == "delta"
    assert out["classified"][0]["toolCalls"], "tool_calls 必须被分类保留，不能丢"
    assert "Gateway error" not in json.dumps(out)


# --------------------------------------------------------------------------
# i18n 齐备（防的是"中文面板有、英日韩面板显示原始键名"）
# --------------------------------------------------------------------------

def _i18n_playground_keys() -> dict:
    blocks = {}
    current = None
    for line in _I18N_JS.read_text(encoding="utf-8").splitlines():
        lang = re.match(r"^ {4}'([A-Za-z-]+)': \{$", line)
        if lang:
            current = lang.group(1)
            blocks[current] = set()
            continue
        if current is None:
            continue
        key = re.match(r"^ +'(playground\.[^']+)':", line)
        if key:
            blocks[current].add(key.group(1))
    assert set(_LANGS) <= set(blocks), f"i18n.js 语言块解析异常: {sorted(blocks)}"
    return blocks


def test_gateway_error_key_exists_in_every_language():
    blocks = _i18n_playground_keys()
    missing = [lang for lang in _LANGS if "playground.gatewayError" not in blocks[lang]]
    assert not missing, f"这些语言块缺少 playground.gatewayError: {missing}"


def test_all_language_blocks_share_the_same_playground_keys():
    blocks = _i18n_playground_keys()
    baseline = blocks["zh-CN"]
    assert baseline, "没解析到任何 playground.* 键，i18n.js 格式可能变了"
    drift = {
        lang: {"missing": sorted(baseline - blocks[lang]), "extra": sorted(blocks[lang] - baseline)}
        for lang in _LANGS
        if blocks[lang] != baseline
    }
    assert not drift, f"playground.* 键在各语言块之间漂移了: {drift}"
