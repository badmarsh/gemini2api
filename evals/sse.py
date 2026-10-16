"""Strict Server-Sent Events parser plus protocol-level validators.

``parse_sse`` implements the wire grammar (blocks separated by blank lines, ``field: value`` lines,
``:`` comments) and reports every deviation as a violation instead of silently tolerating it.
The ``check_*`` functions then validate OpenAI chat, OpenAI Responses and Anthropic stream lifecycles.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

_KNOWN_FIELDS = {"data", "event", "id", "retry"}


@dataclass
class SSEEvent:
    kind: str  # "data" | "comment"
    data: str = ""
    event: str = ""
    comment: str = ""
    index: int = 0  # block index in the stream


@dataclass
class SSEParse:
    events: list[SSEEvent] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    @property
    def data_events(self) -> list[SSEEvent]:
        return [e for e in self.events if e.kind == "data"]

    @property
    def comments(self) -> list[SSEEvent]:
        return [e for e in self.events if e.kind == "comment"]


def parse_sse(text: str) -> SSEParse:
    """Parse a complete SSE body. CRLF is normalised; a final block without a blank line is a violation."""
    out = SSEParse()
    norm = text.replace("\r\n", "\n")
    if not norm:
        return out
    terminated = norm.endswith("\n\n")
    blocks = norm.split("\n\n")
    if terminated:
        blocks = blocks[:-1]
    elif blocks:
        out.violations.append("unterminated_block")
    for i, block in enumerate(blocks):
        if block == "":
            out.violations.append("empty_block")
            continue
        data_lines: list[str] = []
        event_name = ""
        comment_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith(":"):
                comment_lines.append(line[1:].lstrip(" "))
                continue
            if ":" in line:
                fname, _, value = line.partition(":")
                value = value[1:] if value.startswith(" ") else value
            else:
                fname, value = line, ""
            if fname not in _KNOWN_FIELDS:
                out.violations.append(f"unknown_field:{fname[:20]}")
                continue
            if fname == "data":
                data_lines.append(value)
            elif fname == "event":
                event_name = value
        if data_lines:
            out.events.append(SSEEvent("data", data="\n".join(data_lines), event=event_name, index=i))
        if comment_lines:
            out.events.append(SSEEvent("comment", comment="\n".join(comment_lines), index=i))
        if not data_lines and not comment_lines and not event_name:
            out.violations.append("block_without_content")
    return out


@dataclass
class StreamReport:
    protocol: str
    content: str = ""
    thoughts: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict | None = None
    error: dict | None = None
    n_data: int = 0
    n_comments: int = 0
    saw_done: bool = False
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations and self.error is None


def _loads(s: str):
    try:
        return json.loads(s), None
    except json.JSONDecodeError as e:
        return None, str(e)[:60]


def check_openai_chat(text: str) -> StreamReport:
    """Validate an OpenAI chat.completions SSE body."""
    p = parse_sse(text)
    rep = StreamReport("openai_chat", violations=list(p.violations))
    rep.n_comments = len(p.comments)
    data = p.data_events
    rep.n_data = len(data)
    done_idx = [i for i, e in enumerate(data) if e.data.strip() == "[DONE]"]
    rep.saw_done = bool(done_idx)
    finish_seen = 0
    after_finish_content = False
    tool_acc: dict[int, dict] = {}
    for ev in data:
        if ev.data.strip() == "[DONE]":
            continue
        obj, err = _loads(ev.data)
        if err or not isinstance(obj, dict):
            rep.violations.append("data_not_json_object")
            continue
        if "error" in obj and "choices" not in obj:
            rep.error = obj.get("error") if isinstance(obj.get("error"), dict) else {"message": str(obj["error"])}
            continue
        if obj.get("usage"):
            u = obj["usage"]
            ints = all(isinstance(u.get(k), int) and u.get(k) >= 0
                       for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
            if not ints:
                rep.violations.append("usage_not_ints")
            elif u["total_tokens"] != u["prompt_tokens"] + u["completion_tokens"]:
                rep.violations.append("usage_sum_mismatch")
            rep.usage = u
        for ch in obj.get("choices", []) or []:
            delta = ch.get("delta") or {}
            if delta.get("content"):
                rep.content += delta["content"]
                if finish_seen:
                    after_finish_content = True
            if delta.get("reasoning_content"):
                rep.thoughts += delta["reasoning_content"]
            for tc in delta.get("tool_calls") or []:
                idx = tc.get("index")
                if not isinstance(idx, int) or isinstance(idx, bool):
                    rep.violations.append("tool_call_index_not_int")
                    continue
                slot = tool_acc.setdefault(idx, {"id": None, "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
            if ch.get("finish_reason"):
                finish_seen += 1
                rep.finish_reason = ch["finish_reason"]
    if not data:
        rep.violations.append("no_data_events")
    if len(done_idx) != 1:
        rep.violations.append(f"done_count_{len(done_idx)}")
    elif done_idx[0] != len(data) - 1:
        rep.violations.append("data_after_done")
    if rep.error is None:
        if finish_seen == 0:
            rep.violations.append("no_finish_reason")
        elif finish_seen > 1:
            rep.violations.append("finish_reason_repeated")
    if after_finish_content:
        rep.violations.append("content_after_finish")
    if tool_acc:
        idxs = sorted(tool_acc)
        if idxs != list(range(len(idxs))):
            rep.violations.append("tool_call_index_gap")
        for idx in idxs:
            slot = tool_acc[idx]
            if not slot["name"]:
                rep.violations.append("tool_call_missing_name")
            if rep.finish_reason == "tool_calls":
                _, jerr = _loads(slot["arguments"] or "{}")
                if jerr:
                    rep.violations.append("tool_call_arguments_invalid_json")
            rep.tool_calls.append(slot)
        if rep.finish_reason not in ("tool_calls", None) and rep.error is None:
            rep.violations.append("tool_calls_with_wrong_finish_reason")
    return rep


def check_responses(text: str) -> StreamReport:
    """Validate an OpenAI Responses (Codex) SSE body: typed events, one terminal event, last."""
    p = parse_sse(text)
    rep = StreamReport("responses", violations=list(p.violations))
    rep.n_comments = len(p.comments)
    data = p.data_events
    rep.n_data = len(data)
    terminal = 0
    types: list[str] = []
    for ev in data:
        if ev.data.strip() == "[DONE]":
            rep.saw_done = True
            continue
        obj, err = _loads(ev.data)
        if err or not isinstance(obj, dict):
            rep.violations.append("data_not_json_object")
            continue
        t = obj.get("type") or ev.event
        if ev.event and obj.get("type") and ev.event != obj.get("type"):
            rep.violations.append("event_name_type_mismatch")
        types.append(t)
        if t == "response.output_text.delta":
            rep.content += obj.get("delta", "")
        elif t in ("response.completed", "response.failed", "response.incomplete"):
            terminal += 1
            resp = obj.get("response") or {}
            if t == "response.failed":
                rep.error = resp.get("error") or {"message": "response.failed"}
            if resp.get("usage"):
                rep.usage = resp["usage"]
        elif t == "error":
            rep.error = obj.get("error") or {"message": str(obj)[:100]}
            terminal += 1
        elif t == "response.output_item.done":
            item = obj.get("item") or {}
            if item.get("type") == "function_call":
                _, jerr = _loads(item.get("arguments") or "{}")
                if jerr:
                    rep.violations.append("function_call_arguments_invalid_json")
                rep.tool_calls.append({"id": item.get("call_id"), "name": item.get("name"),
                                       "arguments": item.get("arguments", "")})
    if types and types[0] != "response.created":
        rep.violations.append("first_event_not_response_created")
    if terminal != 1:
        rep.violations.append(f"terminal_count_{terminal}")
    elif types and types[-1] not in ("response.completed", "response.failed", "response.incomplete", "error"):
        rep.violations.append("events_after_terminal")
    return rep


def check_anthropic(text: str) -> StreamReport:
    """Validate an Anthropic messages SSE body (message_start .. message_stop, balanced blocks)."""
    p = parse_sse(text)
    rep = StreamReport("anthropic", violations=list(p.violations))
    rep.n_comments = len(p.comments)
    data = p.data_events
    rep.n_data = len(data)
    types: list[str] = []
    open_blocks: dict[int, str] = {}
    stops = 0
    tool_args: dict[int, str] = {}
    tool_meta: dict[int, dict] = {}
    for ev in data:
        obj, err = _loads(ev.data)
        if err or not isinstance(obj, dict):
            rep.violations.append("data_not_json_object")
            continue
        t = obj.get("type") or ev.event
        if ev.event and ev.event != t:
            rep.violations.append("event_name_type_mismatch")
        types.append(t)
        if t == "content_block_start":
            idx = obj.get("index")
            if idx in open_blocks:
                rep.violations.append("block_started_twice")
            cb = obj.get("content_block") or {}
            open_blocks[idx] = cb.get("type", "")
            if cb.get("type") == "tool_use":
                tool_meta[idx] = {"id": cb.get("id"), "name": cb.get("name")}
                tool_args[idx] = ""
        elif t == "content_block_delta":
            idx = obj.get("index")
            if idx not in open_blocks:
                rep.violations.append("delta_for_unopened_block")
            d = obj.get("delta") or {}
            if d.get("type") == "text_delta":
                rep.content += d.get("text", "")
            elif d.get("type") == "thinking_delta":
                rep.thoughts += d.get("thinking", "")
            elif d.get("type") == "input_json_delta" and idx in tool_args:
                tool_args[idx] += d.get("partial_json", "")
        elif t == "content_block_stop":
            idx = obj.get("index")
            if idx not in open_blocks:
                rep.violations.append("stop_for_unopened_block")
            else:
                open_blocks.pop(idx)
        elif t == "message_delta":
            sr = (obj.get("delta") or {}).get("stop_reason")
            if sr:
                rep.finish_reason = sr
            if obj.get("usage"):
                rep.usage = obj["usage"]
        elif t == "message_stop":
            stops += 1
        elif t == "error":
            rep.error = obj.get("error") or {"message": "error"}
    if types and types[0] != "message_start":
        rep.violations.append("first_event_not_message_start")
    if stops != 1 and rep.error is None:
        rep.violations.append(f"message_stop_count_{stops}")
    if types and rep.error is None and types[-1] != "message_stop":
        rep.violations.append("events_after_message_stop")
    if open_blocks and rep.error is None:
        rep.violations.append("unclosed_content_block")
    for idx, args in tool_args.items():
        _, jerr = _loads(args or "{}")
        if jerr:
            rep.violations.append("tool_use_input_invalid_json")
        rep.tool_calls.append({**tool_meta[idx], "arguments": args})
    return rep
