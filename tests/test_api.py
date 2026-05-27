"""Tests for HTTP API response shape and query behaviour."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.event_types import EventType
from tests.conftest import add_event, add_reading


@pytest.mark.asyncio
async def test_health_endpoint(api_client: AsyncClient, db_session: AsyncSession) -> None:
    await add_reading(db_session, city="Ottawa")
    await add_reading(db_session, city="Toronto")
    await add_event(
        db_session,
        type=EventType.RAPID_CHANGE.value,
        city="Ottawa",
        description="test",
    )

    response = await api_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["readings_stored"] == 2
    assert body["events_stored"] == 1
    assert "last_poll" in body
    assert "db_size_bytes" in body


@pytest.mark.asyncio
async def test_readings_endpoint(api_client: AsyncClient, db_session: AsyncSession) -> None:
    ts_old = datetime(2026, 5, 25, 12, 0, tzinfo=UTC)
    ts_new = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    await add_reading(db_session, city="Ottawa", timestamp=ts_old)
    await add_reading(db_session, city="Toronto", timestamp=ts_new)

    response = await api_client.get("/readings", params={"city": "Toronto", "limit": 10})
    assert response.status_code == 200
    payload = response.json()
    assert "readings" in payload
    assert len(payload["readings"]) == 1
    reading = payload["readings"][0]
    assert set(reading.keys()) == {
        "id", "city", "timestamp", "temperature", "apparent_temperature",
        "precipitation", "wind_speed", "weather_code", "fetched_at",
    }
    assert reading["city"] == "Toronto"


@pytest.mark.asyncio
async def test_events_endpoint(api_client: AsyncClient, db_session: AsyncSession) -> None:
    await add_event(
        db_session,
        type=EventType.STORM_CONDITIONS.value,
        city="Vancouver",
        description="Storm in Vancouver",
        timestamp=datetime(2026, 5, 26, 15, 0, tzinfo=UTC),
    )

    response = await api_client.get("/events", params={"city": "Vancouver"})
    assert response.status_code == 200
    payload = response.json()
    assert "events" in payload
    assert len(payload["events"]) == 1
    event = payload["events"][0]
    assert set(event.keys()) == {
        "id", "type", "city", "timestamp", "description",
        "severity", "reading_ids", "created_at",
    }
    assert event["type"] == EventType.STORM_CONDITIONS.value


@pytest.mark.asyncio
async def test_events_endpoint_includes_severity(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    await add_event(
        db_session,
        type=EventType.TEMPERATURE_ANOMALY.value,
        city="Ottawa",
        description="Anomaly",
        severity=0.75,
    )

    response = await api_client.get("/events", params={"city": "Ottawa"})
    assert response.status_code == 200
    event = response.json()["events"][0]
    assert event["severity"] == 0.75


@pytest.mark.asyncio
async def test_stats_endpoint(api_client: AsyncClient, db_session: AsyncSession) -> None:
    ts = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    await add_reading(db_session, city="Ottawa", timestamp=ts, temperature=22.5)
    await add_reading(db_session, city="Toronto", timestamp=ts)
    await add_reading(db_session, city="Vancouver", timestamp=ts)
    await add_event(db_session, type=EventType.RAPID_CHANGE.value, city="Ottawa")

    response = await api_client.get("/stats")
    assert response.status_code == 200
    payload = response.json()
    assert "cities" in payload
    assert set(payload["cities"].keys()) == {"Ottawa", "Toronto", "Vancouver"}
    assert "totals" in payload
    assert payload["totals"]["readings_stored"] == 3

    ottawa = payload["cities"]["Ottawa"]
    assert ottawa["readings_stored"] == 1
    assert ottawa["events_stored"] == 1
    assert ottawa["latest_temperature"] == 22.5
    assert ottawa["most_frequent_event_type"] == EventType.RAPID_CHANGE.value

    vancouver = payload["cities"]["Vancouver"]
    assert vancouver["readings_stored"] == 1
    assert vancouver["events_stored"] == 0
    assert vancouver["most_frequent_event_type"] is None


@pytest.mark.asyncio
async def test_readings_most_recent_first(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    ts = datetime(2026, 5, 26, 10, 0, tzinfo=UTC)
    await add_reading(db_session, city="Ottawa", timestamp=ts, temperature=10.0)
    await add_reading(db_session, city="Ottawa", timestamp=ts + timedelta(hours=1), temperature=20.0)

    response = await api_client.get("/readings", params={"city": "Ottawa"})
    readings = response.json()["readings"]
    assert len(readings) == 2
    assert readings[0]["temperature"] == 20.0  # newest first
    assert readings[1]["temperature"] == 10.0


@pytest.mark.asyncio
async def test_readings_since_filter(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    base = datetime(2026, 5, 26, 10, 0, tzinfo=UTC)
    await add_reading(db_session, city="Ottawa", timestamp=base, temperature=1.0)
    await add_reading(db_session, city="Ottawa", timestamp=base + timedelta(hours=2), temperature=2.0)
    await add_reading(db_session, city="Ottawa", timestamp=base + timedelta(hours=4), temperature=3.0)

    cutoff = (base + timedelta(hours=1)).isoformat()
    response = await api_client.get("/readings", params={"city": "Ottawa", "since": cutoff})
    temps = [r["temperature"] for r in response.json()["readings"]]
    assert 1.0 not in temps
    assert 2.0 in temps
    assert 3.0 in temps


@pytest.mark.asyncio
async def test_readings_until_filter(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    base = datetime(2026, 5, 26, 10, 0, tzinfo=UTC)
    await add_reading(db_session, city="Ottawa", timestamp=base, temperature=1.0)
    await add_reading(db_session, city="Ottawa", timestamp=base + timedelta(hours=2), temperature=2.0)
    await add_reading(db_session, city="Ottawa", timestamp=base + timedelta(hours=4), temperature=3.0)

    cutoff = (base + timedelta(hours=3)).isoformat()
    response = await api_client.get("/readings", params={"city": "Ottawa", "until": cutoff})
    temps = [r["temperature"] for r in response.json()["readings"]]
    assert 3.0 not in temps
    assert 1.0 in temps
    assert 2.0 in temps


@pytest.mark.asyncio
async def test_readings_offset_pagination(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    base = datetime(2026, 5, 26, 10, 0, tzinfo=UTC)
    for i in range(5):
        await add_reading(
            db_session, city="Ottawa",
            timestamp=base + timedelta(hours=i),
            temperature=float(i),
        )

    page1 = await api_client.get("/readings", params={"city": "Ottawa", "limit": 2, "offset": 0})
    page2 = await api_client.get("/readings", params={"city": "Ottawa", "limit": 2, "offset": 2})
    temps1 = [r["temperature"] for r in page1.json()["readings"]]
    temps2 = [r["temperature"] for r in page2.json()["readings"]]
    assert len(temps1) == 2
    assert len(temps2) == 2
    assert set(temps1).isdisjoint(set(temps2))


@pytest.mark.asyncio
async def test_events_since_filter(
    api_client: AsyncClient, db_session: AsyncSession
) -> None:
    base = datetime(2026, 5, 26, 10, 0, tzinfo=UTC)
    await add_event(db_session, type=EventType.RAPID_CHANGE.value, city="Ottawa",
                    description="old", timestamp=base)
    await add_event(db_session, type=EventType.RAPID_CHANGE.value, city="Ottawa",
                    description="new", timestamp=base + timedelta(hours=3))

    cutoff = (base + timedelta(hours=1)).isoformat()
    response = await api_client.get("/events", params={"city": "Ottawa", "since": cutoff})
    descriptions = [e["description"] for e in response.json()["events"]]
    assert "old" not in descriptions
    assert "new" in descriptions


@pytest.mark.asyncio
async def test_readings_limit_validation_error(api_client: AsyncClient) -> None:
    response = await api_client.get("/readings", params={"limit": 201})
    assert response.status_code == 422
    payload = response.json()
    assert payload["detail"]
