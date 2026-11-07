"""M6 feedback/publication graph, with real PostgreSQL and explicit offline model injection."""

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select
from test_delivery_workflow import (
    BundleProvider,
    accepted,
    candidates,
    upload,
)
from test_delivery_workflow import (
    bundle_context as bundle_context,
)

from company.persistence.agent_runs import AgentRun
from company.persistence.models import Command, InputSnapshot, Job, Message, Order, now
from company.persistence.production import Evidence
from company.persistence.publication import FeedbackRequest, PublishAttempt, SellerConfirmation
from company.persistence.support import SupportTicket
from company.providers.publishers.simulated import SimulatedPublisher
from company.schemas.commands import DecisionInput
from company.schemas.publication import ConfirmationInput, FeedbackInput
from company.services.orders import enqueue_decision
from company.services.production import accepted_revision
from company.services.publication import PublicationError, queue_confirmation, queue_feedback
from company.services.workflow import process_one_job

pytestmark = pytest.mark.integration


class FullProvider(BundleProvider):
    def __init__(self, *, bad_feedback_candidates=False):
        super().__init__()
        self.bad_feedback_candidates = bad_feedback_candidates

    async def generate_json(self, schema, messages, **kwargs):
        if schema.__name__ == "FeedbackDraft":
            self.calls.append(schema.__name__)
            output = {
                "category": "style",
                "reason": "整理卖家修改意见。",
                "questions": [],
                "summary": "已记录修改意见，等待检查。",
            }
        elif schema.__name__ == "PublishPlan":
            self.calls.append(schema.__name__)
            source = json.loads(messages[-1]["content"])
            output = {key: source[key] for key in schema.model_fields}
        elif schema.__name__ == "ListingDraft":
            result, metadata = await super().generate_json(schema, messages, **kwargs)
            source = json.loads(messages[-1]["content"])
            facts = {fact["key"]: fact for fact in source["confirmed_facts"]}
            quantity = int(facts["pack_quantity"]["value"])
            if source.get("feedback") and self.bad_feedback_candidates:
                quantity += 1
            output = result.model_dump()
            output["title"] = f"{quantity}-Pack Silicone Cable Clips"
            output["bullets"][0] = "For cables up to 6 mm in diameter."
            output["bullets"][4] = f"{quantity} clips included."
            output["claims"] = [
                {
                    "field_path": "title",
                    "text": output["title"],
                    "fact_ids": ["fact:pack_quantity", "fact:material"],
                },
                {"field_path": "bullets[0]", "text": "6 mm", "fact_ids": ["fact:cable_diameter"]},
                {
                    "field_path": "bullets[4]",
                    "text": f"{quantity} clips",
                    "fact_ids": ["fact:pack_quantity"],
                },
            ]
            return schema.model_validate(output), metadata
        else:
            return await super().generate_json(schema, messages, **kwargs)
        return schema.model_validate(output), SimpleNamespace(
            usage={"input_tokens": 8, "output_tokens": 4, "total_tokens": 12},
            model="offline-m6-provider",
            elapsed_seconds=0.01,
        )


async def released(settings, database, order_id, version, key):
    async with database.sessions() as session:
        await enqueue_decision(
            session,
            order_id,
            DecisionInput(
                action="release",
                expected_revision=(await session.get(Order, order_id)).revision,
                version_id=version.id,
            ),
            key,
        )
    assert await process_one_job(settings, provider=FullProvider(), order_id=order_id)


async def delivered_base(bundle_context, provider=None):
    settings, database, client, create = bundle_context
    order_id = await create()
    async with database.sessions() as session, session.begin():
        snapshot = await session.scalar(
            select(InputSnapshot).where(
                InputSnapshot.order_id == order_id,
                InputSnapshot.revision == 1,
            )
        )
        snapshot.facts = [
            *snapshot.facts,
            {"key": "cable_diameter", "value": "6", "unit": "mm", "confirmed": True},
        ]
    seller, headers = await client(order_id)
    await upload(seller, headers, order_id)
    provider = provider or FullProvider()
    await accepted(settings, database, order_id, provider)
    version = (await candidates(database, order_id))[0]
    assert version.full_passed
    await released(settings, database, order_id, version, "base-release")
    return settings, database, order_id, provider, version


