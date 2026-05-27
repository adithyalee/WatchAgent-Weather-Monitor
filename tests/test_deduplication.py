"""Tests that duplicate Open-Meteo readings are stored only once.

Covers two layers:
1. Database constraint — IntegrityError on (city, timestamp) duplicate.
2. Poller logic — store_reading() called twice with the same mocked API
   payload stores only one row, mirroring the real scenario where Open-Meteo
   returns the same hourly reading across consecutive poll cycles.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Reading
from app.poller import fetch_current, store_reading
from tests.conftest import make_reading


@pytest.mark.asyncio
async def test_duplicate_city_timestamp_rejected(db_session: AsyncSession) -> None:
    """Database unique constraint on (city, timestamp) rejects the duplicate row."""
    ts = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    first = make_reading(city="Ottawa", timestamp=ts)
    duplicate = make_reading(city="Ottawa", timestamp=ts, temperature=99.0)

    db_session.add(first)
    await db_session.commit()

    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        await db_session.commit()

    await db_session.rollback()
    count = await db_session.scalar(select(func.count()).select_from(Reading))
    stored = await db_session.scalar(select(Reading.temperature).limit(1))
    assert count == 1
    assert stored == 20.0


@pytest.mark.asyncio
async def test_poller_skips_duplicate_api_reading(db_session: AsyncSession) -> None:
    """Poller deduplication: same API payload passed to store_reading() twice → one row.

    Open-Meteo updates readings once per hour. When the poller runs more
    frequently it receives the same (city, timestamp) pair across consecutive
    poll cycles. store_reading() must catch the IntegrityError and return None
    on the second attempt without raising.
    """
    payload = {
        "current": {
            "time": "2026-05-26T14:00",
            "temperature_2m": 18.5,
            "apparent_temperature": 16.0,
            "precipitation": 0.0,
            "wind_speed_10m": 12.0,
            "weather_code": 1,
        }
    }

    first = await store_reading(db_session, "Ottawa", payload)
    assert first is not None, "First store must succeed"

    second = await store_reading(db_session, "Ottawa", payload)
    assert second is None, "Duplicate store must return None, not raise"

    count = await db_session.scalar(select(func.count()).select_from(Reading))
    assert count == 1


@pytest.mark.asyncio
async def test_mock_api_same_reading_twice_deduplicates(db_session: AsyncSession) -> None:
    """Mock the Open-Meteo HTTP client to return the same payload twice.

    Simulates two consecutive poll cycles where the API has not yet updated.
    fetch_current() returns an identical dict both times; store_reading() must
    store exactly one row.
    """
    same_payload = {
        "current": {
            "time": "2026-05-26T15:00",
            "temperature_2m": 15.0,
            "apparent_temperature": 13.0,
            "precipitation": 0.2,
            "wind_speed_10m": 18.0,
            "weather_code": 61,
        }
    }

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = same_payload
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock(spec=httpx.AsyncClient)
    mock_client.get.return_value = mock_response

    # Poll cycle 1
    payload1 = await fetch_current(mock_client, "Vancouver", 49.25, -123.12)
    r1 = await store_reading(db_session, "Vancouver", payload1)

    # Poll cycle 2 — API still returns same timestamp (not yet updated)
    payload2 = await fetch_current(mock_client, "Vancouver", 49.25, -123.12)
    r2 = await store_reading(db_session, "Vancouver", payload2)

    assert r1 is not None
    assert r2 is None
    assert mock_client.get.call_count == 2

    count = await db_session.scalar(select(func.count()).select_from(Reading))
    assert count == 1
