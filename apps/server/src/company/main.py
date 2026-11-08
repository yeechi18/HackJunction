"""Order access and command APIs; the separate worker drives the durable intake graph."""

from collections import deque
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from company import __version__
from company.api.access import router as access_router
from company.api.accounts import router as accounts_router
from company.api.assets import router as assets_router
from company.api.auth import router as auth_router
from company.api.copy_preview import router as copy_preview_router
from company.api.deliverables import router as deliverables_router
from company.api.health import router as health_router
from company.api.image_tasks import router as image_tasks_router
from company.api.integrations import router as integrations_router
from company.api.internal import router as internal_router
from company.api.orders import router as orders_router
from company.api.production import router as production_router
from company.api.publication import router as publication_router
from company.api.support import router as support_router
from company.config import Settings, get_settings
from company.persistence.database import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    configuration = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = Database(configuration)
        app.state.database = database
        try:
            yield
        finally:
            await database.close()

    application = FastAPI(
        title="AI Ecommerce Company API",
        version=__version__,
        description="Durable order intake and scoped access. The worker runs MiMo workflows.",
        lifespan=lifespan,
    )
    application.state.settings = configuration
    application.state.intake_chat_attempts = deque()

    @application.exception_handler(RequestValidationError)
    async def safe_validation_error(request, error):
        # Validation errors must not echo submitted passwords, link tokens or other input values.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": item["loc"], "msg": item["msg"], "type": item["type"]}
                    for item in error.errors()
                ]
            },
        )

    application.include_router(health_router, prefix="/api/v1")
    for router in (
        access_router,
        accounts_router,
        auth_router,
        internal_router,
        orders_router,
        production_router,
        assets_router,
        deliverables_router,
        publication_router,
        support_router,
        copy_preview_router,
        integrations_router,
        image_tasks_router,
    ):
        application.include_router(router, prefix="/api/v1")
    return application


app = create_app()
