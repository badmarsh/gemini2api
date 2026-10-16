"""Agent-loop guard: stops a coding agent from falling asleep mid-task.

Gemini Web is trained as a chat assistant. Inside a tool loop (Codex, Cline, OpenCode, OpenHands...) that
shows up as the "conversational promise" stall: the model answers ``I'll inspect the files now and let you
know`` instead of emitting a tool_call. The harness only sees text, concludes the assistant is done and goes
idle -- the user has to nudge it by hand, every single round.

Two defences, both inert when the request declares no tools:

1. ``build_tool_prompt`` appends the reminder to the *end* of the flattened prompt (a prefix-only
   instruction is what the model forgets first in long turns). See ``app/utils/tools.py``.
2. ``guard_tool_loop`` catches the residual stall -- a reply that parses as plain text yet reads as a
   promise of future work -- and asks the model once more with the corrective nudge appended. One retry,
   never a loop; the first reply is kept if the retry does not produce a tool_call either.

The guard is deliberately conservative: it never fires on a reply that contains a code fence, on a reply
that already parses as a tool call, or on text longer than ``_MAX_PROMISE_CHARS`` (long answers are work
product, not promises). Every correction is logged and counted so ``/admin/status`` can show how often the
loop needed rescuing.
"""
from __future__ import annotations

import logging
import re
import threading

logger = logging.getLogger(__name__)

# Phrases that mean "I am about to do it" rather than "I did it". Multi-language because the end user here
# talks to the model in Slovak/English and the model mirrors the conversation language.
_PROMISE_PATTERNS = (
    # English
    r"\bi(?:'ll| will) (?:now |immediately |first )?(?:inspect|look|check|read|start|begin|proceed|review|analyse|analyze|implement|create|build|update|write|run|continue|handle)",
    r"\bi(?:'m| am) (?:now )?(?:going to|about to|starting to)",
    r"\blet me (?:start|begin|first|now|check|look|inspect|read|walk)",
    r"\bworking on (?:it|this|that) now",
    r"\bi(?:'m| am) working on\b",
    r"\b(?:will|i(?:'ll| will)) (?:report|let you know|keep you posted|update you)\b",
    r"\b(?:starting|beginning) (?:the )?(?:implementation|work|task|process)\b",
    r"\bnext steps? (?:are|will be|:)\b",
    r"\bgive me a (?:moment|minute|second)\b",
    r"\bstanding by\b",
    # Slovak / Czech (the model mirrors the language of the conversation)
    r"\bpracujem na (?:tom|ňom|tomto)\b",
    r"\bidem (?:na to|sa do toho|to spraviť|to urobiť|pokračovať)\b",
    r"\bzačínam\b|\bzacnem\b|\bzačnem\b|\bspustil som realizáciu\b|\bspustam realizaciu\b",
    r"\bdám (?:ti|vám) vedieť\b|\bdam (?:ti|vam) vediet\b",
    r"\bbudem (?:ťa|vás|ta|vas) informovať\b|\bbudem informovat\b",
    r"\bpripravujem\b|\bchystám sa\b|\bchystam sa\b",
    r"\bpokračujem\b|\bpokracujem\b",
)

_PROMISE_RE = re.compile("|".join(_PROMISE_PATTERNS), re.IGNORECASE)
# A long reply is work product (an answer, a plan, code), not a stall. Only short promise-shaped replies
# are worth one corrective round trip.
_MAX_PROMISE_CHARS = 1200

NUDGE = (
    "\n\n[MANDATORY CORRECTION] Your previous message did not contain a tool_call — it only described what "
    "you were about to do. There is no background worker and no progress to report: only a tool_call does "
    "the work. Do NOT restate intent, do NOT announce a plan, do NOT promise to report back. Emit the "
    "tool_call now, in this message, as the single JSON object defined above."
)

_lock = threading.Lock()
_stalls_seen = 0        # promise-shaped replies detected
_stalls_corrected = 0   # of those, how many the retry turned into a tool_call


def stats() -> dict:
    with _lock:
        return {"detected": _stalls_seen, "corrected": _stalls_corrected}


def reset_stats() -> None:
    global _stalls_seen, _stalls_corrected
    with _lock:
        _stalls_seen = 0
        _stalls_corrected = 0


def looks_like_stall(text: str) -> bool:
    """True when the reply reads as a promise of future work, i.e. an agent-loop stall.

    Conservative by construction: code fences, a tool-call payload, long answers and plain questions are
    all ignored (a request for clarification is a legitimate turn end).
    """
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped or len(stripped) > _MAX_PROMISE_CHARS:
        return False
    if "```" in stripped:
        return False
    if '"tool_calls"' in stripped or '"tool_use"' in stripped:
        return False
    return bool(_PROMISE_RE.search(stripped))


async def guard_tool_loop(text: str, regenerate, *, enabled: bool = True, budget=None) -> tuple[str, bool]:
    """Return ``(text, corrected)``, asking the model once more when it stalled on a promise.

    ``regenerate(nudge)`` must re-run the upstream call with ``nudge`` appended to the prompt. Any failure
    (exception, empty reply, still no tool call) keeps the original text: this guard may never turn a
    graceful answer into an error. Disabled, or nothing to correct → the input is returned untouched.
    ``budget`` is shared with the malformed-JSON parser so the two safeguards allow only one regeneration
    per logical turn.
    """
    global _stalls_seen, _stalls_corrected
    if not enabled or regenerate is None or not looks_like_stall(text):
        return text, False

    if budget is None:
        from app.core.regeneration_budget import RegenerationBudget
        budget = RegenerationBudget()
    if not budget.try_consume("guard"):
        logger.warning("Agent-loop correction skipped: the per-turn regeneration budget is exhausted")
        return text, False

    with _lock:
        _stalls_seen += 1
    logger.warning(f"Agent-loop stall detected (a promise, no tool_call): {text.strip()[:120]!r} "
                   f"— regenerating once with the tool-loop correction")

    try:
        retry_text = await regenerate(NUDGE)
    except Exception as e:  # noqa: BLE001 - the guard must never break the reply path
        logger.warning(f"Tool-loop correction failed, keeping the first reply: {type(e).__name__}: {e}")
        return text, False

    if not isinstance(retry_text, str) or not retry_text.strip():
        return text, False
    if not _has_tool_payload(retry_text):
        logger.warning("Tool-loop correction did not produce a tool_call either; keeping the first reply")
        return text, False

    with _lock:
        _stalls_corrected += 1
    logger.info("Tool-loop correction produced a tool_call; using it")
    return retry_text, True


def _has_tool_payload(text: str) -> bool:
    """Cheap structural check that the corrected reply carries a tool call (parsing happens later)."""
    stripped = text.strip()
    return ('"tool_calls"' in stripped or '"tool_use"' in stripped
            or ('"name"' in stripped and '"arguments"' in stripped))
