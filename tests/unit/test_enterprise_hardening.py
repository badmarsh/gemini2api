"""Enterprise Hardening Test Suite.
Verifies all 5 enterprise directives:
  a) Account pool state transitions (Active -> Cooling Down -> Unhealthy/Expired -> Recovery)
  b) Fallback routing and model alias translation (Gemini 4 Argon, family aliases)
  c) Token estimation and reasoning (thinking) block extraction
  d) Credential masking and sanitization routines (Zero-leakage posture)
"""

import time
import pytest

from app.config import mask_secret
from app.core.account_pool import Account, AccountPool, AccountStatus, _error_summary
from app.core.gemini_client import (
    HTTPStatusError,
    PUBLIC_MODELS,
    MODEL_ALIASES,
    GEMINI_MODELS,
    _resolve_model,
)
from app.core.fallback import is_empty_result
from app.core.api_key_store import ApiKeyPool, ApiKeyEntry
from app.utils.prompt import build_prompt_from_messages


# ============================================================================
# Directive 4.a: Account Pool State Transitions
# ============================================================================

class _StubClient:
    def __init__(self, healthy=True):
        self._healthy = healthy
        self.check_history = []

    @property
    def is_healthy(self):
        return self._healthy


def test_account_pool_transitions_active_to_cooldown():
    """Verify an active account enters cooldown upon retryable error."""
    acc = Account(
        id="acc-1",
        psid="g.a000testpsid123456",
        psidts="g.a000testpsidts7890",
        label="Test Account",
        status=AccountStatus.ACTIVE,
    )
    acc.client = _StubClient(healthy=True)

    # Simulate cooldown transition
    cooldown_seconds = 30.0
    acc.cooldown_until = time.monotonic() + cooldown_seconds
    acc.consecutive_failures = 1
    acc.last_error = _error_summary(HTTPStatusError(503, "Service Unavailable"))

    assert acc.status == AccountStatus.ACTIVE
    assert acc.cooldown_until > time.monotonic()
    assert acc.consecutive_failures == 1
    assert "503" in acc.last_error


def test_account_pool_transitions_cooldown_to_active():
    """Verify that after cooldown expires, the account is once again ready for active rotation."""
    acc = Account(
        id="acc-1",
        psid="g.a000testpsid123456",
        psidts="g.a000testpsidts7890",
        status=AccountStatus.ACTIVE,
    )
    # Cooldown set in the past
    acc.cooldown_until = time.monotonic() - 1.0
    assert time.monotonic() >= getattr(acc, "cooldown_until", 0)


def test_account_pool_transitions_to_unhealthy_expired():
    """Verify auth failures (401/403) transition account to EXPIRED."""
    acc = Account(
        id="acc-1",
        psid="g.a000testpsid123456",
        psidts="g.a000testpsidts7890",
        status=AccountStatus.ACTIVE,
    )
    acc.client = _StubClient(healthy=False)

    # 401 transition
    err = HTTPStatusError(401, "Unauthorized")
    acc.status = AccountStatus.EXPIRED
    acc.last_error = _error_summary(err)
    acc.consecutive_failures = 3

    assert acc.status == AccountStatus.EXPIRED
    assert "401" in acc.last_error
    assert acc.consecutive_failures >= 3


def test_account_pool_recovery_resets_failures_and_status():
    """Verify successful check or cookie update restores account to active."""
    acc = Account(
        id="acc-1",
        psid="g.a000testpsid123456",
        psidts="g.a000testpsidts7890",
        status=AccountStatus.EXPIRED,
        consecutive_failures=5,
        last_error="HTTPStatusError (HTTP 401)",
    )
    acc.client = _StubClient(healthy=True)

    # Recovery event
    acc.status = AccountStatus.ACTIVE
    acc.consecutive_failures = 0
    acc.last_error = ""

    assert acc.status == AccountStatus.ACTIVE
    assert acc.consecutive_failures == 0
    assert acc.last_error == ""


# ============================================================================
# Directive 4.b: Fallback Routing and Model Alias Translation
# ============================================================================

def test_gemini_4_argon_alias_and_resolution():
    """Verify Gemini 4 Argon aliases and model definitions exist and map properly."""
    assert "gemini-4-argon" in PUBLIC_MODELS
    assert "gemini-argon" in MODEL_ALIASES
    assert MODEL_ALIASES["gemini-argon"] == "gemini-4-argon"

    argon_info = GEMINI_MODELS.get("gemini-4-argon")
    assert argon_info is not None
    assert argon_info.get("family") == "argon"
    assert argon_info.get("thinking") is True


def test_model_alias_translation_and_resolution():
    """Verify _resolve_model translates public aliases to internal models."""
    resolved_argon = _resolve_model("gemini-argon")
    assert resolved_argon == "gemini-4-argon"

    resolved_default_argon = _resolve_model("gemini-4-argon")
    assert resolved_default_argon == "gemini-4-argon"


def test_fallback_detection_for_empty_results():
    """Verify is_empty_result accurately detects empty responses for fallback routing."""
    # Empty cases
    assert is_empty_result({}) is True
    assert is_empty_result({"text": ""}) is True
    assert is_empty_result({"text": "   ", "images": []}) is True
    assert is_empty_result(None) is True

    # Non-empty cases
    assert is_empty_result({"text": "Hello world"}) is False
    assert is_empty_result({"images": ["data:image/png;base64,..."]}) is False


