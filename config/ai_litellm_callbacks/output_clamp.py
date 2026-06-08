from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any

try:
    import yaml
except Exception:  # pragma: no cover - PyYAML ships with LiteLLM, fallback is for local syntax checks.
    yaml = None

try:
    from litellm.integrations.custom_logger import CustomLogger
except Exception:  # pragma: no cover - lets repository checks import this without LiteLLM installed.
    class CustomLogger:  # type: ignore[no-redef]
        pass


CALLBACK_NAME = "ai_litellm_callbacks.output_clamp.proxy_handler_instance"
DEFAULT_POLICY = {
    "enabled": True,
    "default": 32000,
    "tokenizer_headroom": 8192,
    "minimum_input": 32768,
}


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except Exception:
        return None
    return number if number > 0 else None


def _bool_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no", "off"}
    return value is not False


def _read_config_policy() -> dict[str, Any]:
    policy = dict(DEFAULT_POLICY)
    config_path = os.environ.get("AI_LITELLM_CONFIG")
    if yaml is not None and config_path:
        path = Path(config_path)
        if path.is_file():
            try:
                payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:
                payload = {}
            configured = payload.get("x-gateway-output-clamp") or {}
            if isinstance(configured, dict):
                policy.update(configured)

    env_map = {
        "default": "AI_LITELLM_OUTPUT_CLAMP_DEFAULT",
        "tokenizer_headroom": "AI_LITELLM_OUTPUT_CLAMP_TOKENIZER_HEADROOM",
        "minimum_input": "AI_LITELLM_OUTPUT_CLAMP_MINIMUM_INPUT",
    }
    for key, env_name in env_map.items():
        env_value = _positive_int(os.environ.get(env_name))
        if env_value is not None:
            policy[key] = env_value
    return policy


def _policy_value(policy: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _positive_int(policy.get(key))
        if value is not None:
            return value
    return None


def _model_names(kwargs: dict[str, Any]) -> list[str]:
    metadata = kwargs.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    names = [
        kwargs.get("deployment_model_name"),
        metadata.get("deployment_model_name"),
        metadata.get("model_group"),
        kwargs.get("model"),
    ]
    out: list[str] = []
    for name in names:
        if isinstance(name, str) and name and name not in out:
            out.append(name)
    return out


def _per_model_cap(policy: dict[str, Any], kwargs: dict[str, Any]) -> int | None:
    per_model = policy.get("perModel") or policy.get("per_model") or {}
    if not isinstance(per_model, dict):
        return None
    for name in _model_names(kwargs):
        cap = _positive_int(per_model.get(name))
        if cap is not None:
            return cap
    return None


def gateway_output_cap(kwargs: dict[str, Any]) -> int | None:
    policy = _read_config_policy()
    if not _bool_enabled(policy.get("enabled", True)):
        return None

    cap = _per_model_cap(policy, kwargs) or _policy_value(policy, "default") or DEFAULT_POLICY["default"]
    info = kwargs.get("model_info")
    if not isinstance(info, dict):
        metadata = kwargs.get("metadata")
        info = metadata.get("model_info") if isinstance(metadata, dict) else {}
    if not isinstance(info, dict):
        info = {}

    capability = _positive_int(info.get("max_output_tokens"))
    if capability is not None:
        cap = min(cap, capability)

    context = _positive_int(info.get("max_input_tokens"))
    if context is not None:
        configured_headroom = _policy_value(policy, "tokenizer_headroom", "tokenizerHeadroom") or 0
        configured_minimum_input = _policy_value(policy, "minimum_input", "minimumInput") or DEFAULT_POLICY["minimum_input"]
        headroom = min(configured_headroom, math.floor(context * 0.1))
        minimum_input = min(configured_minimum_input, max(1, math.floor(context * 0.5)))
        max_reservation = context - headroom - minimum_input
        cap = min(cap, max_reservation if max_reservation > 0 else 1)

    return max(1, cap)


def clamp_token_reservations(kwargs: dict[str, Any]) -> dict[str, Any]:
    cap = gateway_output_cap(kwargs)
    if cap is None:
        return kwargs

    for key in ("max_tokens", "max_completion_tokens"):
        if key not in kwargs or kwargs[key] is None:
            continue
        value = _positive_int(kwargs[key])
        if value is None:
            continue
        kwargs[key] = min(value, cap)
    return kwargs


class GatewayOutputClamp(CustomLogger):
    async def async_pre_call_deployment_hook(self, kwargs: dict[str, Any], call_type: Any) -> dict[str, Any]:
        return clamp_token_reservations(kwargs)


proxy_handler_instance = GatewayOutputClamp()

