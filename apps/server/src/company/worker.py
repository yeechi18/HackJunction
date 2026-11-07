"""Run queued commands through the persistent LangGraph workflow."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

from company.config import Settings, get_settings
from company.persistence.database import Database
from company.runtime import configure_asyncio
from company.services.workflow import WorkflowError, process_one_job


async def run_worker(
    settings: Settings,
    *,
    once: bool = False,
    drain: bool = False,
    order_id: UUID | None = None,
    intake_only: bool = False,
    text_only: bool = False,
) -> None:
    database = Database(settings)
    try:
        while True:
            processed = await process_one_job(
                settings,
                order_id=order_id,
                include_production=not intake_only,
                include_images=not text_only,
            )
            check = await database.readiness()
            print(
                json.dumps(
                    {
                        "event": "worker_heartbeat",
                        "timestamp": datetime.now(UTC).isoformat(),
                        "milestone": "M7",
                        "mode": "agent_workflow",
                        "database": check.database,
                        "schema": check.schema,
                        "job_processed": processed,
                    }
                ),
                flush=True,
            )
            if once or (drain and not processed):
                return
            await asyncio.sleep(0.1 if processed else min(2, settings.worker_heartbeat_seconds))
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Process durable workflow jobs.")
    parser.add_argument("--once", action="store_true", help="Process at most one job and exit.")
    parser.add_argument("--drain", action="store_true", help="Run available jobs until idle.")
    parser.add_argument(
        "--text-only", action="store_true", help="Stop before images for M4 validation."
    )
    parser.add_argument(
        "--intake-only", action="store_true", help="Stop after acceptance for M3 validation."
    )
    parser.add_argument(
        "--order-id", type=UUID, help="Only process this order, useful for validation."
    )
    arguments = parser.parse_args()
    configure_asyncio()
    try:
        asyncio.run(
            run_worker(
                get_settings(),
                once=arguments.once,
                drain=arguments.drain,
                order_id=arguments.order_id,
                intake_only=arguments.intake_only,
                text_only=arguments.text_only,
            )
        )
    except KeyboardInterrupt:
        pass
    except WorkflowError as error:
        print(str(error))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
