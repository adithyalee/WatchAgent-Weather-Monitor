"""Tests for weather event detection logic and cooldown behaviour."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.detectors import (
    CompoundStormDetector,
    NationalContrastDetector,
    PrecipitationSurgeDetector,
    RapidChangeDetector,
    SustainedColdSpellDetector,
    SustainedHeatSpellDetector,
    TemperatureAnomalyDetector,
    WeatherCodeTransitionDetector,
    evaluate_new_reading,
)
from app.event_types import EventType
from app.models import Event, Reading
from tests.conftest import add_event, add_reading, make_reading


# ---------------------------------------------------------------------------
# TemperatureAnomalyDetector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "history_temps,new_temp,should_fire",
    [
        ([18, 19, 20, 21, 20, 19], 35.0, True),
        ([20, 21, 19, 20, 20, 21], 20.5, False),
    ],
    ids=["fires_high_anomaly", "does_not_fire_normal"],
)
async def test_temperature_anomaly(
    db_session: AsyncSession,
    history_temps: list[float],
    new_temp: float,
    should_fire: bool,
) -> None:
    base = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
    for idx, temp in enumerate(history_temps):
        await add_reading(
            db_session,
            city="Toronto",
            timestamp=base + timedelta(hours=idx),
            apparent_temperature=temp,
        )

    reading = await add_reading(
        db_session,
        city="Toronto",
        timestamp=base + timedelta(hours=len(history_temps)),
        apparent_temperature=new_temp,
    )

    event = await TemperatureAnomalyDetector().evaluate(reading, db_session)
    if should_fire:
        assert event is not None
        assert event.type == EventType.TEMPERATURE_ANOMALY.value
        assert 0.0 < event.severity <= 1.0
    else:
        assert event is None


@pytest.mark.asyncio
async def test_temperature_anomaly_not_enough_history(db_session: AsyncSession) -> None:
    reading = await add_reading(db_session, apparent_temperature=40.0)
    assert await TemperatureAnomalyDetector().evaluate(reading, db_session) is None


@pytest.mark.asyncio
async def test_temperature_anomaly_cooldown(db_session: AsyncSession) -> None:
    base = datetime(2026, 5, 20, 0, 0, tzinfo=UTC)
    for idx in range(6):
        await add_reading(db_session, timestamp=base + timedelta(hours=idx), apparent_temperature=20.0)
    reading = await add_reading(
        db_session, timestamp=base + timedelta(hours=6), apparent_temperature=40.0
    )
    detector = TemperatureAnomalyDetector()
    first = await detector.evaluate(reading, db_session)
    assert first is not None
    db_session.add(first)
    await db_session.commit()

    second_reading = await add_reading(
        db_session, timestamp=base + timedelta(hours=7), apparent_temperature=42.0
    )
    assert await detector.evaluate(second_reading, db_session) is None


# ---------------------------------------------------------------------------
# RapidChangeDetector — city-specific thresholds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "city,prev_temp,new_temp,prev_wind,new_wind,should_fire",
    [
        # Ottawa: 4°C threshold, fires
        ("Ottawa",    10.0, 15.0, 10.0, 12.0, True),
        # Ottawa: 3°C delta, does not fire (threshold is 4°C)
        ("Ottawa",    20.0, 23.0, 10.0, 12.0, False),
        # Ottawa: wind delta ≥25 km/h, fires
        ("Ottawa",    20.0, 20.0, 10.0, 40.0, True),
        # Vancouver: 3°C threshold, fires on a 3.5°C delta
        ("Vancouver", 15.0, 18.5, 10.0, 12.0, True),
        # Vancouver: 2.5°C delta, does not fire (threshold is 3°C)
        ("Vancouver", 15.0, 17.5, 10.0, 12.0, False),
        # Vancouver: wind delta ≥20 km/h, fires
        ("Vancouver", 15.0, 15.0, 10.0, 32.0, True),
    ],
    ids=[
        "ottawa_fires_temp", "ottawa_no_fire_small_delta", "ottawa_fires_wind",
        "vancouver_fires_temp", "vancouver_no_fire_small_delta", "vancouver_fires_wind",
    ],
)
async def test_rapid_change(
    db_session: AsyncSession,
    city: str,
    prev_temp: float,
    new_temp: float,
    prev_wind: float,
    new_wind: float,
    should_fire: bool,
) -> None:
    ts = datetime(2026, 5, 26, 10, 0, tzinfo=UTC)
    await add_reading(db_session, city=city, timestamp=ts, apparent_temperature=prev_temp, wind_speed=prev_wind)
    reading = await add_reading(
        db_session, city=city, timestamp=ts + timedelta(minutes=30),
        apparent_temperature=new_temp, wind_speed=new_wind,
    )
    event = await RapidChangeDetector().evaluate(reading, db_session)
    assert (event is not None) == should_fire
    if should_fire and event is not None:
        assert 0.0 < event.severity <= 1.0


@pytest.mark.asyncio
async def test_rapid_change_cooldown(db_session: AsyncSession) -> None:
    ts = datetime(2026, 5, 26, 8, 0, tzinfo=UTC)
    await add_reading(db_session, timestamp=ts, apparent_temperature=10.0)
    reading = await add_reading(
        db_session, timestamp=ts + timedelta(minutes=20), apparent_temperature=16.0
    )
    detector = RapidChangeDetector()
    event = await detector.evaluate(reading, db_session)
    assert event is not None
    db_session.add(event)
    await db_session.commit()

    later = await add_reading(
        db_session, timestamp=ts + timedelta(minutes=40), apparent_temperature=22.0
    )
    assert await detector.evaluate(later, db_session) is None


# ---------------------------------------------------------------------------
# CompoundStormDetector — city-specific thresholds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "city,wind,precip,should_fire",
    [
        # Ottawa: thresholds 50 km/h, 2.0 mm
        ("Ottawa",    55.0, 3.0, True),
        ("Ottawa",    30.0, 5.0, False),   # wind too low
        ("Ottawa",    60.0, 1.0, False),   # precip too low
        # Toronto: thresholds 50 km/h, 2.5 mm — higher precip bar than Ottawa
        ("Toronto",   55.0, 3.0, True),    # both above threshold: fires
        ("Toronto",   55.0, 2.2, False),   # precip 2.2 < 2.5 mm; same reading fires Ottawa
        # Vancouver: thresholds 55 km/h, 8.0 mm — highest bar
        ("Vancouver", 60.0, 9.0, True),
        ("Vancouver", 60.0, 6.0, False),   # 6mm < 8mm threshold
        ("Vancouver", 50.0, 9.0, False),   # 50 km/h < 55 km/h threshold
    ],
    ids=[
        "ottawa_fires", "ottawa_low_wind", "ottawa_low_precip",
        "toronto_fires", "toronto_precip_below_threshold",
        "vancouver_fires", "vancouver_precip_below_threshold", "vancouver_wind_below_threshold",
    ],
)
async def test_compound_storm(
    db_session: AsyncSession,
    city: str,
    wind: float,
    precip: float,
    should_fire: bool,
) -> None:
    reading = await add_reading(db_session, city=city, wind_speed=wind, precipitation=precip)
    event = await CompoundStormDetector().evaluate(reading, db_session)
    assert (event is not None) == should_fire


# ---------------------------------------------------------------------------
# WeatherCodeTransitionDetector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prev_code,curr_code,expected_type,expected_severity",
    [
        (0,  95, EventType.WEATHER_DEGRADATION.value,  0.6),  # clear → thunderstorm
        (65,  1, EventType.WEATHER_IMPROVEMENT.value,  0.3),  # heavy rain → clear
        (65, 95, None, None),  # severe → severe: no boundary crossing
        (1,   3, None, None),  # mild → mild: stays within clear/mild band
        (4,  95, None, None),  # intermediate (4–44) → severe: prev must be clear/mild to fire
    ],
    ids=[
        "fires_degradation", "fires_improvement",
        "no_fire_severe_to_severe", "no_fire_mild_to_mild", "no_fire_intermediate_to_severe",
    ],
)
async def test_weather_code_transition(
    db_session: AsyncSession,
    prev_code: int,
    curr_code: int,
    expected_type: str | None,
    expected_severity: float | None,
) -> None:
    ts = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    await add_reading(db_session, weather_code=prev_code, timestamp=ts)
    reading = await add_reading(
        db_session, weather_code=curr_code, timestamp=ts + timedelta(minutes=30)
    )
    event = await WeatherCodeTransitionDetector().evaluate(reading, db_session)
    if expected_type is None:
        assert event is None
    else:
        assert event is not None
        assert event.type == expected_type
        assert event.severity == expected_severity


# ---------------------------------------------------------------------------
# SustainedHeatSpellDetector / SustainedColdSpellDetector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "city,temps,spell_type,should_fire",
    [
        # Ottawa: heat ≥30°C
        ("Ottawa",    [31.0, 32.0, 33.0], EventType.SUSTAINED_HEAT_SPELL, True),
        # Ottawa: cold ≤-20°C
        ("Ottawa",    [-21.0, -22.0, -25.0], EventType.SUSTAINED_COLD_SPELL, True),
        # Ottawa: heat spell broken by a cool reading
        ("Ottawa",    [25.0, 31.0, 33.0], EventType.SUSTAINED_HEAT_SPELL, False),
        # Vancouver: heat ≥28°C (lower threshold)
        ("Vancouver", [28.5, 29.0, 30.0], EventType.SUSTAINED_HEAT_SPELL, True),
        # Vancouver: cold ≤-3°C
        ("Vancouver", [-4.0, -5.0, -6.0], EventType.SUSTAINED_COLD_SPELL, True),
        # Vancouver: -2°C does not cross -3°C cold threshold
        ("Vancouver", [-2.0, -1.5, -2.5], EventType.SUSTAINED_COLD_SPELL, False),
    ],
    ids=[
        "ottawa_heat_fires", "ottawa_cold_fires", "ottawa_heat_broken",
        "vancouver_heat_fires", "vancouver_cold_fires", "vancouver_cold_no_fire",
    ],
)
async def test_sustained_spell(
    db_session: AsyncSession,
    city: str,
    temps: list[float],
    spell_type: EventType,
    should_fire: bool,
) -> None:
    ts = datetime(2026, 5, 26, 0, 0, tzinfo=UTC)
    for idx, temp in enumerate(temps):
        await add_reading(db_session, city=city, timestamp=ts + timedelta(hours=idx), apparent_temperature=temp)

    latest = (
        await db_session.execute(
            select(Reading)
            .where(Reading.city == city)
            .order_by(desc(Reading.timestamp))
            .limit(1)
        )
    ).scalar_one()

    detector = SustainedHeatSpellDetector() if spell_type == EventType.SUSTAINED_HEAT_SPELL else SustainedColdSpellDetector()
    event = await detector.evaluate(latest, db_session)

    if should_fire:
        assert event is not None
        assert event.type == spell_type.value
        assert event.severity > 0.0
    else:
        assert event is None


@pytest.mark.asyncio
async def test_sustained_spell_insufficient_history(db_session: AsyncSession) -> None:
    ts = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    await add_reading(db_session, city="Ottawa", timestamp=ts, apparent_temperature=33.0)
    await add_reading(db_session, city="Ottawa", timestamp=ts + timedelta(hours=1), apparent_temperature=34.0)
    latest = (await db_session.execute(
        select(Reading).where(Reading.city == "Ottawa").order_by(desc(Reading.timestamp)).limit(1)
    )).scalar_one()
    assert await SustainedHeatSpellDetector().evaluate(latest, db_session) is None


@pytest.mark.asyncio
async def test_sustained_heat_spell_cooldown(db_session: AsyncSession) -> None:
    ts = datetime(2026, 5, 26, 0, 0, tzinfo=UTC)
    for idx in range(3):
        await add_reading(db_session, city="Ottawa", timestamp=ts + timedelta(hours=idx), apparent_temperature=33.0)

    detector = SustainedHeatSpellDetector()
    latest = (await db_session.execute(
        select(Reading).where(Reading.city == "Ottawa").order_by(desc(Reading.timestamp)).limit(1)
    )).scalar_one()
    first = await detector.evaluate(latest, db_session)
    assert first is not None
    db_session.add(first)
    await db_session.commit()

    next_reading = await add_reading(
        db_session, city="Ottawa", timestamp=ts + timedelta(hours=3), apparent_temperature=34.0
    )
    assert await detector.evaluate(next_reading, db_session) is None


# ---------------------------------------------------------------------------
# PrecipitationSurgeDetector — city-specific thresholds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "city,prev_precip,curr_precip,should_fire",
    [
        # Ottawa: threshold 4 mm; dry → 5 mm fires
        ("Ottawa",    0.0, 5.0, True),
        # Ottawa: dry → 3.5 mm, below threshold
        ("Ottawa",    0.0, 3.5, False),
        # Ottawa: already raining → 5 mm, no surge (not dry baseline)
        ("Ottawa",    2.0, 5.0, False),
        # Vancouver: threshold 8 mm; dry → 9 mm fires
        ("Vancouver", 0.5, 9.0, True),
        # Vancouver: dry → 7 mm, below raised threshold
        ("Vancouver", 0.0, 7.0, False),
    ],
    ids=[
        "ottawa_fires", "ottawa_below_threshold", "ottawa_not_dry",
        "vancouver_fires", "vancouver_below_threshold",
    ],
)
async def test_precipitation_surge(
    db_session: AsyncSession,
    city: str,
    prev_precip: float,
    curr_precip: float,
    should_fire: bool,
) -> None:
    ts = datetime(2026, 5, 26, 10, 0, tzinfo=UTC)
    await add_reading(db_session, city=city, timestamp=ts, precipitation=prev_precip)
    reading = await add_reading(
        db_session, city=city, timestamp=ts + timedelta(minutes=30), precipitation=curr_precip
    )
    event = await PrecipitationSurgeDetector().evaluate(reading, db_session)
    assert (event is not None) == should_fire
    if should_fire and event is not None:
        assert event.type == EventType.PRECIPITATION_SURGE.value
        assert 0.0 < event.severity <= 1.0


@pytest.mark.asyncio
async def test_precipitation_surge_cooldown(db_session: AsyncSession) -> None:
    ts = datetime(2026, 5, 26, 10, 0, tzinfo=UTC)
    await add_reading(db_session, city="Ottawa", timestamp=ts, precipitation=0.0)
    first_surge = await add_reading(
        db_session, city="Ottawa", timestamp=ts + timedelta(minutes=30), precipitation=6.0
    )
    detector = PrecipitationSurgeDetector()
    event = await detector.evaluate(first_surge, db_session)
    assert event is not None
    db_session.add(event)
    await db_session.commit()

    second_surge = await add_reading(
        db_session, city="Ottawa", timestamp=ts + timedelta(hours=1), precipitation=8.0
    )
    assert await detector.evaluate(second_surge, db_session) is None


# ---------------------------------------------------------------------------
# NationalContrastDetector
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_national_contrast_fires(db_session: AsyncSession) -> None:
    ts = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    await add_reading(db_session, city="Ottawa", apparent_temperature=30.0, timestamp=ts)
    await add_reading(db_session, city="Toronto", apparent_temperature=25.0, timestamp=ts)
    await add_reading(db_session, city="Vancouver", apparent_temperature=5.0, timestamp=ts)

    event = await NationalContrastDetector().evaluate_cycle(db_session)
    assert event is not None
    assert event.type == EventType.NATIONAL_CONTRAST.value
    assert event.city is None
    assert event.severity > 0.0


@pytest.mark.asyncio
async def test_national_contrast_does_not_fire(db_session: AsyncSession) -> None:
    ts = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    await add_reading(db_session, city="Ottawa", apparent_temperature=22.0, timestamp=ts)
    await add_reading(db_session, city="Toronto", apparent_temperature=20.0, timestamp=ts)
    await add_reading(db_session, city="Vancouver", apparent_temperature=18.0, timestamp=ts)

    assert await NationalContrastDetector().evaluate_cycle(db_session) is None


@pytest.mark.asyncio
async def test_national_contrast_severity_scales_with_spread(db_session: AsyncSession) -> None:
    ts = datetime(2026, 5, 26, 12, 0, tzinfo=UTC)
    await add_reading(db_session, city="Ottawa", apparent_temperature=40.0, timestamp=ts)
    await add_reading(db_session, city="Toronto", apparent_temperature=20.0, timestamp=ts)
    await add_reading(db_session, city="Vancouver", apparent_temperature=0.0, timestamp=ts)

    event = await NationalContrastDetector().evaluate_cycle(db_session)
    assert event is not None
    assert event.severity == 1.0  # 40°C spread / 40 = 1.0


# ---------------------------------------------------------------------------
# evaluate_new_reading integration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_evaluate_new_reading_returns_list(db_session: AsyncSession) -> None:
    ts = datetime(2026, 5, 26, 10, 0, tzinfo=UTC)
    await add_reading(db_session, timestamp=ts, apparent_temperature=10.0)
    reading = make_reading(timestamp=ts + timedelta(minutes=30), apparent_temperature=16.0)
    db_session.add(reading)
    await db_session.flush()
    events = await evaluate_new_reading(reading, db_session)
    assert any(e.type == EventType.RAPID_CHANGE.value for e in events)
