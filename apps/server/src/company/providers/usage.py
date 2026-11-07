"""Payload-free call metadata and replay-safe cumulative usage for durable receipts."""

import hashlib
from dataclasses import dataclass
from uuid import uuid4

TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens")


@dataclass(frozen=True)
class CallUsage:
    call_id: str
    usage: dict[str, int]
    model: str | None
    elapsed_seconds: float | None


def observed_call(result) -> CallUsage:
    return CallUsage(
        str(getattr(result, "request_id", None) or uuid4()),
        {
            key: value
            for key, value in dict(result.usage).items()
            if key in TOKEN_FIELDS
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        },
        getattr(result, "model", None),
        getattr(result, "elapsed_seconds", None),
    )


def record_call(record, usage_attribute: str, call: CallUsage | None) -> bool:
    if call is None:
        return False
    usage = dict(getattr(record, usage_attribute) or {})
    marker = "_call_" + hashlib.sha256(call.call_id.encode()).hexdigest()[:32]
    if marker in usage:
        return False
    legacy_calls = int(any(key in usage for key in TOKEN_FIELDS) or record.model is not None)
    for key, value in call.usage.items():
        usage[key] = usage.get(key, 0) + value
    usage[marker] = 1
    usage["_model_calls"] = usage.get("_model_calls", legacy_calls) + 1
    setattr(record, usage_attribute, usage)
    record.model = call.model
    if call.elapsed_seconds is not None:
        record.elapsed_seconds = (record.elapsed_seconds or 0) + call.elapsed_seconds
    return True


def attach_call(error: Exception, call: CallUsage | None) -> CallUsage | None:
    existing = getattr(error, "call_usage", None)
    if existing is None and call is not None:
        error.call_usage = call
    return existing or call


class UsageTrackingProvider:
    def __init__(self, provider):
        self.inner, self.last_call = provider, None

    def __getattr__(self, name):
        return getattr(self.inner, name)

    async def generate_json(self, schema, messages, **kwargs):
        self.last_call = None
        try:
            output, result = await self.inner.generate_json(schema, messages, **kwargs)
        except Exception as error:
            self.last_call = getattr(error, "call_usage", None)
            raise
        self.last_call = observed_call(result)
        return output, result

    async def close(self):
        await self.inner.close()


def tracked_provider(provider):
    return (
        provider if isinstance(provider, UsageTrackingProvider) else UsageTrackingProvider(provider)
    )


def unwrapped_provider(provider):
    while isinstance(provider, UsageTrackingProvider):
        provider = provider.inner
    return provider
