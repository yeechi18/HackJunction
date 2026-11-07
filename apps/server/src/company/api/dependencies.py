from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from company.config import Settings
from company.persistence.models import AuthSession
from company.services.access import OWNER_COOKIE, check_csrf, get_session


async def database_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.database.sessions() as session:
        yield session


def configuration(request: Request) -> Settings:
    return request.app.state.settings


DB = Annotated[AsyncSession, Depends(database_session)]
Configuration = Annotated[Settings, Depends(configuration)]


async def owner_session(request: Request, database: DB) -> AuthSession:
    return await get_session(database, request.cookies.get(OWNER_COOKIE), "owner")


Owner = Annotated[AuthSession, Depends(owner_session)]


def owner_write(request: Request, settings: Configuration, owner: Owner) -> None:
    check_csrf(request, settings, request.cookies.get(OWNER_COOKIE, ""))