async def feedback(database, order_id, version, content, *, fact_updates=None, revision=1):
    async with database.sessions() as session:
        return await queue_feedback(
            session,
            order_id,
            FeedbackInput(
                expected_revision=revision,
                version_id=version.id,
                content=content,
                fact_updates=fact_updates,
            ),
            f"feedback-{uuid4()}",
        )


async def confirm(database, settings, order_id, version, *, revision=1):
    async with database.sessions() as session:
        return await queue_confirmation(
            session,
            settings,
            order_id,
            ConfirmationInput(
                expected_revision=revision,
                version_id=version.id,
                listing_price="12.99",
                currency="USD",
                seller_sku="DEMO-CLIPS",
            ),
            f"confirm-{uuid4()}",
        )


async def test_style_feedback_reuses_research_then_releases_confirms_price_and_publishes(
    bundle_context,
):
    settings, database, order_id, provider, old = await delivered_base(bundle_context)
    async with database.sessions() as session, session.begin():
        session.add(
            Evidence(
                order_id=order_id,
                kind="competitor",
                source="fixture observation",
                provider="manual",
                status="success",
                observed_at=now(),
                scope="style-only fixture",
                is_demo=True,
                content="Fictional additional observation; not a product fact.",
                data={},
            )
        )
    queued = await feedback(database, order_id, old, "请让标题读起来更简洁，保持商品事实不变。")
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    old, new = await candidates(database, order_id)
    assert new.full_passed and new.parent_version_id == old.id
    assert new.research_package_id == old.research_package_id
    assert provider.calls.count("ResearchDraft") == 1
    async with database.sessions() as session:
        request = await session.get(FeedbackRequest, queued.feedback_id)
        run = await session.scalar(
            select(AgentRun).where(
                AgentRun.command_id == queued.command_id,
                AgentRun.role == "acquisition_feedback",
            )
        )
        assert request.candidate_count == 1 and request.candidate_limit == 3
        assert run.status == "succeeded" and run.usage["total_tokens"] == 12
        assert run.result["category"] == "style"
    await released(settings, database, order_id, new, "feedback-release")
    confirmation = await confirm(database, settings, order_id, new)
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    async with database.sessions() as session:
        order = await session.get(Order, order_id)
        attempt = await session.scalar(
            select(PublishAttempt).where(PublishAttempt.order_id == order_id)
        )
        approved = await session.get(SellerConfirmation, confirmation.confirmation_id)
        assert order.phase == "COMPLETED" and attempt.status == "SIMULATED"
        assert attempt.submit_count == 1 and approved.snapshot["listing_price"] == "12.99"
        assert attempt.cost_usd is None and provider.calls.count("PublishPlan") == 1


