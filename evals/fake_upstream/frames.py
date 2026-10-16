"""Builders for Google StreamGenerate response frames (synthetic, no real data).

Wire shape mirrors what gemini_client parses: an XSSI prefix, then length-prefixed JSON arrays whose
elements look like ``["wrb.fr", null, "<json payload>", ...]``. The payload carries the conversation id at
``payload[1]``, the cumulative reply text at ``payload[4][0][1][0]`` and the thinking text at
``payload[4][0][37][0][0]``.
"""
from __future__ import annotations

import json

XSSI_PREFIX = ")]}'\n\n"


def wrb_elem(text: str | None, conv: str = "c_fake0001", thoughts: str = "", rcid: str = "rc_fake0001",
             raw_newline_in_string: bool = False) -> str:
    """One ``["wrb.fr", ...]`` element as a JSON string.

    ``text=None`` builds a routing/metadata frame without candidates (the client must skip it).
    ``raw_newline_in_string`` deliberately emits a literal newline inside the JSON string, which is invalid
    strict JSON (a malformed-frame fault, not something Google is known to send).
    """
    if text is None:
        payload = [None, conv, None, None, None]
    else:
        cand: list = [rcid, [text]] + [None] * 35
        cand.append([[thoughts]] if thoughts else None)
        payload = [None, conv, None, None, [cand]]
    inner = json.dumps(payload, ensure_ascii=False)
    elem = json.dumps(["wrb.fr", None, inner, None, None, None, "generic"], ensure_ascii=False)
    if raw_newline_in_string:
        elem = elem.replace("\\n", "\n", 1)
    return elem


def frame_chunk(elems: list[str]) -> str:
    """Length-prefixed chunk containing the given elements (the length value is informational only)."""
    body = "[" + ",".join(elems) + "]"
    return f"{len(body)}\n{body}\n"


def cumulative_texts(text: str, n_frames: int) -> list[str]:
    """Growing prefixes of ``text`` (Gemini frames are cumulative, not incremental)."""
    n_frames = max(1, n_frames)
    if len(text) <= n_frames:
        return [text[: i + 1] for i in range(len(text))] or [""]
    step = len(text) / n_frames
    cuts = sorted({max(1, round(step * (i + 1))) for i in range(n_frames)} | {len(text)})
    return [text[:c] for c in cuts]


def ok_body(text: str, n_frames: int = 3, conv: str = "c_fake0001", thoughts: str = "") -> list[str]:
    """Full response as a list of wire chunks (XSSI prefix in the first one)."""
    chunks = []
    for i, t in enumerate(cumulative_texts(text, n_frames)):
        th = thoughts if i == len(cumulative_texts(text, n_frames)) - 1 else ""
        chunks.append(frame_chunk([wrb_elem(t, conv=conv, thoughts=th)]))
    if chunks:
        chunks[0] = XSSI_PREFIX + chunks[0]
    return chunks


def split_bytes(data: str, parts: int) -> list[bytes]:
    """Split a wire string at arbitrary byte boundaries (frames may straddle chunks)."""
    raw = data.encode("utf-8")
    parts = max(1, parts)
    size = max(1, len(raw) // parts)
    return [raw[i:i + size] for i in range(0, len(raw), size)]
