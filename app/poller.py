import asyncio
import signal
from datetime import UTC, datetime

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session_factory, init_db
from app.detectors import evaluate_national_contrast, evaluate_new_reading
from app.logging_config import setup_logging
from app.models import Reading

logger = setup_logging("poller")

CITIES = {
    "Ottawa": (45.42, -75.69),
    "Toronto": (43.70, -79.42),
    "Vancouver": (49.25, -123.12),
}

CURRENT_PARAMS = (
    "temperature_2m,apparent_temperature,precipitation,wind_speed_10m,weather_code"
)
BACKOFF_DELAYS = (2, 4, 8, 16, 32)
_shutdown = False


def _parse_timestamp(value: str) -> datetime:
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if ts.tzinfo is None:
        return ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


def _handle_shutdown(*_args: object) -> None:
    global _shutdown
    _shutdown = True
    logger.info("Shutdown signal received", extra={"service": "poller", "status": "shutting_down"})


async def fetch_current(
    client: httpx.AsyncClient,
    city: str,
    lat: float,
    lon: float,
) -> dict | None:
    settings = get_settings()
    url = f"{settings.open_meteo_base_url}/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": CURRENT_PARAMS,
        "wind_speed_unit": "kmh",
        "timezone": "UTC",
    }

    for attempt, delay in enumerate(BACKOFF_DELAYS, start=1):
        try:
            response = await client.get(url, params=params, timeout=30.0)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, httpx.TimeoutException) as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            extra = {
                "service": "poller",
                "city": city,
                "status": "retry",
                "retry_count": attempt,
                "error": str(exc),
            }
            if status is not None:
                extra["http_status"] = status
            if attempt >= 2:
                logger.warning("Weather API fetch failed", extra=extra)
            if attempt == len(BACKOFF_DELAYS):
                logger.error("Weather API fetch exhausted retries", extra=extra)
                return None
            await asyncio.sleep(delay)
    return None


async def store_reading(session: AsyncSession, city: str, payload: dict) -> Reading | None:
    current = payload.get("current")
    if not current:
        logger.warning(
            "Missing current block in API response",
            extra={"service": "poller", "city": city, "status": "skipped"},
        )
        return None

    reading = Reading(
        city=city,
        timestamp=_parse_timestamp(current["time"]),
        temperature=float(current["temperature_2m"]),
        apparent_temperature=float(current["apparent_temperature"]),
        precipitation=float(current["precipitation"]),
        wind_speed=float(current["wind_speed_10m"]),
        weather_code=int(current["weather_code"]),
        fetched_at=datetime.now(UTC),
    )

    session.add(reading)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        logger.info(
            "Duplicate reading skipped",
            extra={"service": "poller", "city": city, "status": "duplicate"},
        )
        return None

    events = await evaluate_new_reading(reading, session)
    for event in events:
        session.add(event)

    await session.commit()
    await session.refresh(reading)
    logger.info(
        "Stored new reading",
        extra={
            "service": "poller",
            "city": city,
            "status": "stored",
            "events_created": len(events),
        },
    )
    return reading


async def poll_cycle(client: httpx.AsyncClient) -> None:
    stored_any = False
    for city, (lat, lon) in CITIES.items():
        if _shutdown:
            break
        payload = await fetch_current(client, city, lat, lon)
        if payload is None:
            continue
        async with async_session_factory() as session:
            reading = await store_reading(session, city, payload)
            if reading is not None:
                stored_any = True

    if stored_any and not _shutdown:
        async with async_session_factory() as session:
            event = await evaluate_national_contrast(session)
            if event is not None:
                session.add(event)
                await session.commit()
                logger.info(
                    "National contrast event created",
                    extra={"service": "poller", "status": "event_created"},
                )


async def run_poller() -> None:
    settings = get_settings()
    await init_db()
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    logger.info(
        "Poller started",
        extra={
            "service": "poller",
            "status": "started",
            "poll_interval_seconds": settings.poll_interval_seconds,
        },
    )

    async with httpx.AsyncClient() as client:
        while not _shutdown:
            await poll_cycle(client)
            if _shutdown:
                break
            await asyncio.sleep(settings.poll_interval_seconds)

    logger.info("Poller stopped", extra={"service": "poller", "status": "stopped"})


def main() -> None:
    asyncio.run(run_poller())


if __name__ == "__main__":
    main()
