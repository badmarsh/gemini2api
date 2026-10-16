"""Vision: images sent as OpenAI image_url (data URI) and Anthropic image/base64 blocks."""
import pytest

import e2e_helpers as h

pytestmark = pytest.mark.live

RED, GREEN, BLUE = (220, 20, 20), (20, 200, 20), (20, 20, 220)


def with_image(png: bytes, question: str) -> list[dict]:
    return [{"role": "user", "content": [
        {"type": "text", "text": question},
        {"type": "image_url", "image_url": {"url": h.data_uri(png)}},
    ]}]


@pytest.mark.core
def test_solid_color_image(client):
    msgs = with_image(h.png_solid(GREEN), "What single color fills this image? Answer with one word.")
    assert "green" in h.text_of(h.chat(client, msgs)).lower()


def test_two_images_in_one_message(client):
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "Image 1 and image 2 are each a single solid color. Name both colors, in order."},
        {"type": "image_url", "image_url": {"url": h.data_uri(h.png_solid(RED))}},
        {"type": "image_url", "image_url": {"url": h.data_uri(h.png_solid(BLUE))}},
    ]}]
    text = h.text_of(h.chat(client, msgs)).lower()
    assert "red" in text and "blue" in text
    assert text.index("red") < text.index("blue")


def test_stripe_order(client):
    png = h.png_stripes([RED, GREEN, BLUE])
    msgs = with_image(png, "This image has three horizontal stripes. List their colors from top to bottom.")
    text = h.text_of(h.chat(client, msgs)).lower()
    assert text.index("red") < text.index("green") < text.index("blue")


def test_vision_streaming(client):
    msgs = with_image(h.png_solid(BLUE), "What single color fills this image? Answer with one word.")
    res = h.chat_stream(client, msgs)
    assert "blue" in res.text.lower() and res.saw_done


def test_vision_followup_in_same_conversation(client):
    first = h.chat(client, with_image(h.png_solid(RED), "Remember the color of this image. Say 'ok'."))
    conv = first.get("conversation_id")
    assert conv
    second = h.chat(client, [{"role": "user", "content": "Which color was the image? One word."}],
                    conversation_id=conv)
    assert "red" in h.text_of(second).lower()


@pytest.mark.core
def test_anthropic_image_block(client):
    body = {"messages": [{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": h.b64(h.png_solid(GREEN))}},
        {"type": "text", "text": "What single color fills this image? Answer with one word."},
    ]}]}
    assert "green" in h.claude_text(h.claude(client, body)).lower()


def test_invalid_image_does_not_crash(client):
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "Say 'pong'."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,not-valid-base64!!"}},
    ]}]
    r = client.post("/v1/chat/completions", json={"model": h.MODEL, "messages": msgs})
    assert r.status_code != 500, r.text[:500]
