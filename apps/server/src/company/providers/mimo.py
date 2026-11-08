"""Small OpenAI-compatible MiMo boundary. Never returns credentials in errors."""

import asyncio
import base64
import json
import mimetypes
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, TypeVar

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, ValidationError

from company.config import REPO_ROOT, Settings, get_settings
from company.providers.usage import CallUsage, observed_call

T = TypeVar("T", bound=BaseModel)


class ProviderError(RuntimeError):
    """A sanitized error suitable for operator-facing diagnostics."""

    def __init__(self, message: str, *, call_usage: CallUsage | None = None):
        super().__init__(message)
        self.call_usage = call_usage


@dataclass(frozen=True)
class ModelResult:
    content: str
    usage: dict[str, int]
    elapsed_seconds: float
    model: str
    message: dict[str, Any]
    request_id: str | None = None


class MiMoProvider:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.trace_context: dict[str, str] = {}
        if self.settings.mimo_mode != "live":
            raise ProviderError("Live MiMo provider requires MIMO_MODE=live.")
        if not self.settings.mimo_api_key:
            raise ProviderError("MIMO_API_KEY is not configured in the backend environment.")
        self.client = AsyncOpenAI(
            api_key=self.settings.mimo_api_key.get_secret_value(),
            base_url=self.settings.mimo_base_url,
            timeout=self.settings.request_timeout_seconds,
            max_retries=0,  # The workflow owns its explicit attempt budget.
        )

    async def close(self) -> None:
        await self.client.close()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        json_mode: bool = False,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
    ) -> ModelResult:
        arguments: dict[str, Any] = {
            "model": self.settings.mimo_model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        if json_mode:
            arguments["response_format"] = {"type": "json_object"}
        if tools:
            arguments.update(tools=tools, tool_choice="auto")
        started = perf_counter()
        for attempt in range(2):
            try:
                response = await self.client.chat.completions.create(**arguments)
                break
            except APIStatusError as error:
                if error.status_code in {429, 500, 502, 503, 504} and attempt == 0:
                    await asyncio.sleep(1)
                    continue
                raise ProviderError(
                    f"MiMo HTTP {error.status_code}; "
                    "check authorization, quota, model and endpoint."
                ) from None
            except (APITimeoutError, APIConnectionError):
                if attempt == 0:
                    await asyncio.sleep(1)
                    continue
                raise ProviderError("MiMo connection timed out or is unavailable.") from None
        choice = response.choices[0] if response.choices else None
        message = choice.message.model_dump(exclude_none=True) if choice else {}
        usage = response.usage
        result = ModelResult(
            content=(choice.message.content or "") if choice else "",
            usage={
                "input_tokens": usage.prompt_tokens,
                "output_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            }
            if usage
            else {},
            elapsed_seconds=round(perf_counter() - started, 3),
            model=response.model,
            message=message,
            request_id=response.id,
        )
        # Record every completed API call, including responses rejected later by domain checks.
        # This contains identifiers and usage only, never prompts, responses or credentials.
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "request_id": response.id,
            "model": result.model,
            "usage": result.usage,
            "elapsed_seconds": result.elapsed_seconds,
            **{
                key: value
                for key, value in self.trace_context.items()
                if key in {"order_id", "command_id", "role", "step_key"}
            },
        }
        try:
            destination = REPO_ROOT / "var" / "reports" / "model-calls.jsonl"
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            # Optional diagnostics must not turn a successful model call into a duplicate retry.
            pass
        if choice is None:
            raise ProviderError(
                "MiMo returned no completion choices.", call_usage=observed_call(result)
            )
        if choice.finish_reason in {"length", "content_filter", "repetition_truncation"}:
            raise ProviderError(
                f"MiMo output is incomplete ({choice.finish_reason}).",
                call_usage=observed_call(result),
            )
        return result

    async def generate_json(
        self, schema: type[T], messages: list[dict[str, Any]], *, max_tokens: int = 4096
    ) -> tuple[T, ModelResult]:
        instruction = {
            "role": "system",
            "content": "Return only a JSON object matching this schema. Unknown facts must not "
            "be invented. Schema: " + json.dumps(schema.model_json_schema(), ensure_ascii=False),
        }
        result = await self.chat([instruction, *messages], json_mode=True, max_tokens=max_tokens)
        try:
            parsed = schema.model_validate_json(result.content)
        except ValidationError as error:
            fields = "; ".join(
                f"{'.'.join(str(p) for p in item['loc'])}: {item['type']}"
                for item in error.errors(include_input=False, include_url=False)[:6]
            )
            raise ProviderError(
                f"MiMo JSON did not match the required application schema ({fields}).",
                call_usage=observed_call(result),
            ) from None
        return parsed, result

    @staticmethod
    def image_content(image_path: Path) -> dict[str, Any]:
        media_type = mimetypes.guess_type(image_path.name)[0]
        if media_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ProviderError("Only PNG, JPEG and WebP image inputs are supported.")
        if image_path.stat().st_size > 10 * 1024 * 1024:
            raise ProviderError("Image input exceeds the 10 MiB application limit.")
        encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
        return {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{encoded}"}}
