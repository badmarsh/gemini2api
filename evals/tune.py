"""Deterministic, resumable local tuning harness.

The default T0 tier uses the repository's fault-injecting upstream and never contacts Google. T1/T2 are
opt-in HTTP spot-check tiers for an operator-supplied local or test endpoint. A worker may propose a patch,
but this module applies nothing unless the proposal passes the exact-match validator and ``--apply`` is set.

The worker contract intentionally names ``gemini-3.8-flash``. The harness does not call a hosted worker by
itself: an operator can save a worker response as JSON and pass it with ``--proposal-file``. This keeps the
default evaluation deterministic and prevents an accidental live model call from changing the checkout.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import re
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

WORKER_MODEL = "gemini-3.8-flash"
DEFAULT_STATE = Path("evals/state.json")
DEFAULT_REPORT_DIR = Path("evals/reports")
MAX_CONCURRENCY = 2
MAX_CONSECUTIVE_UPSTREAM_ERRORS = 3
MAX_PATCH_BYTES = 64 * 1024

_SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)(?:sk|key|token)[-_][A-Za-z0-9._~-]{8,}"),
    re.compile(r"(?i)__Secure-1PSID(?:TS)?\s*[=:]\s*[^;\s,]+"),
    re.compile(r"(?i)(?:psid|psidts|cookie|authorization)\s*[=:]\s*[^,\s]+"),
)
_SECRET_KEYS = {
    "authorization", "cookie", "cookies", "psid", "psidts", "api_key", "token", "secret",
    "cookie_value", "cookie_header", "session_cookie",
}


@dataclass
class TuneConfig:
    tier: str = "t0"
    state_path: Path = DEFAULT_STATE
    report_dir: Path = DEFAULT_REPORT_DIR
    seed: int = 20261016
    concurrency: int = MAX_CONCURRENCY
    argon_cap: int = 200
    max_cases: int = 0
    base_url: str = ""
    api_key_env: str = "API_KEY"
    timeout_s: float = 30.0
    resume: bool = False
    apply_proposal: bool = False
    proposal_file: Path | None = None


@dataclass
class TuneState:
    run_id: str
    tier: str
    seed: int
    started_at: str
    completed: dict[str, dict] = field(default_factory=dict)
    records: list[dict] = field(default_factory=list)
    edits: list[dict] = field(default_factory=list)
    argon_requests: int = 0
    consecutive_upstream_errors: int = 0
    aborted: bool = False
    abort_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "version": 1,
            "run_id": self.run_id,
            "tier": self.tier,
            "seed": self.seed,
            "started_at": self.started_at,
            "completed": redact(self.completed),
            "records": redact(self.records),
            "edits": redact(self.edits),
            "argon_requests": self.argon_requests,
            "consecutive_upstream_errors": self.consecutive_upstream_errors,
            "aborted": self.aborted,
            "abort_reason": self.abort_reason,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> "TuneState":
        return cls(
            run_id=str(raw.get("run_id") or uuid.uuid4().hex[:12]),
            tier=str(raw.get("tier") or "t0"),
            seed=int(raw.get("seed") or 20261016),
            started_at=str(raw.get("started_at") or _now()),
            completed=dict(raw.get("completed") or {}),
            records=list(raw.get("records") or []),
            edits=list(raw.get("edits") or []),
            argon_requests=int(raw.get("argon_requests") or 0),
            consecutive_upstream_errors=int(raw.get("consecutive_upstream_errors") or 0),
            aborted=bool(raw.get("aborted")),
            abort_reason=str(raw.get("abort_reason") or ""),
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact(value: Any, *, key: str = "") -> Any:
    """Recursively remove credentials and cap diagnostic strings before persistence/reporting."""
    low_key = key.lower()
    if low_key in _SECRET_KEYS or any(part in low_key for part in ("password", "credential")):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, key=key) for v in value[:200]]
    if isinstance(value, str):
        text = value.replace(".env", "[ENV_FILE]").replace("data/", "[DATA_DIR]/")
        for pattern in _SECRET_PATTERNS:
            text = pattern.sub("[REDACTED]", text)
        return text[:2000]
    return value


def stable_record(record: dict) -> dict:
    """Keep only deterministic, client-visible fields for T0 comparison/fingerprints."""
    keep = (
        "case", "stream", "fallback_enabled", "fallback_used", "empty", "status", "error_class", "violations", "content",
        "finish_reason", "n_comments", "argon_calls", "other_calls", "leaked_slots", "harness_error",
    )
    return {key: record.get(key) for key in keep if key in record}


def stable_digest(records: Iterable[dict]) -> str:
    payload = [stable_record(r) for r in records]
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, payload: dict) -> None:
    """Persist state/report atomically without ever touching runtime ``data/`` or ``.env`` files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(json.dumps(redact(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def load_state(path: Path) -> TuneState | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("version") != 1:
        raise ValueError(f"unsupported tuning state format: {path}")
    return TuneState.from_dict(raw)


@contextlib.contextmanager
def single_process_lock(state_path: Path):
    """Prevent two tuning processes from sharing a state/report run or touching runtime data indirectly."""
    lock_path = state_path.with_name(state_path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RuntimeError(f"another tuning process owns {lock_path}") from exc
    try:
        os.write(fd, str(os.getpid()).encode("ascii"))
        os.close(fd)
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            lock_path.unlink()


def new_state(config: TuneConfig) -> TuneState:
    return TuneState(run_id=f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}",
                     tier=config.tier, seed=config.seed, started_at=_now())


def worker_contract() -> dict:
    """Machine-readable contract for the optional ``gemini-3.8-flash`` tuning worker."""
    return {
        "model": WORKER_MODEL,
        "task": "propose one minimal, evidence-backed hardening edit",
        "input": {
            "finding": "string",
            "redacted_metrics": "object",
            "source_excerpt": "string",
            "tests": "array of strings",
        },
        "output": {
            "edits": [{
                "path": "repository-relative path",
                "old_text": "exact existing text, required exactly once",
                "new_text": "replacement text",
                "reason": "short evidence-based reason",
            }],
            "no_change_reason": "string when edits is empty",
        },
        "rules": [
            "Return JSON only.",
            "Never include credentials, cookies, prompts, account IDs, or raw response bodies.",
            "Do not use regex rescue, guessed truncation, or broad search-and-replace.",
            "At most one exact-match edit per proposal; the harness applies nothing without --apply.",
            "Only repository files outside data/, .env, and docker-compose.override.yml may be proposed.",
        ],
    }


def _safe_relative_path(path: str, root: Path) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() or any(part in ("", ".", "..") for part in candidate.parts):
        raise ValueError("proposal path must be a clean repository-relative path")
    if candidate.name in {".env", "docker-compose.override.yml"} or candidate.parts[0] == "data":
        raise ValueError("proposal cannot touch secrets or runtime data")
    resolved = (root / candidate).resolve()
    if root.resolve() not in resolved.parents:
        raise ValueError("proposal path escapes repository root")
    return resolved


def _read_text_preserving_eol(path: Path) -> str:
    return path.read_bytes().decode("utf-8")


def validate_proposal(proposal: dict, *, root: Path = Path(".")) -> list[dict]:
    """Validate worker output; exact ``str.count`` is the only edit matching mechanism."""
    if not isinstance(proposal, dict):
        raise ValueError("worker proposal must be an object")
    edits = proposal.get("edits", [])
    if not isinstance(edits, list) or len(edits) > 1:
        raise ValueError("worker proposal must contain zero or one edit")
    checked: list[dict] = []
    for edit in edits:
        if not isinstance(edit, dict):
            raise ValueError("each edit must be an object")
        path = _safe_relative_path(str(edit.get("path", "")), root)
        old_text = edit.get("old_text")
        new_text = edit.get("new_text")
        if not isinstance(old_text, str) or not old_text:
            raise ValueError("old_text is required")
        if not isinstance(new_text, str):
            raise ValueError("new_text is required")
        if len(new_text.encode("utf-8")) > MAX_PATCH_BYTES:
            raise ValueError("replacement is too large")
        if any(pattern.search(new_text) for pattern in _SECRET_PATTERNS):
            raise ValueError("replacement contains a credential-like value")
        source = _read_text_preserving_eol(path)
        matches = source.count(old_text)
        if matches != 1:
            raise ValueError(f"old_text must match exactly once (found {matches})")
        checked.append({"path": str(path.relative_to(root.resolve())), "old_text": old_text,
                        "new_text": new_text, "reason": str(edit.get("reason") or "")[:500]})
    return checked


def apply_proposal(proposal: dict, *, root: Path = Path(".")) -> list[dict]:
    """Apply only a proposal that was validated immediately before the write."""
    checked = validate_proposal(proposal, root=root)
    applied = []
    for edit in checked:
        path = root / edit["path"]
        source = _read_text_preserving_eol(path)
        # Re-check immediately before writing so a concurrent edit cannot redirect the patch.
        if source.count(edit["old_text"]) != 1:
            raise ValueError(f"source changed before applying {edit['path']}")
        path.write_bytes(source.replace(edit["old_text"], edit["new_text"], 1).encode("utf-8"))
        applied.append({"path": edit["path"], "reason": edit["reason"], "applied": True})
    return applied


def _error_class(status: int, body: str) -> str:
    low = body.lower()
    if status == 429 or "captcha" in low or "unusual traffic" in low:
        return "rate_limit_or_captcha"
    if status >= 500:
        return "upstream_error"
    if status in (401, 403):
        return "auth_error"
    if status >= 400:
        return "client_error"
    return ""


def _record_is_upstream_error(record: dict) -> bool:
    return record.get("status") == 429 or int(record.get("status") or 0) >= 500 or record.get("error_class") in {
        "rate_limit_or_captcha", "upstream_error", "transport_error", "upstream_timeout"
    }


async def run_t0(config: TuneConfig, state: TuneState) -> list[dict]:
    """Run the deterministic fault matrix and checkpoint after every case."""
    from evals.run_t0 import CASES, run_case  # local import keeps module import side-effect free

    keys = [(name, stream, fallback) for name in CASES for stream in (False, True) for fallback in (True, False)]
    if config.max_cases:
        keys = keys[:config.max_cases]
    records = list(state.records)
    for name, stream, fallback in keys:
        key = f"{name}|{'stream' if stream else 'nonstream'}|fb={int(fallback)}"
        if key in state.completed:
            continue
        if state.argon_requests >= config.argon_cap:
            state.aborted = True
            state.abort_reason = "Argon request cap reached"
            break
        try:
            raw_record = await run_case(name, stream, fallback)
            record = stable_record(raw_record)
            # T0 records keep only bounded outcome metadata; response bodies and volatile IDs are discarded.
            record["fallback_used"] = bool(raw_record.get("other_calls"))
            record["empty"] = not bool(raw_record.get("content"))
            record["latency_ms"] = round(float(raw_record.get("total_s") or 0) * 1000, 1)
            state.argon_requests += int(raw_record.get("argon_calls") or 0)
        except Exception as exc:  # a harness crash is a record, not an invisible failure
            record = {"case": name, "stream": stream, "fallback_enabled": fallback,
                      "harness_error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        record["case_key"] = key
        records = [existing for existing in records if existing.get("case_key") != key]
        records.append(record)
        state.records = records
        state.completed[key] = {"status": record.get("status"), "error_class": record.get("error_class", ""),
                                "digest": hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[:12]}
        atomic_write_json(config.state_path, state.to_dict())
    return records


async def _live_one(client, spec: dict, timeout_s: float) -> dict:
    started = time.monotonic()
    requested = str(spec["model"])
    try:
        response = await client.post(spec["path"], json=spec["body"], timeout=timeout_s)
        raw = (await response.aread())[:65536].decode("utf-8", "replace")
        served = response.headers.get("x-gemini2api-argon-served-by", "")
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                served = str(payload.get("model") or served)
        except json.JSONDecodeError:
            pass
        error_class = _error_class(response.status_code, raw)
        return {
            "label": spec["label"], "model": requested, "served_model": served[:128], "status": response.status_code,
            "error_class": error_class, "fallback_used": bool(served and served != requested),
            "total_ms": round((time.monotonic() - started) * 1000, 1), "response_length": len(raw),
        }
    except Exception as exc:  # noqa: BLE001 - live transport types vary by client
        return {
            "label": spec["label"], "model": requested, "served_model": "", "status": 0,
            "error_class": "transport_error", "fallback_used": False,
            "total_ms": round((time.monotonic() - started) * 1000, 1),
            "transport_type": type(exc).__name__,
        }


def _live_specs(tier: str, base_url: str) -> list[dict]:
    model = "gemini-4-argon"
    flash = "gemini-3-flash"
    common = {"messages": [{"role": "user", "content": "Reply with the word pong."}]}
    specs = [
        {"label": "argon-buffered", "model": model, "path": "/v1/chat/completions",
         "body": {**common, "model": model, "stream": False}},
        {"label": "flash-buffered", "model": flash, "path": "/v1/chat/completions",
         "body": {**common, "model": flash, "stream": False}},
    ]
    if tier == "t2":
        specs.extend([
            {"label": "argon-stream", "model": model, "path": "/v1/chat/completions",
             "body": {**common, "model": model, "stream": True}},
            {"label": "responses-argon", "model": model, "path": "/v1/responses",
             "body": {"model": model, "input": "Reply with the word pong.", "stream": False}},
        ])
    return specs


async def run_live(config: TuneConfig, state: TuneState) -> list[dict]:
    if not config.base_url:
        raise ValueError("live tier requires --base-url")
    api_key = os.environ.get(config.api_key_env, "")
    if not api_key:
        raise ValueError(f"live tier requires {config.api_key_env} in the environment")
    import httpx

    specs = _live_specs(config.tier, config.base_url)
    semaphore = asyncio.Semaphore(min(MAX_CONCURRENCY, max(1, config.concurrency)))
    headers = {"Authorization": f"Bearer {api_key}"}
    consecutive = state.consecutive_upstream_errors

    async with httpx.AsyncClient(base_url=config.base_url.rstrip("/"), headers=headers) as client:
        async def one(spec):
            nonlocal consecutive
            if "argon" in spec["model"].lower() and state.argon_requests >= config.argon_cap:
                return {"label": spec["label"], "status": 0, "error_class": "argon_cap_reached"}
            async with semaphore:
                record = await _live_one(client, spec, config.timeout_s)
            if "argon" in spec["model"].lower():
                state.argon_requests += 1
            if _record_is_upstream_error(record):
                consecutive += 1
            else:
                consecutive = 0
            state.consecutive_upstream_errors = consecutive
            if consecutive >= MAX_CONSECUTIVE_UPSTREAM_ERRORS:
                state.aborted = True
                state.abort_reason = "three consecutive upstream errors, 429s, or captcha responses"
            state.records.append(record)
            atomic_write_json(config.state_path, state.to_dict())
            return record

        results = []
        for spec in specs:
            if state.aborted:
                break
            results.append(await one(spec))
        return state.records


def summarize(records: list[dict]) -> dict:
    classes = Counter(str(r.get("error_class") or "ok") for r in records)
    latencies = sorted(float(r.get("total_ms", r.get("latency_ms", 0))) for r in records
                       if r.get("total_ms") is not None or r.get("latency_ms") is not None)
    return {
        "records": len(records),
        "error_classes": dict(classes),
        "error_rate": (sum(v for k, v in classes.items() if k != "ok") / len(records)) if records else 0.0,
        "fallback_count": sum(1 for r in records if r.get("fallback_used")),
        "latency_ms": {
            "p50": latencies[(len(latencies) - 1) // 2] if latencies else 0.0,
            "p95": latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else 0.0,
            "max": latencies[-1] if latencies else 0.0,
        },
        "stable_digest": stable_digest(records) if records else "",
    }


def report_payload(config: TuneConfig, state: TuneState) -> dict:
    return {
        "run_id": state.run_id,
        "tier": config.tier,
        "worker_contract": worker_contract(),
        "caps": {"concurrency": MAX_CONCURRENCY, "argon_cap": config.argon_cap,
                 "abort_after_consecutive_upstream_errors": MAX_CONSECUTIVE_UPSTREAM_ERRORS},
        "summary": summarize(state.records),
        "state": {"aborted": state.aborted, "abort_reason": state.abort_reason,
                  "argon_requests": state.argon_requests, "completed": len(state.completed)},
        "records": redact(state.records),
        "edits": redact(state.edits),
    }


def write_reports(config: TuneConfig, state: TuneState) -> tuple[Path, Path]:
    config.report_dir.mkdir(parents=True, exist_ok=True)
    payload = report_payload(config, state)
    json_path = config.report_dir / f"{state.run_id}.json"
    md_path = config.report_dir / f"{state.run_id}.md"
    atomic_write_json(json_path, payload)
    summary = payload["summary"]
    lines = [
        f"# Local tuning run {state.run_id}", "", f"- Tier: `{config.tier}`",
        f"- Worker contract: `{WORKER_MODEL}` (proposal-only; no implicit model call)",
        f"- Records: {summary['records']}", f"- Error rate: {summary['error_rate']:.3f}",
        f"- Stable digest: `{summary['stable_digest']}`", "", "## Error classes", "",
    ]
    lines.extend(f"- `{key}`: {value}" for key, value in sorted(summary["error_classes"].items()))
    lines.extend(["", "## Safety", "", "- T0 uses only the fake upstream and temporary evaluation data.",
                  "- Runtime data, `.env`, cookies, prompts, and account identifiers are excluded/redacted.",
                  "- Worker edits require one exact old-text match and an explicit `--apply`."])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, md_path


def read_proposal(path: Path) -> dict:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("proposal JSON must be an object")
    return raw


async def run(config: TuneConfig) -> TuneState:
    prior = load_state(config.state_path) if config.resume else None
    if prior is not None and prior.tier != config.tier:
        raise ValueError(f"cannot resume tier {prior.tier!r} as {config.tier!r}")
    state = prior or new_state(config)
    atomic_write_json(config.state_path, state.to_dict())
    if config.tier == "t0":
        await run_t0(config, state)
    elif config.tier in {"t1", "t2"}:
        await run_live(config, state)
    else:
        raise ValueError(f"unknown tier: {config.tier}")

    if config.proposal_file:
        proposal = read_proposal(config.proposal_file)
        root = Path.cwd().resolve()
        checked = validate_proposal(proposal, root=root)
        state.edits.append({"validated": True, "count": len(checked), "paths": [e["path"] for e in checked]})
        if config.apply_proposal:
            state.edits.extend(apply_proposal(proposal, root=root))
    atomic_write_json(config.state_path, state.to_dict())
    write_reports(config, state)
    return state


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tier", choices=("t0", "t1", "t2"), default="t0",
                        help="t0=deterministic fake upstream; t1/t2=opt-in local/live HTTP tiers")
    parser.add_argument("--base-url", default="", help="HTTP target for t1/t2; never inferred from localhost")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--resume", action="store_true", help="resume the exact tier from --state")
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--argon-cap", type=int, default=200)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--proposal-file", type=Path)
    parser.add_argument("--apply", dest="apply_proposal", action="store_true",
                        help="apply a proposal only after exact-match validation")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = TuneConfig(tier=args.tier, base_url=args.base_url, state_path=args.state,
                        report_dir=args.report_dir, resume=args.resume, max_cases=max(0, args.max_cases),
                        argon_cap=max(1, args.argon_cap), timeout_s=max(0.1, args.timeout),
                        proposal_file=args.proposal_file, apply_proposal=args.apply_proposal)
    try:
        with single_process_lock(config.state_path):
            state = asyncio.run(run(config))
    except Exception as exc:  # concise CLI error; no exception text from a live upstream is printed
        print(f"tuning failed: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return 2
    print(json.dumps({"run_id": state.run_id, "tier": state.tier, "records": len(state.records),
                      "aborted": state.aborted, "abort_reason": state.abort_reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
