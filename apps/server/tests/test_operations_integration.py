"""New owner dashboard reads our draft receipts without exposing internal payloads."""

import pytest
from sqlalchemy import select
from test_assets import asset_context as _asset_context

from company.persistence.agent_runs import AgentRun
from company.persistence.models import Command, InputSnapshot, now

asset_context = _asset_context


@pytest.mark.integration
async def test_operations_reads_copy_preview_with_owner_only_filtered_trace(asset_context):
    _, app, scoped_client, new_order = asset_context
    order_id = await new_order()
    seller, _ = await scoped_client(order_id)
    owner, _ = await scoped_client(owner=True)
    async with app.state.database.sessions() as session, session.begin():
        command = await session.scalar(select(Command).where(Command.order_id == order_id))
        snapshot = await session.scalar(
            select(InputSnapshot).where(InputSnapshot.order_id == order_id)
        )
        receipt = AgentRun(
            order_id=order_id,
            command_id=command.id,
            input_snapshot_id=snapshot.id,
            input_revision=1,
            role="copy_preview",
            status="needs_review",
            model="offline-integration-fixture",
            usage={"total_tokens": 123},
            result={
                "draft": None,
                "missing_fields": ["net_volume"],
                "issues": [{"reason": "An unsupported claim was blocked."}],
                "rejected_draft": {"title": "Do not show unapproved raw draft"},
                "private_diagnostics": "Do not export raw diagnostics",
            },
            finished_at=now(),
        )
        session.add(receipt)
        await session.flush()
        receipt_id = str(receipt.id)
    path = "/api/v1/internal/orders/agent-runs"
    assert (await seller.get(path)).status_code == 401
    basic = await owner.get(path, params={"limit": 500})
    assert basic.status_code == 200
    plain = next(row for row in basic.json() if row["id"] == receipt_id)
    assert plain["result"] is None and plain["usage"]["total_tokens"] == 123
    detailed = await owner.get(path, params={"include_trace": True, "limit": 500})
    assert detailed.status_code == 200
    trace = next(row for row in detailed.json() if row["id"] == receipt_id)
    assert trace["role"] == "copy_preview" and trace["status"] == "needs_review"
    assert set(trace["result"]) == {"draft", "missing_fields", "issues"}
    assert "private_diagnostics" not in trace["result"]
    assert (await owner.get(path, params={"limit": 501})).status_code == 422