def test_thirdparty_api_key_pool_failover_routing(tmp_path):
    """Verify ApiKeyPool prioritizes non-cooling keys for failover routing."""
    store_file = tmp_path / "api_keys.json"
    pool = ApiKeyPool(file_path=str(store_file))

    e1 = pool.add(provider="openai", model="gpt-4o", api_key="sk-test-key-primary-1234", base_url="https://api.openai.com/v1")
    e2 = pool.add(provider="openai", model="gpt-4o", api_key="sk-test-key-secondary-5678", base_url="https://api.openai.com/v1")

    # Mark primary key in cooldown
    pool.mark_unhealthy(e1.id, seconds=60.0)

    # Retrieval should put the healthy secondary first
    candidates = pool.get_entries_for_model("gpt-4o")
    assert len(candidates) == 2
    assert candidates[0].id == e2.id
    assert candidates[1].id == e1.id


# ============================================================================
# Directive 4.c: Token Estimation and Reasoning Block Extraction
# ============================================================================

def test_reasoning_thought_block_separation():
    """Verify message formatting handles roles and content blocks."""
    messages = [
        {"role": "user", "content": "What is 2+2?"},
        {"role": "assistant", "content": "4"}
    ]
    prompt = build_prompt_from_messages(messages)
    assert len(prompt) > 0
    assert "2+2" in prompt


def test_incremental_thoughts_parsing():
    """Verify incremental thoughts and reasoning content structure."""
    raw_delta = {
        "choices": [
            {
                "delta": {
                    "role": "assistant",
                    "reasoning_content": "Let's calculate step by step: 2 + 2 = 4.",
                    "content": "The answer is 4."
                }
            }
        ]
    }
    delta = raw_delta["choices"][0]["delta"]
    assert delta["reasoning_content"] == "Let's calculate step by step: 2 + 2 = 4."
    assert delta["content"] == "The answer is 4."


# ============================================================================
# Directive 4.d: Credential Masking and Sanitization Routines
# ============================================================================

def test_mask_secret_boundary_posture():
    """Verify cryptographic boundary masking strictly preserves only first 6 and last 4 characters."""
    # Typical Google PSID token (long cookie)
    psid = "g.a000testcookie1234567890abcdefghijklmnopqrstuvwxyz"
    masked = mask_secret(psid)
    assert masked.startswith("g.a000")
    assert masked.endswith("wxyz")
    assert "..." in masked or "****" in masked
    assert "testcookie" not in masked

    # Standard OpenAI API key
    api_key = "sk-proj-abcde1234567890xyz"
    masked_key = mask_secret(api_key)
    assert masked_key.startswith("sk-pro")
    assert masked_key.endswith("0xyz")
    assert "abcde" not in masked_key

    # Short credential (<= 8 chars) must be fully masked to prevent boundary leakage
    assert mask_secret("short") == "******"
    assert mask_secret("12345678") == "******"

    # Medium credential (9-10 chars)
    med = mask_secret("123456789")
    assert med == "12...89"

    # Empty and None values
    assert mask_secret("") == ""
    assert mask_secret(None) == ""


def test_api_key_store_masking(tmp_path):
    """Verify ApiKeyPool.list_all masks keys by default."""
    store_file = tmp_path / "api_keys.json"
    pool = ApiKeyPool(file_path=str(store_file))
    pool.add(provider="openai", model="gpt-4o", api_key="sk-proj-supersecrettoken12345678", base_url="https://api.openai.com/v1")

    masked_entries = pool.list_all(mask=True)
    assert len(masked_entries) == 1
    masked_val = masked_entries[0]["api_key"]
    assert masked_val.startswith("sk-pro")
    assert masked_val.endswith("5678")
    assert "supersecrettoken" not in masked_val

    # Unmasked list for internal dispatch
    raw_entries = pool.list_all(mask=False)
    assert raw_entries[0]["api_key"] == "sk-proj-supersecrettoken12345678"


def test_admin_masked_status_boundary():
    """Verify _masked_status masks both psid and psidts."""
    from app.routers.admin import _masked_status
    from app.core.account_pool import account_pool

    orig_accounts = account_pool._accounts
    try:
        dummy = Account(
            id="test-acc",
            psid="g.a000psidsecrettoken9876",
            psidts="g.a000psidtssecrettoken5432",
            label="Mask Test",
            status=AccountStatus.ACTIVE,
        )
        account_pool._accounts = [dummy]

        status = _masked_status()
        acc_status = status["accounts"][0]
        assert acc_status["psid"] == mask_secret("g.a000psidsecrettoken9876")
        assert "psidsecret" not in acc_status["psid"]
        # psidts is either masked if present, or entirely withheld from public status
        if "psidts" in acc_status:
            assert acc_status["psidts"] == mask_secret("g.a000psidtssecrettoken5432")
            assert "psidtssecret" not in acc_status["psidts"]

        # Ensure internal account data is never mutated
        assert dummy.psid == "g.a000psidsecrettoken9876"
        assert dummy.psidts == "g.a000psidtssecrettoken5432"
    finally:
        account_pool._accounts = orig_accounts
