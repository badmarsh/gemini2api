"""Reasoning / thinking behaviour of Argon (a thinking model) via reasoning_effort and reasoning_content."""
import pytest

import e2e_helpers as h

pytestmark = pytest.mark.live

PUZZLE = ("A farmer has 17 sheep. All but 9 run away, then he buys 14 more and sells 6. "
          "How many sheep does he have now? Answer with the final number only.")  # 9 + 14 - 6 = 17
MULT = "What is 17 * 23 + 58? Think it through, then answer with the number only."  # 391 + 58 = 449


def u(text):
    return [{"role": "user", "content": text}]


@pytest.mark.core
def test_reasoning_effort_high_solves_multistep(client):
    assert "449" in h.text_of(h.chat(client, u(MULT), reasoning_effort="high"))


def test_trick_question(client):
    assert "17" in h.text_of(h.chat(client, u(PUZZLE), reasoning_effort="high"))


def test_reasoning_effort_none_still_answers(client):
    resp = h.chat(client, u("What is 6 * 7? Answer with the number only."), reasoning_effort="none")
    assert "42" in h.text_of(resp)


def test_stream_with_reasoning_delivers_final_answer(client):
    res = h.chat_stream(client, u(MULT), reasoning_effort="high")
    assert "449" in res.text
    assert res.saw_done and res.finish_reason == "stop"


@pytest.mark.xfail(strict=False, reason="thoughts depend on the upstream returning them; XPASS = it works")
def test_non_stream_exposes_reasoning_content(client):
    resp = h.chat(client, u(MULT), reasoning_effort="high")
    thoughts = h.message(resp).get("reasoning_content")
    assert isinstance(thoughts, str) and thoughts.strip()


@pytest.mark.xfail(strict=False, reason="thoughts depend on the upstream returning them; XPASS = it works")
def test_stream_exposes_reasoning_content(client):
    res = h.chat_stream(client, u(MULT), reasoning_effort="high")
    assert res.reasoning.strip()
