"""Run four explicit, non-destructive real-model probes; retain a safe report."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict

from company.config import REPO_ROOT
from company.providers.mimo import MiMoProvider, ProviderError
from company.runtime import configure_asyncio


class JsonProbe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["ok"]
    probe: Literal["structured"]


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: Literal["demo-cable-clip"]


def fixture_image() -> Path:
    destination = REPO_ROOT / "fixtures" / "demo" / "reference.png"
    if not destination.exists():
        picture = Image.new("RGB", (720, 440), "#f8f6ef")
        draw = ImageDraw.Draw(picture)
        draw.text((25, 25), "FICTIONAL DEMO - cable clip diagram", fill="#222222")
        for y in (135, 265):
            for x in (125, 345, 565):
                draw.rounded_rectangle((x - 58, y - 35, x + 58, y + 35), radius=28, fill="#242528")
                draw.rectangle((x - 5, y - 40, x + 5, y + 14), fill="#f8f6ef")
        draw.text((25, 395), "Invented fixture. Not a product photograph.", fill="#222222")
        picture.save(destination)
    return destination


async def run_probes() -> dict:
    provider = MiMoProvider()
    report = {
        "mode": "live",
        "model": provider.settings.mimo_model,
        "created_at": datetime.now(UTC).isoformat(),
        "checks": [],
    }

    async def check(name, run):
        try:
            detail = await run()
            report["checks"].append({"name": name, "passed": True, **detail})
            print(f"PASS {name}", flush=True)
        except ProviderError as error:
            report["checks"].append({"name": name, "passed": False, "error": str(error)})
            print(f"FAIL {name}: {error}", flush=True)
        except Exception:
            report["checks"].append(
                {
                    "name": name,
                    "passed": False,
                    "error": "Probe validation failed; no raw API data logged.",
                }
            )
            print(f"FAIL {name}: validation failed", flush=True)

    def metadata(result):
        return {"usage": result.usage, "elapsed_seconds": result.elapsed_seconds}

    async def text_probe():
        result = await provider.chat([{"role": "user", "content": "Reply exactly MIMO_OK."}])
        if "MIMO_OK" not in result.content:
            raise ProviderError("Expected text marker was absent.")
        return metadata(result)

    async def json_probe():
        _, result = await provider.generate_json(
            JsonProbe,
            [{"role": "user", "content": 'Return JSON with status="ok" and probe="structured".'}],
        )
        return metadata(result)

    async def image_probe():
        result = await provider.chat(
            [
                {
                    "role": "user",
                    "content": [
                        provider.image_content(fixture_image()),
                        {
                            "type": "text",
                            "text": "Read the large label at the top of this image. "
                            "Does it say FICTIONAL DEMO? Include that exact label in your answer.",
                        },
                    ],
                }
            ]
        )
        if "FICTIONAL DEMO" not in result.content.upper():
            raise ProviderError("The image label was not correctly recognized.")
        return metadata(result)

    async def tool_probe():
        messages = [
            {
                "role": "user",
                "content": "Call verify_demo_record with "
                "product_id demo-cable-clip. Then report its returned status verbatim.",
            }
        ]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "verify_demo_record",
                    "description": "Read a fictional fixture record with no external side effects.",
                    "parameters": ToolInput.model_json_schema(),
                },
            }
        ]
        first = await provider.chat(messages, tools=tools)
        calls = first.message.get("tool_calls", [])
        if not calls:
            raise ProviderError("Model did not request the expected tool.")
        messages.append(first.message)
        for call in calls:
            function = call["function"]
            if function["name"] != "verify_demo_record":
                raise ProviderError("Model requested a tool outside the allowlist.")
            ToolInput.model_validate_json(function["arguments"])
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": json.dumps({"status": "DEMO_RECORD_OK"}),
                }
            )
        second = await provider.chat(messages)
        if "DEMO_RECORD_OK" not in second.content:
            raise ProviderError("Model did not consume the returned tool result.")
        return {
            "tool_calls": len(calls),
            "usage": {key: first.usage[key] + second.usage[key] for key in first.usage},
            "elapsed_seconds": round(first.elapsed_seconds + second.elapsed_seconds, 3),
        }

    try:
        for name, run in [
            ("text", text_probe),
            ("json", json_probe),
            ("image_understanding", image_probe),
            ("tool_call", tool_probe),
        ]:
            await check(name, run)
    finally:
        await provider.close()
    report["passed"] = all(item["passed"] for item in report["checks"])
    output = REPO_ROOT / "var" / "reports" / "m0-mimo.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Safe report: var/reports/m0-mimo.json", flush=True)
    return report


def main() -> None:
    configure_asyncio()
    try:
        report = asyncio.run(run_probes())
    except ProviderError as error:
        print(str(error))
        raise SystemExit(1) from None
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
