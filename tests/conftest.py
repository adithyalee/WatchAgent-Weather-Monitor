"""Shared pytest fixtures for WatchAgent tests."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import get_session
from app.main import app
from app.models import Base, Event, Reading


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def api_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_session() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


def make_reading(
    *,
    city: str = "Ottawa",
    timestamp: datetime | None = None,
    temperature: float = 20.0,
    apparent_temperature: float = 20.0,
    precipitation: float = 0.0,
    wind_speed: float = 10.0,
    weather_code: int = 0,
) -> Reading:
    return Reading(
        city=city,
        timestamp=timestamp or datetime.now(UTC),
        temperature=temperature,
        apparent_temperature=apparent_temperature,
        precipitation=precipitation,
        wind_speed=wind_speed,
        weather_code=weather_code,
        fetched_at=datetime.now(UTC),
    )


async def add_reading(session: AsyncSession, **kwargs: object) -> Reading:
    reading = make_reading(**kwargs)  # type: ignore[arg-type]
    session.add(reading)
    await session.commit()
    await session.refresh(reading)
    return reading


async def add_event(session: AsyncSession, **kwargs: object) -> Event:
    event = Event(
        type=kwargs.get("type", "RAPID_CHANGE"),  # type: ignore[arg-type]
        city=kwargs.get("city", "Ottawa"),  # type: ignore[arg-type]
        timestamp=kwargs.get("timestamp", datetime.now(UTC)),  # type: ignore[arg-type]
        description=kwargs.get("description", "test event"),  # type: ignore[arg-type]
        severity=kwargs.get("severity", 0.5),  # type: ignore[arg-type]
        reading_ids=kwargs.get("reading_ids"),  # type: ignore[arg-type]
        created_at=datetime.now(UTC),
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event
