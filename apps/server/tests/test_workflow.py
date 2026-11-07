"""Postgres-backed M3 acceptance, with an explicitly injected offline model."""

from types import SimpleNamespace
from uuid import UUID

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import func, select

from company.cli.init_db import initialize_database
from company.config import Settings
from company.persistence.agent_runs import AgentRun
from company.persistence.database import Database
from company.persistence.models import Command, HumanDecision, Job, Message, Order, now
from company.providers.mimo import ProviderError
from company.schemas.commands import DecisionInput
from company.schemas.orders import CreateOrder, MessageInput, ProductFact, SupplementInput
from company.services.orders import add_message, add_supplement, create_order, enqueue_decision
from company.services.workflow import process_one_job

pytestmark = pytest.mark.integration


class OfflineProvider:
    def __init__(self):
        self.calls = 0

    async def generate_json(self, schema, messages, **kwargs):
        self.calls += 1
        return schema.model_validate(
            {
                "ready_for_acceptance": True,
                "missing_fields": [],
                "conflicts": [],
                "questions": [],
                "summary": "已整理卖家资料；如需补充，请确认下列项目。",
            }
        ), SimpleNamespace(
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            model="offline-test-provider",
            elapsed_seconds=0.1,
        )


def product_facts(*, complete: bool = True):
    values = [
        ProductFact(key="material", value="silicone", confirmed=True),
        ProductFact(key="pack_quantity", value="6", confirmed=True),
        ProductFact(key="use_cases", value="indoor desk cable organization", confirmed=True),
    ]
    if complete:
        values.append(
            ProductFact(key="dimensions", value="25 x 25 x 15", unit="mm", confirmed=True)
        )
    return values


async def make_order(database: Database, settings: Settings, *, complete: bool) -> UUID:
    async with database.sessions() as session:
        created = await create_order(
            session,
            settings,
            CreateOrder(
                product_details="Known test product specifications.",
                product_name="虚构桌面理线夹",
                facts=product_facts(complete=complete),
            ),
        )
    return created.id


async def read_phase(database: Database, order_id: UUID) -> str:
    async with database.sessions() as session:
        return (await session.get(Order, order_id)).phase


async def receipt_counts(database: Database, order_id: UUID) -> tuple[int, int]:
    async with database.sessions() as session:
        messages = await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.order_id == order_id, Message.role == "assistant")
        )
        runs = await session.scalar(
            select(func.count())
            .select_from(AgentRun)
            .where(AgentRun.order_id == order_id, AgentRun.status == "succeeded")
        )
    return messages, runs


async def test_intake_wait_supplement_and_accept_use_persistent_graph(
    postgres_test_settings: Settings,
):
    settings = postgres_test_settings
    await initialize_database(settings)
    database = Database(settings)
    provider = OfflineProvider()
    try:
        order_id = await make_order(database, settings, complete=False)
        assert await process_one_job(
            settings, include_production=False, provider=provider, order_id=order_id
        )
        assert await read_phase(database, order_id) == "AWAITING_ACCEPTANCE"
        assert not await process_one_job(
            settings, include_production=False, provider=provider, order_id=order_id
        )
        assert provider.calls == 1  # Waiting without new input never polls the model.

        async with database.sessions() as session:
            updated = await add_supplement(
                session,
                order_id,
                SupplementInput(
                    expected_revision=1,
                    facts=product_facts(),
                ),
            )
        assert updated.revision == 2
        # A new process_one_job creates a new graph instance, restoring the DB checkpoint.
        assert await process_one_job(
            settings, include_production=False, provider=provider, order_id=order_id
        )
        assert await read_phase(database, order_id) == "AWAITING_ACCEPTANCE"
        async with database.sessions() as session:
            queued = await enqueue_decision(
                session,
                order_id,
                DecisionInput(
                    action="accept",
                    expected_revision=2,
                ),
                "test-m3-accept",
            )
        assert await process_one_job(
            settings, include_production=False, provider=provider, order_id=order_id
        )
        assert await read_phase(database, order_id) == "RESEARCH"
        async with database.sessions() as session:
            command = await session.get(Command, queued.command_id)
            decision = await session.scalar(
                select(HumanDecision).where(HumanDecision.command_id == queued.command_id)
            )
            assert command.status == decision.status == "applied"
        assert provider.calls == 2
    finally:
        await database.close()


