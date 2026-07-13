from __future__ import annotations

import json
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

try:
    from litellm.exceptions import BadRequestError as LiteLLMBadRequestError
except Exception:  # pragma: no cover - optional outside the LiteLLM proxy runtime.
    LiteLLMBadRequestError = None  # type: ignore[assignment]


CALLBACK_NAME = "ai_litellm_callbacks.output_clamp.proxy_handler_instance"
DEFAULT_POLICY = {
    "enabled": True,
    "default": 32000,
    "tokenizer_headroom": 8192,
    "minimum_input": 32768,
}
DEFAULT_COST_GUARDRAIL = {
    "enabled": True,
    "max_estimated_input_tokens": 200000,
    "max_estimated_total_tokens": 240000,
    "chars_per_token": 4,
}


class GatewayCostGuardrailError(ValueError):
    pass


class GatewayContextWindowError(ValueError):
    pass


class GatewayReasoningEffortError(ValueError):
    pass


_CONTEXT_GUARDED_CALL_TYPES = {
    "completion",
    "acompletion",
    "text_completion",
    "atext_completion",
    "anthropic_messages",
    "responses",
    "aresponses",
    "generate_content",
    "agenerate_content",
    "generate_content_stream",
    "agenerate_content_stream",
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


def _read_config_payload() -> dict[str, Any]:
    config_path = os.environ.get("AI_LITELLM_CONFIG")
    if yaml is not None and config_path:
        path = Path(config_path)
        if path.is_file():
            try:
                return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except Exception:
                return {}
    return {}


def _read_config_policy() -> dict[str, Any]:
    policy = dict(DEFAULT_POLICY)
    configured = _read_config_payload().get("x-gateway-output-clamp") or {}
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


def _read_cost_guardrail_policy() -> dict[str, Any]:
    policy = dict(DEFAULT_COST_GUARDRAIL)
    configured = _read_config_payload().get("x-gateway-cost-guardrail") or {}
    if isinstance(configured, dict):
        policy.update(configured)

    enabled = os.environ.get("AI_LITELLM_COST_GUARDRAIL_ENABLED")
    if enabled is not None:
        policy["enabled"] = _bool_enabled(enabled)

    env_map = {
        "max_estimated_input_tokens": "AI_LITELLM_COST_GUARDRAIL_MAX_ESTIMATED_INPUT_TOKENS",
        "max_estimated_total_tokens": "AI_LITELLM_COST_GUARDRAIL_MAX_ESTIMATED_TOTAL_TOKENS",
        "chars_per_token": "AI_LITELLM_COST_GUARDRAIL_CHARS_PER_TOKEN",
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


def _model_info(kwargs: dict[str, Any]) -> dict[str, Any]:
    info = kwargs.get("model_info")
    if isinstance(info, dict):
        return info
    for metadata_key in ("metadata", "litellm_metadata"):
        metadata = kwargs.get(metadata_key)
        if isinstance(metadata, dict) and isinstance(metadata.get("model_info"), dict):
            return metadata["model_info"]
    return {}


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
    info = _model_info(kwargs)

    capability = _positive_int(info.get("max_output_tokens"))
    if capability is not None:
        cap = min(cap, capability)

    context = _positive_int(info.get("max_input_tokens"))
    if context is not None:
        configured_headroom = _policy_value(policy, "tokenizer_headroom", "tokenizerHeadroom") or 0
        configured_minimum_input = _policy_value(policy, "minimum_input", "minimumInput") or DEFAULT_POLICY["minimum_input"]
        # 10%/50% context scaling keeps the absolute policy constants meaningful
        # on tiny local windows (gemma 8192 -> cap 3277). Mirrored in lib.zsh's
        # Node and Ruby copies -- change all three together (check.zsh pins 3277/221950).
        headroom = min(configured_headroom, math.floor(context * 0.1))
        minimum_input = min(configured_minimum_input, max(1, math.floor(context * 0.5)))
        max_reservation = context - headroom - minimum_input
        cap = min(cap, max_reservation if max_reservation > 0 else 1)

    return max(1, cap)


# Lower-only by design: absent output keys mean provider-default semantics;
# the harness layer (not the gateway) is where reservations get introduced.
def clamp_token_reservations(kwargs: dict[str, Any]) -> dict[str, Any]:
    cap = gateway_output_cap(kwargs)
    if cap is None:
        return kwargs

    for key in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
        if key not in kwargs or kwargs[key] is None:
            continue
        value = _positive_int(kwargs[key])
        if value is None:
            continue
        kwargs[key] = min(value, cap)
    return kwargs


def _requested_effort(container: dict[str, Any]) -> str | None:
    direct = container.get("reasoning_effort")
    if isinstance(direct, str) and direct:
        return direct.lower()
    for key in ("reasoning", "output_config"):
        value = container.get(key)
        if isinstance(value, dict) and isinstance(value.get("effort"), str):
            return value["effort"].lower()
    return None


def _set_effort(container: dict[str, Any], effort: str) -> None:
    if "reasoning_effort" in container:
        container["reasoning_effort"] = effort
    changed = False
    for key in ("reasoning", "output_config"):
        value = container.get(key)
        if isinstance(value, dict) and "effort" in value:
            value["effort"] = effort
            changed = True
    if not changed and "reasoning_effort" not in container:
        container["reasoning_effort"] = effort


def _remove_effort(container: dict[str, Any]) -> None:
    container.pop("reasoning_effort", None)
    for key in ("reasoning", "output_config"):
        value = container.get(key)
        if not isinstance(value, dict):
            continue
        value.pop("effort", None)
        if not value:
            container.pop(key, None)


def enforce_reasoning_effort_policy(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Make Claude's implicit effort obey the selected route contract.

    Claude Code 2.1.207 sends adaptive thinking plus output_config.effort even
    without ``--effort``. LiteLLM's global drop_params can otherwise discard
    that intent for Kimi, pass an unvalidated value to MiMo, or bypass GLM's
    high-only policy. Apply the same rule to the top-level and LiteLLM's nested
    optional-parameter bag so hook ordering cannot create an escape hatch.
    """

    info = _model_info(kwargs)
    raw_allowed = info.get("x_reasoning_efforts")
    allowed = [str(value).lower() for value in raw_allowed] if isinstance(raw_allowed, list) else []
    containers = [kwargs]
    optional = kwargs.get("optional_params")
    if isinstance(optional, dict):
        containers.append(optional)
    requested = next(
        (effort for container in containers if (effort := _requested_effort(container))),
        None,
    )
    if requested is None:
        return kwargs
    if not allowed:
        for container in containers:
            _remove_effort(container)
        return kwargs
    if requested in allowed:
        return kwargs
    if len(allowed) == 1:
        for container in containers:
            if _requested_effort(container) is not None:
                _set_effort(container, allowed[0])
        return kwargs
    model = next(iter(_model_names(kwargs)), "unknown")
    message = (
        f"claude-litellm reasoning effort '{requested}' is not supported by {model}; "
        f"allowed: {', '.join(allowed)}"
    )
    if LiteLLMBadRequestError is not None:
        raise LiteLLMBadRequestError(message=message, model=model, llm_provider="claude-litellm")
    raise GatewayReasoningEffortError(message)


def _iter_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(_iter_text(item))
        return out
    if isinstance(value, dict):
        # Traverse every sibling. OpenAI assistant history commonly has
        # content=null next to tool_calls[].function.arguments; prioritizing the
        # content key would otherwise estimate a huge billable tool payload as
        # zero input tokens.
        out: list[str] = []
        for key, item in value.items():
            out.extend(_iter_text(str(key)))
            out.extend(_iter_text(item))
        return out
    return []


def estimate_input_tokens(kwargs: dict[str, Any]) -> int:
    policy = _read_cost_guardrail_policy()
    chars_per_token = _policy_value(policy, "chars_per_token", "charsPerToken") or DEFAULT_COST_GUARDRAIL[
        "chars_per_token"
    ]
    texts: list[str] = []
    # Agentic harnesses (Claude Code, Codex) carry the bulk of the prompt in the
    # tool catalog and system block, not in `messages`. Counting only the
    # conversation under-estimates the request and lets a large tools/system
    # payload slip past the guardrail to a billable backend, so traverse those
    # too. `system` is Anthropic-native; `tools`/`functions` cover both the
    # Anthropic and OpenAI request shapes the proxy sees.
    for key in ("messages", "input", "prompt", "system", "instructions", "tools", "functions"):
        if key in kwargs:
            value = kwargs.get(key)
            if isinstance(value, str):
                texts.append(value)
                continue
            try:
                # Retain structural JSON syntax as well as every sibling key and
                # value. This matters for large tool schemas and assistant tool
                # calls whose arguments live beside content=null.
                texts.append(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))
            except Exception:
                texts.extend(_iter_text(value))
    if not texts:
        return 0

    joined = "\n".join(texts)
    ascii_chars = sum(1 for character in joined if ord(character) < 128)
    non_ascii = "".join(character for character in joined if ord(character) >= 128)
    # Whitespace splitting and len/4 badly undercount CJK text. A Unicode
    # codepoint is commonly one token and emoji can require more, so account
    # for at least one token/codepoint and half of its UTF-8 byte length.
    unicode_tokens = max(len(non_ascii), math.ceil(len(non_ascii.encode("utf-8")) / 2))
    by_chars = math.ceil(ascii_chars / max(1, chars_per_token)) + unicode_tokens
    by_words = len(joined.split())
    return max(by_chars, by_words)


def requested_output_tokens(kwargs: dict[str, Any]) -> int:
    info = _model_info(kwargs)
    capability = _positive_int(info.get("max_output_tokens")) if isinstance(info, dict) else None
    enforcement = str(info.get("x_output_enforcement") or "") if isinstance(info, dict) else ""
    if capability is not None and (
        "provider-natural-cap-only" in enforcement or "strips-token-limit-fields" in enforcement
    ):
        return capability
    values = [
        _positive_int(kwargs.get("max_tokens")),
        _positive_int(kwargs.get("max_completion_tokens")),
        _positive_int(kwargs.get("max_output_tokens")),
    ]
    requested = [value for value in values if value is not None]
    if requested:
        return max(requested)
    # Do not mutate an omitted provider field, but price the request
    # conservatively. Otherwise a direct compatible-API caller can omit both
    # fields and make the total-cost guard account for zero output while the
    # provider is still free to generate its default maximum.
    return capability or gateway_output_cap(kwargs) or 0


def _tokenizer_headroom_tokens(kwargs: dict[str, Any], context: int) -> int:
    policy = _read_config_policy()
    configured = _policy_value(policy, "tokenizer_headroom", "tokenizerHeadroom") or 0
    # Match gateway_output_cap(): tiny local contexts receive proportional
    # headroom, while normal/cloud contexts retain the configured absolute
    # safety margin.
    return min(configured, math.floor(context * 0.1))


def gateway_context_window_decision(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Conservatively prove that the complete request fits the route window.

    Every supported gateway route must publish model_info.max_input_tokens. A
    missing or invalid value is therefore a broken routing contract, not a
    reason to dispatch without a bound.
    """

    estimated_input = estimate_input_tokens(kwargs)
    requested_output = requested_output_tokens(kwargs)
    context = _positive_int(_model_info(kwargs).get("max_input_tokens"))
    reasons: list[str] = []

    if context is None:
        headroom = 0
        estimated_context = estimated_input + requested_output
        reasons.append("model_info.max_input_tokens is missing or invalid")
    else:
        headroom = _tokenizer_headroom_tokens(kwargs, context)
        estimated_context = estimated_input + requested_output + headroom
        if estimated_context > context:
            reasons.append(
                f"estimated_input_tokens={estimated_input} + "
                f"requested_output_tokens={requested_output} + "
                f"tokenizer_headroom_tokens={headroom} gives "
                f"estimated_context_tokens={estimated_context}, which exceeds "
                f"model_info.max_input_tokens={context}"
            )

    return {
        "allowed": not reasons,
        "estimated_input_tokens": estimated_input,
        "requested_output_tokens": requested_output,
        "tokenizer_headroom_tokens": headroom,
        "estimated_context_tokens": estimated_context,
        "max_input_tokens": context,
        "reasons": reasons,
    }


def enforce_context_window(kwargs: dict[str, Any]) -> dict[str, Any]:
    decision = gateway_context_window_decision(kwargs)
    if decision["allowed"]:
        return kwargs
    reason = "; ".join(decision["reasons"])
    message = f"claude-litellm context guard rejected request before provider dispatch: {reason}"
    if LiteLLMBadRequestError is not None:
        model = str(kwargs.get("model") or kwargs.get("deployment_model_name") or "unknown")
        raise LiteLLMBadRequestError(message=message, model=model, llm_provider="claude-litellm")
    raise GatewayContextWindowError(message)


def _context_guard_applies(call_type: Any) -> bool:
    if call_type is None:
        # LiteLLM reports None for an unknown call type. Unknown generation
        # paths must not become an escape hatch around the route contract.
        return True
    value = getattr(call_type, "value", call_type)
    return str(value) in _CONTEXT_GUARDED_CALL_TYPES


def gateway_cost_guardrail_decision(kwargs: dict[str, Any]) -> dict[str, Any]:
    policy = _read_cost_guardrail_policy()
    estimated_input = estimate_input_tokens(kwargs)
    requested_output = requested_output_tokens(kwargs)
    estimated_total = estimated_input + requested_output

    reasons: list[str] = []
    if not _bool_enabled(policy.get("enabled", True)):
        return {
            "allowed": True,
            "enabled": False,
            "estimated_input_tokens": estimated_input,
            "requested_output_tokens": requested_output,
            "estimated_total_tokens": estimated_total,
            "reasons": reasons,
        }

    max_input = _policy_value(policy, "max_estimated_input_tokens", "maxEstimatedInputTokens")
    max_total = _policy_value(policy, "max_estimated_total_tokens", "maxEstimatedTotalTokens")
    if max_input is not None and estimated_input > max_input:
        reasons.append(f"estimated_input_tokens={estimated_input} exceeds max_estimated_input_tokens={max_input}")
    if max_total is not None and estimated_total > max_total:
        reasons.append(f"estimated_total_tokens={estimated_total} exceeds max_estimated_total_tokens={max_total}")

    return {
        "allowed": not reasons,
        "enabled": True,
        "estimated_input_tokens": estimated_input,
        "requested_output_tokens": requested_output,
        "estimated_total_tokens": estimated_total,
        "max_estimated_input_tokens": max_input,
        "max_estimated_total_tokens": max_total,
        "reasons": reasons,
    }


def enforce_cost_guardrail(kwargs: dict[str, Any]) -> dict[str, Any]:
    decision = gateway_cost_guardrail_decision(kwargs)
    if decision["allowed"]:
        return kwargs
    reason = "; ".join(decision["reasons"])
    message = f"claude-litellm cost guardrail rejected request before provider dispatch: {reason}"
    if LiteLLMBadRequestError is not None:
        model = str(kwargs.get("model") or kwargs.get("deployment_model_name") or "unknown")
        raise LiteLLMBadRequestError(message=message, model=model, llm_provider="claude-litellm")
    raise GatewayCostGuardrailError(message)


class GatewayOutputClamp(CustomLogger):
    async def async_pre_call_deployment_hook(self, kwargs: dict[str, Any], call_type: Any) -> dict[str, Any]:
        enforce_reasoning_effort_policy(kwargs)
        # Clamp first so the guardrail prices the post-clamp request,
        # not the client's pre-clamp ask.
        clamped = clamp_token_reservations(kwargs)
        if _context_guard_applies(call_type):
            enforce_context_window(clamped)
        return enforce_cost_guardrail(clamped)


proxy_handler_instance = GatewayOutputClamp()