async def test_explicit_fact_snapshot_six_to_four_keeps_six_mm_and_invalidates_old_confirmation(
    bundle_context,
):
    settings, database, order_id, provider, old = await delivered_base(bundle_context)
    old_approval = await confirm(database, settings, order_id, old)
    # A previous failed/cancelled publish operation has returned control to the seller.
    async with database.sessions() as session, session.begin():
        command = await session.get(Command, old_approval.command_id)
        command.status = "cancelled"
        (await session.scalar(select(Job).where(Job.command_id == command.id))).status = "failed"
        (await session.get(Order, order_id)).pending_command_id = None
        snapshot = await session.scalar(
            select(InputSnapshot).where(InputSnapshot.order_id == order_id)
        )
        updates = [dict(fact) for fact in snapshot.facts]
    next(fact for fact in updates if fact["key"] == "pack_quantity")["value"] = "4"
    queued = await feedback(
        database, order_id, old, "每包数量从6改成4，线径仍是6 mm。", fact_updates=updates
    )
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    old, new = await candidates(database, order_id)
    assert new.draft["title"].startswith("4-Pack") and "6 mm" in new.draft["bullets"][0]
    assert new.parent_version_id == old.id and old.draft["title"].startswith("6-Pack")
    assert provider.calls.count("ResearchDraft") == 2
    async with database.sessions() as session:
        request = await session.get(FeedbackRequest, queued.feedback_id)
        assert request.classification["category"] == "fact" and request.resulting_revision == 2
        assert (await session.get(SellerConfirmation, old_approval.confirmation_id)).invalidated_at
        with pytest.raises(PublicationError):
            await queue_confirmation(
                session,
                settings,
                order_id,
                ConfirmationInput(
                    expected_revision=2,
                    version_id=old.id,
                    listing_price="12.99",
                    currency="USD",
                    seller_sku="STALE-CLIPS",
                ),
                "old-version-cannot-publish",
            )


async def test_style_after_fact_revision_preserves_acceptance_for_image_resume(bundle_context):
    settings, database, order_id, provider, original = await delivered_base(bundle_context)
    async with database.sessions() as session:
        snapshot = await session.scalar(
            select(InputSnapshot).where(InputSnapshot.order_id == order_id)
        )
        updates = [dict(fact) for fact in snapshot.facts]
    next(fact for fact in updates if fact["key"] == "pack_quantity")["value"] = "4"
    await feedback(
        database, order_id, original, "每包改成4个，其他事实不变。", fact_updates=updates
    )
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    fact_version = (await candidates(database, order_id))[-1]
    await released(settings, database, order_id, fact_version, "fact-before-style")
    await feedback(database, order_id, fact_version, "标题更简洁，商品事实保持不变。", revision=2)
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    async with database.sessions() as session:
        order = await session.get(Order, order_id)
        assert order.revision == 2
        assert await accepted_revision(session, order)


async def test_new_sensitive_claim_cannot_be_classified_as_style_or_generate(bundle_context):
    settings, database, order_id, provider, old = await delivered_base(bundle_context)
    queued = await feedback(database, order_id, old, "标题加上防水、CE认证和通用适配所有线缆。")
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    assert len(await candidates(database, order_id)) == 1
    async with database.sessions() as session:
        request = await session.get(FeedbackRequest, queued.feedback_id)
        order = await session.get(Order, order_id)
        assert request.classification["category"] == "new_claim"
        assert request.status == "waiting_for_seller" and not request.classification["actionable"]
        assert order.phase == "AWAITING_SELLER" and order.pending_command_id is None
    assert provider.calls.count("ListingDraft") == 1


async def test_unknown_publication_reconciles_without_second_submit_or_planning(bundle_context):
    settings, database, order_id, provider, old = await delivered_base(bundle_context)
    queued = await confirm(database, settings, order_id, old)

    class UnknownAfterCommit(SimulatedPublisher):
        submits = 0

        async def submit(self, snapshot, *, operation_key):
            self.submits += 1
            await super().submit(snapshot, operation_key=operation_key)
            raise TimeoutError("Injected unknown after actual local external effect")

    publisher = UnknownAfterCommit(settings, database)
    assert await process_one_job(
        settings, provider=provider, publisher=publisher, order_id=order_id
    )
    async with database.sessions() as session, session.begin():
        attempt = await session.scalar(
            select(PublishAttempt).where(PublishAttempt.order_id == order_id)
        )
        assert (
            attempt.status == "UNKNOWN"
            and (await session.get(Order, order_id)).phase == "PUBLISHING"
        )
        job = await session.scalar(select(Job).where(Job.command_id == queued.command_id))
        assert job.status == "queued"
        job.available_at = now()
    assert await process_one_job(
        settings, provider=provider, publisher=publisher, order_id=order_id
    )
    assert publisher.submits == 1 and provider.calls.count("PublishPlan") == 1
    async with database.sessions() as session:
        assert (await session.get(Order, order_id)).phase == "COMPLETED"


