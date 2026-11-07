"""Completed but rejected calls still count; no test contacts a remote model."""

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel, SecretStr
from sqlalchemy import select
from test_delivery import delivery_context as delivery_context
from test_delivery_workflow import bundle_context as bundle_context
from test_feedback_workflow import FullProvider, delivered_base, feedback
from test_production_persistence import ProductionStub
from test_production_persistence import production_context as production_context
from test_publication import PlanProvider, confirm, prepare

from company.config import Settings
from company.persistence.agent_runs import AgentRun
from company.persistence.models import Job, Order, now
from company.persistence.production import ProductionStepReceipt
from company.persistence.publication import PublishAttempt
from company.providers.mimo import MiMoProvider, ProviderError
from company.providers.usage import CallUsage, record_call
from company.schemas.research import ResearchDraft
from company.services.model_budget import BudgetedProvider, OrderBudgetExceeded
from company.services.production import research_step
from company.services.publication import PublicationError, publish_confirmed
from company.services.support import order_metrics
from company.services.workflow import process_one_job


class SmallSchema(BaseModel):
    ok: bool


@pytest.mark.parametrize("finish_reason", ["length", "stop"])
async def test_completed_length_and_invalid_json_retain_safe_metadata_and_trace(
    tmp_path, monkeypatch, finish_reason
):
    monkeypatch.setattr("company.providers.mimo.REPO_ROOT", tmp_path)
    sent = []

    async def reply(request):
        sent.append(request)
        return httpx.Response(
            200,
            json={
                "id": f"offline-{finish_reason}",
                "object": "chat.completion",
                "created": 1,
                "model": "offline-response-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "PRIVATE-RESPONSE-MARKER"},
                        "finish_reason": finish_reason,
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            },
        )

    settings = Settings(_env_file=None, mimo_api_key=SecretStr("offline-test-key"))
    provider = MiMoProvider(settings)
    await provider.client.close()
    provider.client = AsyncOpenAI(
        api_key="offline-test-key",
        base_url="https://offline.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply)),
    )
    provider.trace_context = {
        "order_id": "offline-order",
        "role": "review",
        "unapproved_context": "PRIVATE-CONTEXT-MARKER",
    }
    try:
        with pytest.raises(ProviderError) as failed:
            await provider.generate_json(
                SmallSchema, [{"role": "user", "content": "PRIVATE-PROMPT-MARKER"}]
            )
        metadata = failed.value.call_usage
        assert metadata.usage == {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
        assert metadata.model == "offline-response-model" and metadata.elapsed_seconds >= 0
        assert metadata.call_id == f"offline-{finish_reason}" and len(sent) == 1
        assert "PRIVATE" not in str(failed.value) and "offline-test-key" not in str(failed.value)
        trace = (tmp_path / "var/reports/model-calls.jsonl").read_text()
        record = json.loads(trace)
        assert record["usage"]["total_tokens"] == 10 and record["role"] == "review"
        assert "PRIVATE" not in trace and "offline-test-key" not in trace
    finally:
        await provider.close()


def test_cumulative_usage_deduplicates_a_replayed_call_and_keeps_legacy_usage():
    row = SimpleNamespace(usage={"total_tokens": 5}, model="legacy", elapsed_seconds=0.2)
    call = CallUsage("call-1", {"total_tokens": 10}, "current", 0.1)
    assert record_call(row, "usage", call)
    assert not record_call(row, "usage", call)
    assert row.usage["total_tokens"] == 15 and row.usage["_model_calls"] == 2
    assert row.elapsed_seconds == pytest.approx(0.3)


@pytest.mark.integration
async def test_failed_production_usage_accumulates_and_blocks_budget_before_retry(
    production_context,
):
    settings, database, order_id, command_id, _, _ = production_context

    class FailOnce(ProductionStub):
        failed = False

        async def generate_json(self, schema, messages, **kwargs):
            if not self.failed:
                self.failed = True
                self.calls.append(schema.__name__)
                raise ProviderError(
                    "Offline rejected JSON",
                    call_usage=CallUsage(
                        "failed-research-call",
                        {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10},
                        "offline-failed-model",
                        0.2,
                    ),
                )
            return await super().generate_json(schema, messages, **kwargs)

    provider = FailOnce()
    with pytest.raises(ProviderError):
        await research_step(order_id, command_id, provider, database=database)
    guarded = BudgetedProvider(
        provider, database, settings.model_copy(update={"order_token_budget": 10}), order_id
    )
    with pytest.raises(OrderBudgetExceeded):
        await research_step(order_id, command_id, guarded, database=database)
    assert len(provider.calls) == 1
    await research_step(order_id, command_id, provider, database=database)
    async with database.sessions() as session:
        receipt = await session.scalar(
            select(ProductionStepReceipt).where(ProductionStepReceipt.command_id == command_id)
        )
        assert receipt.status == "succeeded" and receipt.usage["total_tokens"] == 25
        assert receipt.usage["_model_calls"] == 2 and receipt.cost_usd is None
        metrics = await order_metrics(session, order_id)
        assert metrics.tokens.total_tokens == 25 and metrics.recorded_model_calls == 2
    assert (await research_step(order_id, command_id, provider, database=database)).cache_hit
    assert len(provider.calls) == 2


@pytest.mark.integration
async def test_post_model_domain_rejection_preserves_actual_call_usage(production_context):
    _, database, order_id, command_id, _, _ = production_context

    class BadReference(ProductionStub):
        async def generate_json(self, schema, messages, **kwargs):
            result, metadata = await super().generate_json(schema, messages, **kwargs)
            assert schema is ResearchDraft
            result.buyer_questions[0].fact_ids = ["fact:nonexistent"]
            return result, metadata

    with pytest.raises(ProviderError):
        await research_step(order_id, command_id, BadReference(), database=database)
    async with database.sessions() as session:
        receipt = await session.scalar(
            select(ProductionStepReceipt).where(ProductionStepReceipt.command_id == command_id)
        )
        assert receipt.status == "failed" and receipt.usage["total_tokens"] == 15
        assert receipt.usage["_model_calls"] == 1


@pytest.mark.integration
async def test_feedback_failure_then_success_keeps_both_model_calls(bundle_context):
    settings, database, order_id, _, old = await delivered_base(bundle_context)
    queued = await feedback(database, order_id, old, "请让措辞更简洁。")

    class FailFeedbackOnce(FullProvider):
        failed = False

        async def generate_json(self, schema, messages, **kwargs):
            if schema.__name__ == "FeedbackDraft" and not self.failed:
                self.failed = True
                raise ProviderError(
                    "Offline incomplete feedback",
                    call_usage=CallUsage(
                        "failed-feedback-call",
                        {"total_tokens": 10},
                        "offline-failed",
                        0.2,
                    ),
                )
            return await super().generate_json(schema, messages, **kwargs)

    provider = FailFeedbackOnce()
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    async with database.sessions() as session, session.begin():
        run = await session.scalar(select(AgentRun).where(AgentRun.command_id == queued.command_id))
        assert run.status == "failed" and run.usage["total_tokens"] == 10
        (
            await session.scalar(select(Job).where(Job.command_id == queued.command_id))
        ).available_at = now()
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    async with database.sessions() as session:
        run = await session.scalar(select(AgentRun).where(AgentRun.command_id == queued.command_id))
        assert run.status == "succeeded" and run.usage["total_tokens"] == 22
        assert run.usage["_model_calls"] == 2
        assert (await session.get(Order, order_id)).phase == "AWAITING_RELEASE"


@pytest.mark.integration
async def test_intake_failure_then_success_keeps_actual_usage_from_both_attempts(bundle_context):
    settings, database, _, create = bundle_context
    order_id = await create()

    class FailIntakeOnce(FullProvider):
        failed = False

        async def generate_json(self, schema, messages, **kwargs):
            if not self.failed:
                self.failed = True
                raise ProviderError(
                    "Offline truncated intake",
                    call_usage=CallUsage(
                        "failed-intake-call",
                        {"total_tokens": 10},
                        "offline-failed",
                        0.2,
                    ),
                )
            return await super().generate_json(schema, messages, **kwargs)

    provider = FailIntakeOnce()
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    async with database.sessions() as session, session.begin():
        run = await session.scalar(select(AgentRun).where(AgentRun.order_id == order_id))
        assert run.status == "failed" and run.usage["total_tokens"] == 10
        (await session.scalar(select(Job).where(Job.order_id == order_id))).available_at = now()
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    async with database.sessions() as session:
        run = await session.scalar(select(AgentRun).where(AgentRun.order_id == order_id))
        assert run.status == "succeeded" and run.usage["total_tokens"] == 25
        assert run.usage["_model_calls"] == 2


@pytest.mark.integration
async def test_revoked_lease_records_paid_usage_without_applying_stale_intake_result(
    bundle_context,
):
    settings, database, _, create = bundle_context
    order_id = await create()

    class RevokedLease(FullProvider):
        async def generate_json(self, schema, messages, **kwargs):
            output = await super().generate_json(schema, messages, **kwargs)
            async with database.sessions() as session, session.begin():
                job = await session.scalar(select(Job).where(Job.order_id == order_id))
                job.lease_generation += 1
            return output

    assert await process_one_job(settings, provider=RevokedLease(), order_id=order_id)
    async with database.sessions() as session:
        run = await session.scalar(select(AgentRun).where(AgentRun.order_id == order_id))
        job = await session.scalar(select(Job).where(Job.order_id == order_id))
        assert run.usage["total_tokens"] == 15 and run.usage["_model_calls"] == 1
        assert run.result is None and run.status == "running"
        assert job.status == "running" and job.lease_generation == 2
        assert (await session.get(Order, order_id)).phase == "INTAKE"


@pytest.mark.integration
async def test_publication_failure_then_success_accumulates_plan_usage(delivery_context):
    prepared = await prepare(delivery_context)
    settings, app, item, _, _, _ = prepared
    queued = await confirm(prepared)

    class FailOnce(PlanProvider):
        failed = False

        async def generate_json(self, schema, messages, **kwargs):
            if not self.failed:
                self.failed = True
                raise ProviderError(
                    "Offline incomplete plan",
                    call_usage=CallUsage(
                        "failed-publish-call",
                        {"total_tokens": 10},
                        "offline-failed",
                        0.2,
                    ),
                )
            return await super().generate_json(schema, messages, **kwargs)

    provider = FailOnce()
    with pytest.raises(PublicationError):
        await publish_confirmed(
            settings,
            item.order_id,
            queued["command_id"],
            database=app.state.database,
            provider=provider,
        )
    result = await publish_confirmed(
        settings,
        item.order_id,
        queued["command_id"],
        database=app.state.database,
        provider=provider,
    )
    assert result.status == "SIMULATED"
    async with app.state.database.sessions() as session:
        attempt = await session.scalar(
            select(PublishAttempt).where(PublishAttempt.order_id == item.order_id)
        )
        assert attempt.plan_usage["total_tokens"] == 32 and attempt.plan_usage["_model_calls"] == 2
        assert attempt.cost_usd is None
