"""Wire-frame JSON uses the same non-strict control-character policy as tool parsing."""

from app.core.gemini_client import _extract_text_from_wrb


def test_wire_payload_with_literal_newline_is_parsed():
    payload = '[null,"conversation",null,null,[["candidate",["line one\nline two"]]] ]'
    text, conversation_id, thoughts = _extract_text_from_wrb(["wrb.fr", None, payload])

    assert text == "line one\nline two"
    assert conversation_id == "conversation"
    assert thoughts == ""