async def test_feedback_candidate_budget_is_persisted_and_cannot_be_reset_by_replay(bundle_context):
    provider = FullProvider(bad_feedback_candidates=True)
    settings, database, order_id, provider, old = await delivered_base(bundle_context, provider)
    queued = await feedback(database, order_id, old, "请优化措辞，保持事实不变。")
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    assert len(await candidates(database, order_id)) == 4
    async with database.sessions() as session, session.begin():
        request = await session.get(FeedbackRequest, queued.feedback_id)
        assert request.candidate_count == request.candidate_limit == 3
        assert (await session.get(Command, queued.command_id)).status == "failed"
        session.add(Job(order_id=order_id, command_id=queued.command_id, kind="RESUME"))
    before = len(provider.calls)
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    assert len(provider.calls) == before


async def test_recorded_order_token_budget_applies_across_new_feedback_requests(bundle_context):
    settings, database, order_id, provider, old = await delivered_base(bundle_context)
    queued = await feedback(database, order_id, old, "请优化措辞。")
    before = len(provider.calls)
    limited = settings.model_copy(update={"order_token_budget": 1})
    assert await process_one_job(limited, provider=provider, order_id=order_id)
    assert len(provider.calls) == before
    async with database.sessions() as session:
        job = await session.scalar(select(Job).where(Job.command_id == queued.command_id))
        assert job.status == "failed" and job.attempts == 1
        assert (await session.get(Order, order_id)).pending_command_id is None


async def test_feedback_classification_receipt_is_reused_after_checkpoint_failure(
    bundle_context, monkeypatch
):
    settings, database, order_id, provider, old = await delivered_base(bundle_context)
    queued = await feedback(database, order_id, old, "请让措辞更简洁。")
    original = AsyncPostgresSaver.aput
    fault = False

    async def fail_after_feedback(self, config, checkpoint, metadata, new_versions):
        nonlocal fault
        state = checkpoint.get("channel_values", {})
        if (
            not fault
            and state.get("command_id") == str(queued.command_id)
            and state.get("phase") == "GENERATION"
            and not state.get("version_id")
        ):
            fault = True
            raise RuntimeError("Injected after saved acquisition feedback classification")
        return await original(self, config, checkpoint, metadata, new_versions)

    monkeypatch.setattr(AsyncPostgresSaver, "aput", fail_after_feedback)
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    assert fault
    async with database.sessions() as session, session.begin():
        (
            await session.scalar(select(Job).where(Job.command_id == queued.command_id))
        ).available_at = now()
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    assert provider.calls.count("FeedbackDraft") == 1
    assert len(await candidates(database, order_id)) == 2


async def test_optional_attribute_omission_does_not_block_revision_or_revalidate_old_delivery(
    bundle_context,
):
    settings, database, order_id, provider, old = await delivered_base(bundle_context)
    async with database.sessions() as session:
        base = await session.get(InputSnapshot, old.input_snapshot_id)
        updates = [dict(fact) for fact in base.facts if fact["key"] != "use_cases"]
    next(fact for fact in updates if fact["key"] == "pack_quantity")["value"] = "4"
    queued = await feedback(
        database, order_id, old, "数量改为4，未提供的适用范围不写入文案。", fact_updates=updates
    )
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    previous, new = await candidates(database, order_id)
    assert new.draft["title"].startswith("4-Pack") and new.parent_version_id == previous.id
    async with database.sessions() as session:
        request = await session.get(FeedbackRequest, queued.feedback_id)
        assert request.classification["actionable"] and request.resulting_revision == 2
    with pytest.raises(PublicationError):
        await confirm(database, settings, order_id, old, revision=2)


