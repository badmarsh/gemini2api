"""Regression guards for seq=12 image-gen network-error fix (SSE keepalive + download tuning).

Static source checks — no runtime deps required.
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_buffered_stream_emits_immediate_sse_and_keepalive():
    """seq=12/s1: buffered path must not block with zero SSE bytes during generate()."""
    src = (_REPO_ROOT / "app" / "routers" / "openai.py").read_text(encoding="utf-8")
    assert "_sse_keepalive_during" in src
    assert 'yield ": ping\\n\\n"' in src  # literal ping frame still emitted by _sse_stream_chunks
    # First role frame before awaiting generate result
    assert "async def _stream_response_buffered" in src
    idx_fn = src.index("async def _stream_response_buffered")
    body = src[idx_fn : idx_fn + 3500]
    assert "StreamDelta(role=\"assistant\")" in body
    # Keepalive while the upstream generate() runs. The call is wrapped in the local _keepalive_task()
    # helper (which itself pumps _sse_keepalive_during and cancels the task on client disconnect), so the
    # invariant to guard is "the generate task is driven through the keepalive helper".
    assert "async for ping in _keepalive_task(gen_task)" in body
    assert "_sse_keepalive_during(task)" in body


def test_image_download_uses_s2048_and_short_timeout():
    """seq=12/s2: =s0 full-res replaced; per-GET timeout converged to ~25s."""
    src = (_REPO_ROOT / "app" / "core" / "gemini_client.py").read_text(encoding="utf-8")
    assert "=s2048" in src
    assert "_IMAGE_DOWNLOAD_TIMEOUT" in src
    assert "_IMAGE_DOWNLOAD_TIMEOUT" in src and "timeout=timeout" in src
    assert "=s512" in src  # fallback tier
    assert "_build_image_entry" in src
    assert '"fallback": True' in src


def test_image_generate_uses_extended_post_timeout():
    """pro 生图常 >60s：生图意图须用更长 POST 超时，避免 flash 正常而 pro 断连。"""
    src = (_REPO_ROOT / "app" / "core" / "gemini_client.py").read_text(encoding="utf-8")
    assert "_GENERATE_TIMEOUT_IMAGE" in src
    assert "_GENERATE_TIMEOUT_IMAGE" in src and "maybe_image_generation_intent" in src
    assert "timeout=gen_timeout" in src


def test_images_md_handles_download_fallback():
    """seq=12/s2: download failure must not silently drop images."""
    src = (_REPO_ROOT / "app" / "routers" / "openai.py").read_text(encoding="utf-8")
    assert 'im.get("fallback")' in src
