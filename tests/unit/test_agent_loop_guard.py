"""Agent-loop guard (app/core/agent_loop.py).

The failure this covers: inside a coding harness the model answers with a *promise* ("I'll inspect the files
now and let you know") instead of a tool_call. The harness only sees text, assumes the assistant is done and
goes idle -- the user has to nudge it by hand, every round.

Contract under test:
- a promise-shaped reply is detected (EN and SK), everything else is left alone;
- detection is conservative: code fences, tool payloads, long answers, empty text and plain questions are not
  stalls;
- the correction asks the model exactly once, and only the corrected text that actually carries a tool call
  replaces the original;
- a failing regenerator never turns a graceful reply into an error.
"""
import asyncio

from app.core import agent_loop


def _run(coro):
    return asyncio.run(coro)


# ---- looks_like_stall -----------------------------------------------------------------------------------

def test_promise_replies_are_stalls():
    for text in (
        "I'll inspect the files and let you know.",
        "I am working on it now.",
        "Let me check the repository structure first.",
        "I will report back once the implementation is done.",
        "Give me a moment to review the code.",
        "Pracujem na tom, dám ti vedieť.",
        "Idem na to a spustil som realizáciu.",
        "Začínam implementáciou Canvas enginu.",
        "Budem ťa informovať po nasadení.",
    ):
        assert agent_loop.looks_like_stall(text), text


def test_real_answers_are_not_stalls():
    for text in (
        "",
        "   ",
        "Done. The file static/app/canvas-engine.js now contains the sandbox.",
        "Tests pass: 80 passed in 4.2s.",
        "Which directory should the engine live in?",
        '{"status": "tool_use", "tool_calls": [{"name": "read_file", "arguments": {"path": "a.py"}}]}',
        "```python\nprint('I will not be retried')\n```",
        "I'll inspect " * 200,          # long answer = work product, not a stall
    ):
        assert not agent_loop.looks_like_stall(text), text


# ---- guard_tool_loop ------------------------------------------------------------------------------------

def test_guard_retries_once_and_uses_the_tool_call():
    calls = []

    async def regenerate(nudge):
        calls.append(nudge)
        return '{"status": "tool_use", "tool_calls": [{"name": "ls", "arguments": {}}]}'

    text, corrected = _run(agent_loop.guard_tool_loop("I'll start by inspecting the repo.", regenerate))
    assert corrected is True
    assert len(calls) == 1, "the guard must never retry more than once"
    assert "tool_call" in calls[0], "the corrective nudge must be what is passed to the regenerator"
    assert '"tool_calls"' in text


def test_guard_keeps_the_first_reply_when_the_retry_still_promises():
    async def regenerate(nudge):
        return "Let me check the files and I'll report back."

    text, corrected = _run(agent_loop.guard_tool_loop("I'll inspect the files.", regenerate))
    assert corrected is False
    assert text == "I'll inspect the files."


def test_guard_keeps_the_first_reply_when_the_retry_fails():
    async def regenerate(nudge):
        raise RuntimeError("upstream exploded")

    text, corrected = _run(agent_loop.guard_tool_loop("I'll inspect the files.", regenerate))
    assert corrected is False
    assert text == "I'll inspect the files."


def test_guard_is_inert_for_non_stalls_and_when_disabled():
    calls = []

    async def regenerate(nudge):
        calls.append(nudge)
        return "never used"

    # a plain answer: nothing to correct
    text, corrected = _run(agent_loop.guard_tool_loop("All tests pass.", regenerate))
    assert (text, corrected) == ("All tests pass.", False)
    # disabled by setting: not even the detection runs
    text, corrected = _run(agent_loop.guard_tool_loop("I'll inspect the files.", regenerate, enabled=False))
    assert (text, corrected) == ("I'll inspect the files.", False)
    assert calls == []


def test_stats_count_detections_and_corrections():
    agent_loop.reset_stats()

    async def good(nudge):
        return '{"status": "tool_use", "tool_calls": [{"name": "ls", "arguments": {}}]}'

    async def bad(nudge):
        return "I'll do it later."

    _run(agent_loop.guard_tool_loop("I'll inspect the files.", good))
    _run(agent_loop.guard_tool_loop("Pracujem na tom.", bad))
    _run(agent_loop.guard_tool_loop("Nothing to see here.", good))

    assert agent_loop.stats() == {"detected": 2, "corrected": 1}
    agent_loop.reset_stats()
    assert agent_loop.stats() == {"detected": 0, "corrected": 0}