async def test_open_human_ticket_pauses_intake_before_any_model_call(bundle_context):
    settings, database, _, create = bundle_context
    order_id = await create()
    async with database.sessions() as session, session.begin():
        session.add(SupportTicket(order_id=order_id, requested_phase="INTAKE"))
    provider = FullProvider()
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    assert provider.calls == []
    async with database.sessions() as session:
        assert (await session.get(Order, order_id)).phase == "INTAKE"
        command = await session.scalar(select(Command).where(Command.order_id == order_id))
        assert command.status == "applied"
        assert await session.scalar(select(AgentRun).where(AgentRun.order_id == order_id)) is None


async def test_inflight_intake_result_keeps_real_usage_but_suppresses_reply_after_handoff(
    bundle_context,
):
    settings, database, _, create = bundle_context
    order_id = await create()

    class HandoffProvider(FullProvider):
        async def generate_json(self, schema, messages, **kwargs):
            if schema.__name__ == "_IntakeDraft":
                async with database.sessions() as session, session.begin():
                    session.add(SupportTicket(order_id=order_id, requested_phase="INTAKE"))
            return await super().generate_json(schema, messages, **kwargs)

    provider = HandoffProvider()
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    async with database.sessions() as session:
        run = await session.scalar(select(AgentRun).where(AgentRun.order_id == order_id))
        assert run.status == "suppressed" and run.usage["total_tokens"] == 15
        assert (await session.get(Order, order_id)).phase == "INTAKE"
        messages = list(
            await session.scalars(
                select(Message).where(
                    Message.order_id == order_id,
                    Message.role == "assistant",
                )
            )
        )
        assert messages == []


async def test_feedback_image_replacements_share_original_request_candidate_budget(bundle_context):
    class RejectedImagesProvider(FullProvider):
        reject_images = False

        async def generate_json(self, schema, messages, **kwargs):
            if (
                self.reject_images
                and schema.__name__ == "ReviewDraft"
                and any(
                    isinstance(message.get("content"), list)
                    and any(part.get("type") == "image_url" for part in message["content"])
                    for message in messages
                )
            ):
                self.calls.append(schema.__name__)
                return schema.model_validate(
                    {
                        "passed": False,
                        "issues": [
                            {
                                "field_path": "images[0]",
                                "severity": "blocking",
                                "rule_id": "subject_quantity_mismatch",
                                "reason": "图片商品主体与确认参数不符。",
                                "fact_ids": ["fact:pack_quantity"],
                                "suggested_fix": "更换正确参考图。",
                            }
                        ],
                        "buyer_question_coverage": [],
                    }
                ), SimpleNamespace(
                    usage={"total_tokens": 12},
                    model="offline-image-rejection",
                    elapsed_seconds=0.01,
                )
            return await super().generate_json(schema, messages, **kwargs)

    provider = RejectedImagesProvider()
    settings, database, order_id, provider, old = await delivered_base(bundle_context, provider)
    provider.reject_images = True
    queued = await feedback(database, order_id, old, "请优化文字风格。")
    assert await process_one_job(settings, provider=provider, order_id=order_id)
    _, _, client, _ = bundle_context
    seller, headers = await client(order_id)
    for color in ["#123456", "#654321", "#aabbcc"]:
        await upload(seller, headers, order_id, color=color)
        assert await process_one_job(settings, provider=provider, order_id=order_id)
    async with database.sessions() as session:
        request = await session.get(FeedbackRequest, queued.feedback_id)
        assert request.candidate_count == request.candidate_limit == 3
        assert (await session.get(Order, order_id)).pending_command_id is None
    assert (
        len(await candidates(database, order_id)) == 4
    )  # Original plus three feedback candidates.
    assert (
        provider.calls.count("ListingDraft") == 2
    )  # Image correction never restarts copy generation.
