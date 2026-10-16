"""is_blank_answer(): an answer with no text, no images and no reasoning (the cue to answer with an error)."""
from app.core.fallback import EMPTY_RESPONSE_MESSAGE, is_blank_answer


def test_blank_variants_are_blank():
    for result in (None, {}, {"text": ""}, {"text": "  \n "}, {"text": "", "images": [], "thoughts": ""},
                   {"text": "", "thoughts": "   "}, "not a dict"):
        assert is_blank_answer(result), result


def test_any_payload_is_not_blank():
    assert not is_blank_answer({"text": "hello"})
    assert not is_blank_answer({"text": "", "images": [{"id": "x"}]})
    assert not is_blank_answer({"text": "", "thoughts": "the model reasoned but said nothing"})


def test_message_is_a_plain_non_empty_string():
    assert isinstance(EMPTY_RESPONSE_MESSAGE, str) and EMPTY_RESPONSE_MESSAGE.strip()
