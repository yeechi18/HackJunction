"""Issue handling must remain authorized, revision-bound and idempotent."""

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from test_delivery import delivery_context as _delivery_context

from company.persistence.models import Command, Message
from company.services.access import csrf_token

pytestmark = pytest.mark.integration
delivery_context = _delivery_context


async def test_advice_access_revision_and_idempotency(delivery_context):
    settings, app, candidate, client = delivery_context
    item = await candidate()
    owner = await client(owner=True)
    seller = await client(item.order_id)
    path = f"/api/v1/internal/orders/{item.order_id}/issue-actions"
    body = dict(
        action="seller_advice",
        expected_revision=1,
        request_id=str(uuid4()),
        content="Please confirm the dimensions.",
    )
    headers = {
        "origin": settings.public_app_url,
        "x-csrf-token": csrf_token(settings, owner.cookies.get("owner_session")),
    }
    assert (await seller.post(path, json=body)).status_code in {401, 403}
    assert (await owner.post(path, json=body)).status_code == 403
    assert (
        await owner.post(path, json={**body, "expected_revision": 99}, headers=headers)
    ).status_code == 409
    assert (await owner.post(path, json=body, headers=headers)).status_code == 200
    assert (await owner.post(path, json=body, headers=headers)).status_code == 200
    assert (
        await owner.post(path, json={**body, "content": "Changed"}, headers=headers)
    ).status_code == 409
    async with app.state.database.sessions() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.order_id == item.order_id, Message.content == body["content"])
        )
        assert count == 1


async def test_ignore_preserves_failure_and_rejects_wrong_order(delivery_context):
    settings, app, candidate, client = delivery_context
    item, other = await candidate(), await candidate()
    owner = await client(owner=True)
    headers = {
        "origin": settings.public_app_url,
        "x-csrf-token": csrf_token(settings, owner.cookies.get("owner_session")),
    }
    async with app.state.database.sessions() as session, session.begin():
        command = await session.scalar(
            select(Command).where(Command.order_id == item.order_id, Command.kind == "START")
        )
        command.status = "failed"
        command_id = command.id
    body = dict(
        action="ignore",
        expected_revision=1,
        request_id=str(uuid4()),
        command_id=str(command_id),
        content="Acknowledged historical failure",
    )
    assert (
        await owner.post(
            f"/api/v1/internal/orders/{other.order_id}/issue-actions", json=body, headers=headers
        )
    ).status_code == 409
    assert (
        await owner.post(
            f"/api/v1/internal/orders/{item.order_id}/issue-actions", json=body, headers=headers
        )
    ).status_code == 200
    async with app.state.database.sessions() as session:
        assert (await session.get(Command, command_id)).status == "failed"
