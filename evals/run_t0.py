"""T0 (offline) Argon situation matrix. Records observed behaviour; it asserts nothing about what is "right".

Run from the repo root:  py -3.14 -m evals.run_t0 [--out evals/baselines/t0_argon.json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import httpx

from evals.fake_upstream import scenarios as sc
from evals.harness import InProcessSUT
from evals.runner import RequestSpec, run_request

MODEL = "gemini-4-argon"
FALLBACK_OK = lambda: sc.ok("flash fallback reply", n_frames=3)  # noqa: E731

# name -> factory producing the scenario served for every Argon call
CASES = {
    "ok": lambda: sc.ok("argon reply", n_frames=4, thoughts="some thinking"),
    "silent_gap_200s": lambda: sc.silent_gap("late argon reply", gap=200),
    "hang_before_first": sc.hang_before_first_frame,
    "hang_after_first": sc.hang_after_first_frame,
    "empty_reply": sc.empty,
    "no_frames": sc.no_frames,
    "web_error_text": sc.web_error,
    "http_429_captcha": sc.captcha,
    "http_500": lambda: sc.http_error(500, "internal"),
    "cookie_expired": sc.cookie_expired_redirect,
    "malformed_frame": sc.malformed_frame,
    "truncated_frame": sc.truncated_frame,
    "disconnect_mid_stream": sc.disconnect_mid_stream,
}


def _spec(stream: bool, label: str) -> RequestSpec:
    body = {"model": MODEL, "stream": stream, "messages": [{"role": "user", "content": "say hello"}]}
    return RequestSpec("/v1/chat/completions", body, stream=stream, timeout_s=30, label=label)


async def run_case(name: str, stream: bool, fallback: bool) -> dict:
    async with InProcessSUT(accounts=2) as sut:
        sut.upstream.default("argon", CASES[name]())
        sut.upstream.default("other", FALLBACK_OK())
        async with httpx.AsyncClient(base_url=sut.base_url, headers=sut.auth(), timeout=60) as client:
            with sut.settings(argon_fallback_to_flash=fallback):
                rec = await run_request(client, _spec(stream, f"{name}|{'stream' if stream else 'nonstream'}"))
        await asyncio.sleep(0.05)  # let server-side cleanup run before counting slots
        return {
            "case": name, "stream": stream, "fallback_enabled": fallback,
            "status": rec["status"], "error_class": rec["error_class"], "violations": rec["violations"],
            "content": rec["content"][:120], "finish_reason": rec["finish_reason"],
            "n_comments": rec["n_comments"], "total_s": round(rec["total_s"] or 0, 3),
            "argon_calls": len(sut.upstream.calls_for("argon")), "other_calls": len(sut.upstream.calls_for("other")),
            "leaked_slots": sut.active_slots(), "body_head": rec.get("body_head", "")[:120],
        }


async def main(out: Path) -> list[dict]:
    rows = []
    for name in CASES:
        for stream in (False, True):
            for fallback in (True, False):
                try:
                    rows.append(await run_case(name, stream, fallback))
                except Exception as e:  # noqa: BLE001 - a harness crash is itself a finding
                    rows.append({"case": name, "stream": stream, "fallback_enabled": fallback,
                                 "harness_error": f"{type(e).__name__}: {str(e)[:200]}"})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="evals/baselines/t0_argon.json")
    result = asyncio.run(main(Path(ap.parse_args().out)))
    for r in result:
        if "harness_error" in r:
            print(f"{r['case']:<22} {'S' if r['stream'] else 'N'} fb={int(r['fallback_enabled'])}  HARNESS_ERROR {r['harness_error']}")
        else:
            print(f"{r['case']:<22} {'S' if r['stream'] else 'N'} fb={int(r['fallback_enabled'])}  "
                  f"http={r['status']} err={r['error_class'] or '-':<22} viol={len(r['violations'])} "
                  f"cmt={r['n_comments']} argon={r['argon_calls']} other={r['other_calls']} "
                  f"leak={r['leaked_slots']} t={r['total_s']}s")
