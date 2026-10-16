"""Ready-made upstream behaviours (all times are virtual seconds, see ``session.FakeSession``)."""
from __future__ import annotations

from .frames import XSSI_PREFIX, frame_chunk, ok_body, split_bytes, wrb_elem
from .session import Chunk, FakeConnectionReset, Hang, Raise, Scenario, Sleep

WEB_ERROR_TEXT = (
    "I can't use Google Docs because required Gmail settings are off. Turn on these settings to continue."
)


def ok(text: str = "pong", *, n_frames: int = 3, thoughts: str = "", ttft: float = 0.0, gap: float = 0.0,
       splits: int = 1, conv: str = "c_fake0001", name: str = "ok") -> Scenario:
    """Normal reply: ``ttft`` virtual seconds of silence, then cumulative frames spaced ``gap`` apart."""
    steps: list = [Sleep(ttft)] if ttft else []
    for wire in ok_body(text, n_frames=n_frames, conv=conv, thoughts=thoughts):
        for part in split_bytes(wire, splits):
            steps.append(Chunk(part))
        if gap:
            steps.append(Sleep(gap))
    return Scenario(name, steps, kind="ok")


def empty(name: str = "empty") -> Scenario:
    """HTTP 200 whose only frame has an empty reply text."""
    wire = XSSI_PREFIX + frame_chunk([wrb_elem("")])
    return Scenario(name, [Chunk(wire.encode())], kind="empty")


def no_frames(body: str = "<html>nothing useful</html>", name: str = "no_frames") -> Scenario:
    """HTTP 200 with a body that contains no wrb frames at all."""
    return Scenario(name, [Chunk(body.encode())], kind="empty")


def web_error(text: str = WEB_ERROR_TEXT, name: str = "web_error") -> Scenario:
    """Google's template error reply delivered as if it were the model's answer."""
    return Scenario(name, [Chunk(w.encode()) for w in ok_body(text, n_frames=1)], kind="web_error")


def http_error(status: int, body: str = "", name: str | None = None) -> Scenario:
    return Scenario(name or f"http_{status}", [], status=status, status_body=body or f"error {status}",
                    kind="http_error")


def captcha(name: str = "captcha") -> Scenario:
    body = "<html><body>Our systems have detected unusual traffic from your computer network.</body></html>"
    return http_error(429, body, name)


def cookie_expired_redirect(name: str = "cookie_expired") -> Scenario:
    """200 HTML sign-in page (what an expired session redirect looks like once followed): no frames."""
    body = '<html><head><title>Sign in</title></head><body><a href="https://accounts.google.com/ServiceLogin">x</a></body></html>'
    return Scenario(name, [Chunk(body.encode())], kind="cookie_invalid")


def hang_before_first_frame(name: str = "hang_before_first") -> Scenario:
    """Silence until the client gives up (stream chunk timeout / request timeout)."""
    return Scenario(name, [Hang()], kind="timeout")


def hang_after_first_frame(text: str = "partial answer", name: str = "hang_after_first") -> Scenario:
    wire = ok_body(text, n_frames=1)[0]
    return Scenario(name, [Chunk(wire.encode()), Hang()], kind="timeout")


def silent_gap(text: str = "late answer", *, gap: float, name: str = "silent_gap") -> Scenario:
    """First frame immediately, then ``gap`` virtual seconds of silence, then the rest."""
    wires = ok_body(text, n_frames=3)
    steps: list = [Chunk(wires[0].encode()), Sleep(gap)]
    steps += [Chunk(w.encode()) for w in wires[1:]]
    return Scenario(name, steps, kind="ok")


def disconnect_mid_stream(text: str = "cut off in the mid", name: str = "disconnect") -> Scenario:
    wires = ok_body(text, n_frames=4)
    steps: list = [Chunk(w.encode()) for w in wires[:2]] + [Raise(FakeConnectionReset("connection reset"))]
    return Scenario(name, steps, kind="disconnect")


def malformed_frame(name: str = "malformed_frame") -> Scenario:
    """A frame whose JSON string contains a raw newline (invalid strict JSON)."""
    wire = XSSI_PREFIX + frame_chunk([wrb_elem("line1\nline2", raw_newline_in_string=True)])
    return Scenario(name, [Chunk(wire.encode())], kind="malformed")


def truncated_frame(text: str = "this frame never closes", name: str = "truncated_frame") -> Scenario:
    wire = ok_body(text, n_frames=1)[0]
    return Scenario(name, [Chunk(wire[: len(wire) // 2].encode())], kind="malformed")


def tool_call_text(calls: list[tuple[str, dict]], *, raw_newlines: bool = False) -> str:
    """Model text in the prompt-simulated tool protocol: ``{"status":"tool_use","tool_calls":[...]}``.

    ``raw_newlines=True`` turns the JSON escape ``\\n`` inside string values into REAL newlines, the unescaped
    control-character quirk Argon produced (decoded by ``json.loads(strict=False)`` in app/utils/tools.py).
    """
    import json

    body = json.dumps(
        {"status": "tool_use", "tool_calls": [{"name": n, "arguments": a} for n, a in calls]},
        ensure_ascii=False,
    )
    if raw_newlines:
        body = body.replace("\\n", "\n")
    return body


def text_envelope(content: str) -> str:
    """Model text for a plain reply in the simulated protocol: ``{"status":"text","content":...}``."""
    import json

    return json.dumps({"status": "text", "content": content}, ensure_ascii=False)
