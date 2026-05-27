import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import dispose_db, get_session, init_db
from app.detectors import CITIES
from app.logging_config import setup_logging
from app.models import Event, Reading

logger = setup_logging("api")


class HealthResponse(BaseModel):
    status: str
    readings_stored: int
    events_stored: int
    last_poll: datetime | None = None
    db_size_bytes: int = 0


class ReadingOut(BaseModel):
    id: int
    city: str
    timestamp: datetime
    temperature: float
    apparent_temperature: float
    precipitation: float
    wind_speed: float
    weather_code: int
    fetched_at: datetime

    model_config = {"from_attributes": True}


class EventOut(BaseModel):
    id: int
    type: str
    city: str | None
    timestamp: datetime
    description: str
    severity: float
    reading_ids: list[int] | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReadingsResponse(BaseModel):
    readings: list[ReadingOut]


class EventsResponse(BaseModel):
    events: list[EventOut]


class CityStats(BaseModel):
    readings_stored: int
    events_stored: int
    latest_temperature: float | None
    latest_apparent_temperature: float | None
    latest_wind_speed: float | None
    latest_weather_code: int | None
    most_frequent_event_type: str | None


class StatsResponse(BaseModel):
    cities: dict[str, CityStats]
    totals: dict[str, int]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_db()
    yield
    await dispose_db()


app = FastAPI(title="WatchAgent Weather Monitor", lifespan=lifespan)

_DASHBOARD = Path(__file__).parent / "static" / "index.html"


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard() -> str:
    return _DASHBOARD.read_text(encoding="utf-8")


@app.middleware("http")
async def log_requests(request: Request, call_next):
    request_id = uuid.uuid4().hex[:8]
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    logger.info(
        "HTTP request",
        extra={
            "service": "api",
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": duration_ms,
        },
    )
    response.headers["X-Request-ID"] = request_id
    return response


@app.get("/health", response_model=HealthResponse)
async def health(session: AsyncSession = Depends(get_session)) -> HealthResponse:
    readings_count = await session.scalar(select(func.count()).select_from(Reading))
    events_count = await session.scalar(select(func.count()).select_from(Event))
    last_poll = await session.scalar(select(func.max(Reading.fetched_at)))

    settings = get_settings()
    db_path = settings.database_url.replace("sqlite+aiosqlite:///", "")
    db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0

    return HealthResponse(
        status="ok",
        readings_stored=readings_count or 0,
        events_stored=events_count or 0,
        last_poll=last_poll,
        db_size_bytes=db_size,
    )


@app.get("/readings", response_model=ReadingsResponse)
async def list_readings(
    city: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> ReadingsResponse:
    stmt = select(Reading).order_by(desc(Reading.timestamp)).limit(limit).offset(offset)
    if city:
        stmt = stmt.where(Reading.city == city)
    if since:
        stmt = stmt.where(Reading.timestamp >= since)
    if until:
        stmt = stmt.where(Reading.timestamp <= until)
    rows = (await session.execute(stmt)).scalars().all()
    return ReadingsResponse(readings=[ReadingOut.model_validate(r) for r in rows])


@app.get("/events", response_model=EventsResponse)
async def list_events(
    city: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> EventsResponse:
    stmt = select(Event).order_by(desc(Event.timestamp)).limit(limit).offset(offset)
    if city:
        stmt = stmt.where(Event.city == city)
    if since:
        stmt = stmt.where(Event.timestamp >= since)
    if until:
        stmt = stmt.where(Event.timestamp <= until)
    rows = (await session.execute(stmt)).scalars().all()
    return EventsResponse(events=[EventOut.model_validate(e) for e in rows])


@app.get("/stats", response_model=StatsResponse)
async def stats(session: AsyncSession = Depends(get_session)) -> StatsResponse:
    city_stats: dict[str, CityStats] = {}
    total_readings = 0
    total_events = 0

    for city in CITIES:
        readings_count = (
            await session.scalar(
                select(func.count()).select_from(Reading).where(Reading.city == city)
            )
        ) or 0
        events_count = (
            await session.scalar(
                select(func.count()).select_from(Event).where(Event.city == city)
            )
        ) or 0

        latest = (
            await session.execute(
                select(Reading)
                .where(Reading.city == city)
                .order_by(desc(Reading.timestamp))
                .limit(1)
            )
        ).scalar_one_or_none()

        top_event_row = (
            await session.execute(
                select(Event.type, func.count(Event.type).label("cnt"))
                .where(Event.city == city)
                .group_by(Event.type)
                .order_by(desc("cnt"))
                .limit(1)
            )
        ).one_or_none()

        total_readings += readings_count
        total_events += events_count
        city_stats[city] = CityStats(
            readings_stored=readings_count,
            events_stored=events_count,
            latest_temperature=latest.temperature if latest else None,
            latest_apparent_temperature=latest.apparent_temperature if latest else None,
            latest_wind_speed=latest.wind_speed if latest else None,
            latest_weather_code=latest.weather_code if latest else None,
            most_frequent_event_type=top_event_row[0] if top_event_row else None,
        )

    return StatsResponse(
        cities=city_stats,
        totals={"readings_stored": total_readings, "events_stored": total_events},
    )
