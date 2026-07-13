"""LiteLLM 1.92.0 compatibility for ChatGPT's empty completed output.

ChatGPT's Codex backend can stream the real assistant items in
``response.output_item.done`` events and then send ``response.completed`` with
``output: []``.  LiteLLM 1.92.0 discards those earlier items, so its
chat-completions bridge later fails with ``Unknown items ... []``.

The upstream fix is still unmerged (BerriAI/litellm#31332).  Keep this local
shim deliberately narrow: it applies only to exactly LiteLLM 1.92.0 and only
to ``chatgpt`` streams.  Remove it after upgrading to a release containing the
upstream output-recovery implementation.
"""

from __future__ import annotations

import importlib.metadata
import inspect
import json
from typing import Any


EXPECTED_LITELLM_VERSION = "1.92.0"
_STATE_ATTRIBUTE = "_claude_litellm_chatgpt_streamed_output_items"
_PATCH_MARKER = "_claude_litellm_chatgpt_output_recovery"


def _installed_litellm_version() -> str | None:
    try:
        return importlib.metadata.version("litellm")
    except importlib.metadata.PackageNotFoundError:
        return None


LITELLM_VERSION = _installed_litellm_version()
PATCH_REQUIRED = LITELLM_VERSION == EXPECTED_LITELLM_VERSION


def _is_chatgpt_stream(iterator: Any) -> bool:
    provider = getattr(iterator, "custom_llm_provider", None)
    model = getattr(iterator, "model", "")
    return provider == "chatgpt" or (
        isinstance(model, str) and model.startswith("chatgpt/")
    )


def _plain_output_item(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return dict(item)
    model_dump = getattr(item, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        if isinstance(dumped, dict):
            return dumped
    raise TypeError("streamed output item is not serializable")


def install_chatgpt_stream_output_recovery() -> bool:
    """Install the exact-version stream recovery shim, if it is required."""
    if not PATCH_REQUIRED:
        return False

    try:
        from litellm._logging import verbose_logger
        from litellm.responses.streaming_iterator import (
            BaseResponsesAPIStreamingIterator,
        )
    except Exception as exc:
        raise RuntimeError(
            "LiteLLM 1.92.0 streaming internals changed; refusing to install "
            "the ChatGPT output-recovery compatibility patch"
        ) from exc

    original = BaseResponsesAPIStreamingIterator._process_chunk
    if getattr(original, _PATCH_MARKER, False):
        return True
    if tuple(inspect.signature(original).parameters) != ("self", "chunk"):
        raise RuntimeError(
            "LiteLLM 1.92.0 _process_chunk signature changed; refusing to install "
            "the ChatGPT output-recovery compatibility patch"
        )

    def process_chunk_with_chatgpt_output_recovery(
        self: Any, chunk: Any
    ) -> Any:
        scoped = _is_chatgpt_stream(self)
        parsed: dict[str, Any] | None = None
        forwarded_chunk = chunk

        if scoped:
            try:
                candidate = json.loads(chunk)
                if isinstance(candidate, dict):
                    parsed = candidate
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                parsed = None

        if parsed is not None:
            event_type = parsed.get("type")
            if event_type == "response.created":
                setattr(self, _STATE_ATTRIBUTE, {})
            elif event_type in ("response.completed", "response.incomplete"):
                response = parsed.get("response")
                streamed_items = getattr(self, _STATE_ATTRIBUTE, {})
                if (
                    isinstance(response, dict)
                    and not response.get("output")
                    and isinstance(streamed_items, dict)
                    and streamed_items
                ):
                    try:
                        recovered = [
                            _plain_output_item(item)
                            for _, item in sorted(streamed_items.items())
                        ]
                        patched_event = dict(parsed)
                        patched_response = dict(response)
                        patched_response["output"] = recovered
                        patched_event["response"] = patched_response
                        forwarded_chunk = json.dumps(
                            patched_event,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    except Exception:
                        # Preserve upstream behavior if an unexpected item cannot
                        # be represented.  Never fail an otherwise valid stream.
                        verbose_logger.warning(
                            "chatgpt_stream_compat: failed to recover completed output",
                            exc_info=True,
                        )

        result = original(self, forwarded_chunk)

        if scoped and getattr(result, "type", None) == "response.output_item.done":
            item = getattr(result, "item", None)
            if item is not None:
                streamed_items = getattr(self, _STATE_ATTRIBUTE, None)
                if not isinstance(streamed_items, dict):
                    streamed_items = {}
                    setattr(self, _STATE_ATTRIBUTE, streamed_items)
                output_index = getattr(result, "output_index", None)
                if not isinstance(output_index, int) or isinstance(output_index, bool):
                    output_index = max(streamed_items, default=-1) + 1
                streamed_items[output_index] = item

        return result

    setattr(process_chunk_with_chatgpt_output_recovery, _PATCH_MARKER, True)
    BaseResponsesAPIStreamingIterator._process_chunk = (
        process_chunk_with_chatgpt_output_recovery
    )
    return True


PATCH_ACTIVE = install_chatgpt_stream_output_recovery()