async def test_reject_ends_order_without_fabricated_research(postgres_test_settings: Settings):
    settings = postgres_test_settings
    await initialize_database(settings)
    database = Database(settings)
    provider = OfflineProvider()
    try:
        order_id = await make_order(database, settings, complete=True)
        assert await process_one_job(
            settings, include_production=False, provider=provider, order_id=order_id
        )
        async with database.sessions() as session:
            await enqueue_decision(
                session,
                order_id,
                DecisionInput(
                    action="reject",
                    expected_revision=1,
                ),
                "test-m3-reject",
            )
        assert await process_one_job(
            settings, include_production=False, provider=provider, order_id=order_id
        )
        assert await read_phase(database, order_id) == "REJECTED"
        assert not await process_one_job(
            settings, include_production=False, provider=provider, order_id=order_id
        )
        assert provider.calls == 1
    finally:
        await database.close()


async def test_duplicate_wakeup_does_not_resume_the_next_wait(postgres_test_settings: Settings):
    settings = postgres_test_settings
    await initialize_database(settings)
    database = Database(settings)
    first_provider = OfflineProvider()
    try:
        order_id = await make_order(database, settings, complete=True)
        await process_one_job(
            settings, include_production=False, provider=first_provider, order_id=order_id
        )
        async with database.sessions() as session:
            start = await session.scalar(
                select(Command).where(Command.order_id == order_id, Command.kind == "START")
            )
            session.add(Job(order_id=order_id, command_id=start.id, kind="RESUME"))
            await session.commit()
        restarted_provider = OfflineProvider()
        assert await process_one_job(
            settings, include_production=False, provider=restarted_provider, order_id=order_id
        )
        assert restarted_provider.calls == 0
        assert await read_phase(database, order_id) == "AWAITING_ACCEPTANCE"
        assert await receipt_counts(database, order_id) == (1, 1)
    finally:
        await database.close()


async def test_result_committed_before_checkpoint_failure_is_not_generated_twice(
    postgres_test_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
):
    settings = postgres_test_settings
    await initialize_database(settings)
    database = Database(settings)
    provider = OfflineProvider()
    original_put = AsyncPostgresSaver.aput
    fault = {"injected": False}

    async def fail_after_intake(self, config, checkpoint, metadata, new_versions):
        if (
            not fault["injected"]
            and checkpoint.get("channel_values", {}).get("phase") == "AWAITING_ACCEPTANCE"
        ):
            fault["injected"] = True
            raise RuntimeError("injected failure must not appear in operator diagnostics")
        return await original_put(self, config, checkpoint, metadata, new_versions)

    monkeypatch.setattr(AsyncPostgresSaver, "aput", fail_after_intake)
    try:
        order_id = await make_order(database, settings, complete=False)
        assert await process_one_job(
            settings, include_production=False, provider=provider, order_id=order_id
        )
        assert fault["injected"]
        assert await receipt_counts(database, order_id) == (1, 1)
        async with database.sessions() as session:
            job = await session.scalar(select(Job).where(Job.order_id == order_id))
            assert job.status == "queued"
            job.available_at = now()
            await session.commit()
        assert await process_one_job(
            settings, include_production=False, provider=provider, order_id=order_id
        )
        assert provider.calls == 1
        assert await receipt_counts(database, order_id) == (1, 1)
        assert await read_phase(database, order_id) == "AWAITING_ACCEPTANCE"
    finally:
        await database.close()


async def test_retry_budget_exhaustion_is_not_implicitly_revived_by_a_new_message(
    postgres_test_settings: Settings,
):
    class UnavailableProvider:
        def __init__(self):
            self.calls = 0

        async def generate_json(self, schema, messages, **kwargs):
            self.calls += 1
            raise ProviderError("Test provider is temporarily unavailable.")

    settings = postgres_test_settings
    await initialize_database(settings)
    database = Database(settings)
    unavailable = UnavailableProvider()
    try:
        order_id = await make_order(database, settings, complete=True)
        for attempt in range(3):
            assert await process_one_job(
                settings, include_production=False, provider=unavailable, order_id=order_id
            )
            async with database.sessions() as session:
                job = await session.scalar(select(Job).where(Job.order_id == order_id))
                assert job.attempts == attempt + 1
                assert job.status == ("failed" if attempt == 2 else "queued")
                job.available_at = now()
                await session.commit()
        assert unavailable.calls == 3
        assert not await process_one_job(
            settings, include_production=False, provider=unavailable, order_id=order_id
        )
        async with database.sessions() as session:
            await add_message(
                session,
                order_id,
                MessageInput(
                    expected_revision=1,
                    content="请根据现有资料重新检查。",
                ),
            )
        replacement = OfflineProvider()
        assert await process_one_job(
            settings, include_production=False, provider=replacement, order_id=order_id
        )
        assert replacement.calls == 1
        assert unavailable.calls == 3
        assert await read_phase(database, order_id) == "AWAITING_ACCEPTANCE"
        assert await receipt_counts(database, order_id) == (1, 1)
    finally:
        await database.close()
