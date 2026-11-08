import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel, SecretStr

from company.config import Settings
from company.providers.ark import ArkProvider
from company.providers.mimo import ProviderError
from company.services.workflow import WorkflowContext


class Output(BaseModel):
    message: str


async def test_research_source_aliases_round_trip_to_real_ids_without_mutating_inputs():
    from company.schemas.research import ResearchDraft

    source_id = "11111111-1111-4111-8111-111111111111"
    payload = {
        "evidence": [{"id": source_id, "content": "Seller statement."}],
        "confirmed_facts": [{"id": "fact:brand", "key": "brand", "value": "Ganten"}],
    }
    messages = [{"role": "user", "content": json.dumps(payload)}]

    def respond(request):
        body = json.loads(request.content)
        supplied = json.loads(body["input"][-1]["content"])
        assert supplied["evidence"][0]["id"] == "evidence:1"
        assert '"enum": ["evidence:1", "fact:brand"]' in body["input"][0]["content"]
        output = {
            "buyer_questions": [
                {
                    "question": "What is known?",
                    "applicable": True,
                    "evidence_ids": ["evidence:1"],
                    "fact_ids": ["fact:brand"],
                }
            ]
        }
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "model": body["model"],
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": json.dumps(output)}],
                    }
                ],
            },
        )

    provider = ArkProvider(
        Settings(_env_file=None, ark_api_key=SecretStr("test-key")),
        httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    try:
        output, _ = await provider.generate_json(ResearchDraft, messages)
        assert output.buyer_questions[0].evidence_ids == [source_id]
        assert json.loads(messages[0]["content"])["evidence"][0]["id"] == source_id
    finally:
        await provider.close()


async def test_kimi_uses_bounded_reasoning_without_doubao_thinking_parameter():
    def respond(request):
        body = json.loads(request.content)
        assert "thinking" not in body
        assert body["reasoning"] == {"effort": "low"}
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "model": body["model"],
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": '{"message":"checked"}'}],
                    }
                ],
            },
        )

    settings = Settings(
        _env_file=None,
        ark_api_key=SecretStr("private-test-key"),
        ark_research_model="kimi-k3-1-pro-261024",
    )
    p = ArkProvider(settings, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    try:
        result, call = await p.generate_json(Output, [{"role": "user", "content": "Analyze"}])
        assert result.message == "checked" and call.usage == {}
    finally:
        await p.close()


async def test_ark_research_json_usage_and_request_contract():
    def respond(request):
        body = json.loads(request.content)
        assert request.url.path == "/api/v3/responses"
        assert body["model"] == "doubao-seed-2-0-mini-260428"
        assert "tools" not in body  # Analysis does not pretend to be online research.
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "id": "request-1",
                "model": body["model"],
                "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30},
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": '{"message":"source-aware analysis"}'}
                        ],
                    }
                ],
            },
        )

    settings = Settings(_env_file=None, ark_api_key=SecretStr("private-test-key"))
    p = ArkProvider(settings, httpx.AsyncClient(transport=httpx.MockTransport(respond)))
    try:
        output, call = await p.generate_json(Output, [{"role": "user", "content": "Analyze"}])
        assert output.message == "source-aware analysis" and call.usage["total_tokens"] == 30
    finally:
        await p.close()


async def test_ark_error_never_exposes_credential_or_provider_body():
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                404, json={"error": {"code": "ToolNotOpen", "message": "private-test-key"}}
            )
        )
    )
    p = ArkProvider(Settings(_env_file=None, ark_api_key=SecretStr("private-test-key")), client)
    try:
        with pytest.raises(ProviderError) as error:
            await p.response([{"role": "user", "content": "Search"}])
        assert "HTTP 404" in str(error.value) and "private-test-key" not in str(error.value)
    finally:
        await p.close()


async def test_research_routes_to_ark_but_injected_test_provider_remains_authoritative():
    settings = Settings(
        _env_file=None,
        app_env="development",
        mimo_mode="live",
        research_provider="ark",
        ark_api_key=SecretStr("private-test-key"),
    )
    lease = SimpleNamespace(order_id="order", command_id="command")
    context = WorkflowContext(settings, None, lease)
    try:
        assert isinstance(context._provider("research").inner, ArkProvider)
        assert context.provider is None
    finally:
        await context.close()
    injected = SimpleNamespace(last_call=None)
    context = WorkflowContext(settings, None, lease, provider=injected)
    assert context._provider("research").inner is injected
