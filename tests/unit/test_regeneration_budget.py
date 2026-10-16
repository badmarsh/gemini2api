"""Regression tests for the single per-turn tool regeneration budget."""
import asyncio

from app.core import agent_loop
from app.core.regeneration_budget import RegenerationBudget, reset_stats, stats
from app.utils.tools import MALFORMED_TOOL_NOTICE, parse_tool_response_with_retry


TRUNCATED = '{"status":"tool_use","tool_calls":[{"name":"exec","arguments":{"input":"const x = 1'
VALID = '{"status":"tool_use","tool_calls":[{"name":"exec","arguments":{"input":"ls"}}]}'


def run(coro):
    return asyncio.run(coro)


def test_guard_and_parser_share_one_token():
    budget = RegenerationBudget()
    calls = []

    async def regenerate(nudge=""):
        calls.append(nudge)
        if nudge:
            return "I'll inspect the files later."
        return VALID

    text, corrected = run(agent_loop.guard_tool_loop("I'll inspect the files.", regenerate, budget=budget))
    assert corrected is False
    assert len(calls) == 1

    parsed = run(parse_tool_response_with_retry(TRUNCATED, regenerate, budget=budget))
    assert parsed == {"type": "text", "content": MALFORMED_TOOL_NOTICE}
    assert len(calls) == 1, "parser retry must be blocked after the guard spent the turn budget"
    assert budget.snapshot() == {
        "limit": 1, "used": 1, "remaining": 0, "blocked": 1, "last_reason": "parse_retry",
    }


def test_parser_can_use_the_token_when_guard_did_not_fire():
    budget = RegenerationBudget()
    calls = []

    async def regenerate():
        calls.append(True)
        return VALID

    parsed = run(parse_tool_response_with_retry(TRUNCATED, regenerate, budget=budget))
    assert parsed["type"] == "tool_calls"
    assert len(calls) == 1
    assert budget.remaining == 0


def test_process_metrics_record_allowed_and_blocked_regenerations():
    reset_stats()
    budget = RegenerationBudget()
    assert budget.try_consume("guard") is True
    assert budget.try_consume("parse_retry") is False
    snapshot = stats()
    assert snapshot["allowed"] == 1
    assert snapshot["blocked"] == 1
    assert snapshot["by_reason"] == {"guard": 1}
    reset_stats()
