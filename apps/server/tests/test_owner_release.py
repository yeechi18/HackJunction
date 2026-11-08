"""Owner overrides retain review findings and reject stale acknowledgements."""

from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from test_delivery import delivery_context as _delivery_context

from company.persistence.deliveries import DeliverySnapshot
from company.persistence.models import Command, Order, OrderEvent
from company.persistence.production import DeliverableVersion, ReviewReport
from company.schemas.commands import DecisionInput
from company.services.delivery import release_version
from company.services.orders import enqueue_decision
from company.services.owner_release import release_review

pytestmark = pytest.mark.integration
delivery_context = _delivery_context


async def failed_candidate(app, candidate):
    item = await candidate()
    async with app.state.database.sessions() as session, session.begin():
        order = await session.get(Order, item.order_id)
        order.pending_command_id = None
        version = await session.get(DeliverableVersion, item.version_id)
        version.full_passed = False
        version.status = "review_failed"
        report = await session.get(ReviewReport, item.review_id)
        report.full_passed = False
        report.result = {
            **report.result,
            "passed": False,
            "issues": [
                {
                    "field_path": "title",
                    "severity": "blocking",
                    "reason": "Title claim needs checking",
                    "suggested_fix": "Check against product facts",
                }
            ],
        }
    return item


async def submit_override(app, item, token):
    async with app.state.database.sessions() as session:
        return await enqueue_decision(
            session,
            item.order_id,
            DecisionInput(
                action="release",
                version_id=item.version_id,
                expected_revision=1,
                override_review=True,
                review_token=token,
                reason="Owner checked the product",
            ),
            str(uuid4()),
        )


@pytest.mark.parametrize("phase", ["AWAITING_RELEASE", "REVIEW", "NEEDS_IMAGE"])
async def test_owner_can_release_failed_review_without_faking_ai_result(delivery_context, phase):
    settings, app, candidate, client = delivery_context
    item = await failed_candidate(app, candidate)
    async with app.state.database.sessions() as session, session.begin():
        order = await session.get(Order, item.order_id)
        order.phase = phase
    owner = await client(owner=True)
    seller = await client(item.order_id)
    path = f"/api/v1/internal/orders/{item.order_id}/release-review?version_id={item.version_id}"
    assert (await seller.get(path)).status_code in {401, 403}
    response = await owner.get(path)
    assert response.status_code == 200
    preview = response.json()
    assert not preview["blockers"]
    assert preview["issues"][0]["message"] == "Title claim needs checking"
    async with app.state.database.sessions() as session:
        with pytest.raises(HTTPException):
            await enqueue_decision(
                session,
                item.order_id,
                DecisionInput(
                    action="release",
                    version_id=item.version_id,
                    expected_revision=1,
                ),
                str(uuid4()),
            )
    command = await submit_override(app, item, preview["token"])
    output = await release_version(
        app.state.database, settings, item.order_id, item.version_id, command.command_id
    )
    repeated = await release_version(
        app.state.database, settings, item.order_id, item.version_id, command.command_id
    )
    assert output.id == repeated.id
    async with app.state.database.sessions() as session:
        report = await session.get(ReviewReport, item.review_id)
        assert not report.full_passed and report.result["passed"] is False
        saved = await session.get(Command, command.command_id)
        assert saved.payload["owner_acknowledgement"]["issues"] == preview["issues"]
        assert saved.status == "applied"
        event = await session.scalar(
            select(OrderEvent).where(
                OrderEvent.order_id == item.order_id, OrderEvent.kind == "version_delivered"
            )
        )
        assert event.payload["approval_method"] == "owner_override"
        from company.services.publication import PublicationError, current_delivery

        order = await session.get(Order, item.order_id)
        confirmed = await current_delivery(session, order, item.version_id)
        assert confirmed.version_id == item.version_id
        saved.payload = {**saved.payload, "override_review": False}
        await session.flush()
        with pytest.raises(PublicationError):
            await current_delivery(session, order, item.version_id)



@pytest.mark.parametrize("change", ["review", "draft", "images"])
async def test_owner_confirmation_expires_when_content_changes(delivery_context, change):
    settings, app, candidate, _ = delivery_context
    item = await failed_candidate(app, candidate)
    async with app.state.database.sessions() as session:
        preview = await release_review(session, item.order_id, item.version_id)
    command = await submit_override(app, item, preview["token"])
    async with app.state.database.sessions() as session, session.begin():
        version = await session.get(DeliverableVersion, item.version_id)
        if change == "review":
            report = await session.get(ReviewReport, item.review_id)
            result = deepcopy(report.result)
            result["issues"][0]["reason"] = "A newly discovered problem"
            report.result = result
        elif change == "draft":
            version.draft = {**version.draft, "title": "A different title"}
        else:
            version.assets = []
    with pytest.raises(HTTPException) as caught:
        await release_version(
            app.state.database, settings, item.order_id, item.version_id, command.command_id
        )
    assert caught.value.status_code == 409
    async with app.state.database.sessions() as session:
        assert (
            await session.scalar(
                select(DeliverySnapshot).where(DeliverySnapshot.order_id == item.order_id)
            )
            is None
        )


async def test_missing_images_are_not_replaced_by_owner_approval(delivery_context):
    _, app, candidate, _ = delivery_context
    item = await failed_candidate(app, candidate)
    async with app.state.database.sessions() as session, session.begin():
        version = await session.get(DeliverableVersion, item.version_id)
        version.assets = []
    async with app.state.database.sessions() as session:
        preview = await release_review(session, item.order_id, item.version_id)
    assert preview["blockers"]
    with pytest.raises(HTTPException) as caught:
        await submit_override(app, item, preview["token"])
    assert caught.value.status_code == 409


async def test_delivery_supersedes_old_failure_but_not_new_failure(delivery_context):
    from datetime import timedelta

    from company.persistence.models import Job, now
    from company.services.orders import summary

    settings, app, candidate, _ = delivery_context
    item = await candidate()
    await release_version(
        app.state.database, settings, item.order_id, item.version_id, item.command_id
    )
    async with app.state.database.sessions() as session, session.begin():
        jobs = list(await session.scalars(select(Job).where(Job.order_id == item.order_id)))
        for job in jobs:
            job.status = "completed"
        old_command = await session.scalar(
            select(Command).where(Command.order_id == item.order_id, Command.kind == "START")
        )
        old_command.status = "failed"
        failed = Job(
            order_id=item.order_id,
            command_id=old_command.id,
            kind="RESUME",
            status="failed",
            created_at=now() - timedelta(hours=1),
        )
        session.add(failed)
        await session.flush()
        order = await session.get(Order, item.order_id)
        view = await summary(session, order)
        assert view.workflow_status == "waiting_for_seller"
        assert view.workflow_error is None
        failed.created_at = now() + timedelta(seconds=1)
        await session.flush()
        view = await summary(session, order)
        assert view.workflow_status == "failed"
