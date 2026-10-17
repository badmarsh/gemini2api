"""Redacted readiness view of the effective runtime configuration.

The console shows the *effective* value of every setting, where that value came
from (environment, persisted panel override or the built-in default), whether a
change only takes effect after a restart, and which persisted overrides are
invalid or shadowed by the environment.

This module reuses the existing configuration schemas -- ``app.config.Settings``
for the values and ``app.core.settings_overrides`` for the persisted overrides and
their validation -- instead of introducing a second configuration system.

Secrets are never rendered: for those fields only "configured / not configured" and
the source are reported.
"""
from __future__ import annotations

import os
from typing import Any, Mapping

from app.core.settings_overrides import (
    PERSISTABLE_FIELDS,
    check_domain,
    coerce_value,
    load_overrides,
)

# Settings that may be shown verbatim (no secret material in them).
SAFE_FIELDS: tuple[str, ...] = (
    "refresh_interval",
    "max_retries",
    "same_account_5xx_retries",
    "failover_cooldown",
    "rate_limit_enabled",
    "rate_limit_window",
    "rate_limit_max",
    "health_check_enabled",
    "health_check_interval",
    "rotation_strategy",
    "max_concurrent_per_account",
    "acquire_timeout",
    "version_sync_enabled",
    "version_sync_interval",
    "jitter_enabled",
    "usage_stats_enabled",
    "usage_stats_interval",
    "usage_stats_retention_days",
    "model_whitelist",
    "chat_cleanup_enabled",
    "chat_cleanup_keep_hours",
    "chat_cleanup_interval_hours",
    "chat_cleanup_skip_pinned",
    "extended_thinking_enabled",
    "log_bodies_enabled",
    "argon_fallback_to_flash",
    "argon_always_fallback",
    "argon_fallback_model",
    "argon_failover_on_stall",
    "argon_stall_breaker_seconds",
    "argon_timeout",
    "server_side_continuation",
    "argon_system_prompt_mode",
    "tool_loop_reminder",
    "tool_loop_stall_guard",
    "fallback_enabled",
    "fallback_models",
    "thirdparty_failover_cooldown",
    "image_download_size_suffix",
    "image_download_timeout",
    "docs_enabled",
    "cors_allow_origins",
    "cors_allow_credentials",
    "log_level",
)

# Secret material: only "configured" and the source are reported.
SECRET_FIELDS: tuple[str, ...] = (
    "api_key",
    "admin_api_key",
    "gemini_psid",
    "gemini_psidts",
)

# User content: only whether it is set and how long it is.
SENSITIVE_FIELDS: tuple[str, ...] = ("argon_system_prompt",)

# Settings that are read once at import/startup time: changing them needs a
# restart, even though the settings panel offers them.
RESTART_REQUIRED_FIELDS: tuple[str, ...] = (
    "acquire_timeout",
    "accounts_file",
    "chat_cleanup_enabled",
    "chat_cleanup_interval_hours",
    "chat_cleanup_keep_hours",
    "chat_cleanup_skip_pinned",
    "cors_allow_credentials",
    "cors_allow_origins",
    "docs_enabled",
    "fingerprint_config_path",
    "log_level",
    "port",
    "usage_stats_enabled",
    "usage_stats_interval",
    "usage_stats_retention_days",
    "version_sync_enabled",
)


def _env_value(field: str, env: Mapping[str, str]) -> str | None:
    """The raw environment value for a setting (docker env_file lands here too)."""
    value = env.get(field.upper())
    if value is None:
        return None
    value = value.strip()
    return value or None


def _coerce_env(field: str, raw: str) -> Any:
    """Best-effort conversion of an environment string to the declared type."""
    ok, coerced = coerce_value(field, raw)
    if ok:
        return coerced
    for caster in (int, float):
        try:
            return caster(raw)
        except (TypeError, ValueError):
            continue
    if raw.lower() in ("true", "false"):
        return raw.lower() == "true"
    return raw


def build_config_report(
    settings_obj: Any,
    *,
    env: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> dict:
    """Build the redacted configuration report. Never raises on partial input."""
    env = os.environ if env is None else env
    if overrides is None:
        overrides = load_overrides()

    fields: dict[str, dict] = {}
    for name in SAFE_FIELDS:
        effective = getattr(settings_obj, name, None)
        if effective is None:
            continue
        env_raw = _env_value(name, env)
        override_value = overrides.get(name)
        if override_value is not None:
            source = "override"
        elif env_raw is not None:
            source = "env"
        else:
            source = "default"
        entry: dict[str, Any] = {
            "value": effective,
            "source": source,
            "restart_required": name in RESTART_REQUIRED_FIELDS,
        }
        if env_raw is not None:
            entry["env"] = _coerce_env(name, env_raw)
        if override_value is not None:
            entry["override"] = override_value
        fields[name] = entry

    secrets: dict[str, dict] = {}
    for name in SECRET_FIELDS:
        value = str(getattr(settings_obj, name, "") or "")
        if name in overrides:
            source = "override"
        elif _env_value(name, env) is not None:
            source = "env"
        elif value:
            source = "generated"
        else:
            source = "unset"
        secrets[name] = {"configured": bool(value), "source": source}

    for name in SENSITIVE_FIELDS:
        value = str(getattr(settings_obj, name, "") or "")
        secrets[name] = {
            "configured": bool(value),
            "source": "env" if _env_value(name, env) is not None else "default",
            "length": len(value),
        }

    issues: list[dict] = []
    drift: list[dict] = []
    for name, value in overrides.items():
        if name not in PERSISTABLE_FIELDS:
            drift.append({"field": name, "kind": "unknown_override",
                          "detail": "not a persistable setting"})
            continue
        ok, coerced = coerce_value(name, value)
        if not ok:
            issues.append({"field": name, "issue": "wrong_type"})
            drift.append({"field": name, "kind": "invalid_override", "detail": "wrong type"})
            continue
        reason = check_domain(name, coerced)
        if reason is not None:
            issues.append({"field": name, "issue": reason})
            drift.append({"field": name, "kind": "invalid_override", "detail": reason})
            continue
        effective = getattr(settings_obj, name, None)
        if effective is not None and effective != coerced:
            drift.append({
                "field": name,
                "kind": "override_not_applied",
                "detail": f"effective {effective!r} differs from the persisted override",
            })
        env_raw = _env_value(name, env)
        if env_raw is not None:
            env_value = _coerce_env(name, env_raw)
            if env_value != coerced:
                drift.append({
                    "field": name,
                    "kind": "env_overridden",
                    "detail": "the persisted panel override wins over the environment",
                })

    features = {
        "diagnostics": True,
        "usage_stats": bool(getattr(settings_obj, "usage_stats_enabled", False)),
        "argon_fallback": bool(getattr(settings_obj, "argon_fallback_to_flash", False)),
        "thirdparty_fallback": bool(getattr(settings_obj, "fallback_enabled", False)),
        "health_checks": bool(getattr(settings_obj, "health_check_enabled", False)),
        "body_logging": bool(getattr(settings_obj, "log_bodies_enabled", False)),
        "docs": bool(getattr(settings_obj, "docs_enabled", False)),
        "chat_cleanup": bool(getattr(settings_obj, "chat_cleanup_enabled", False)),
    }

    return {
        "fields": fields,
        "secrets": secrets,
        "features": features,
        "validation": {"ok": not issues, "issues": issues},
        "drift": drift,
        "restart_required": sorted(name for name in fields if fields[name]["restart_required"]),
    }
