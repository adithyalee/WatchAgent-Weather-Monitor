from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.detectors._base import (
    CITY_PRECIP_SURGE_CONFIG,
    CITY_STORM_CONFIG,
    BaseEventDetector,
    _as_utc,
)
from app.event_types import EventType
from app.models import Event, Reading


class CompoundStormDetector(BaseEventDetector):
    """Compound condition: simultaneous high wind AND heavy precipitation.

    Requiring both fields to exceed their thresholds avoids false positives from
    calm-wind rainstorms (covered by PrecipitationSurgeDetector) and dry gales.
    Vancouver's precipitation threshold is raised to 8 mm because the city
    receives 1155 mm/year; a 2 mm reading during a Pacific front is routine.
    """

    event_type = EventType.STORM_CONDITIONS
    cooldown = timedelta(hours=6)

    async def evaluate(self, reading: Reading, session: AsyncSession) -> Event | None:
        if await self._is_in_cooldown(reading.city, session, _as_utc(reading.timestamp)):
            return None

        cfg = CITY_STORM_CONFIG[reading.city]
        if reading.wind_speed <= cfg["wind_kmh"] or reading.precipitation <= cfg["precip_mm"]:
            return None

        severity = round(
            min((reading.wind_speed / 100.0 + reading.precipitation / 20.0) / 2.0, 1.0),
            3,
        )
        description = (
            f"Storm conditions in {reading.city}: wind {reading.wind_speed:.1f} km/h "
            f"and precipitation {reading.precipitation:.1f} mm "
            f"(city thresholds: >{cfg['wind_kmh']} km/h, >{cfg['precip_mm']} mm)."
        )
        return Event(
            type=self.event_type.value,
            city=reading.city,
            timestamp=reading.timestamp,
            description=description,
            severity=severity,
            reading_ids=[reading.id],
            created_at=datetime.now(UTC),
        )


class PrecipitationSurgeDetector(BaseEventDetector):
    """Sudden onset of heavy rain from a dry baseline.

    Distinct from CompoundStormDetector (which requires simultaneous high wind):
    this catches slow-moving lows that deliver heavy precipitation with calm winds,
    a pattern common in late-season Atlantic systems reaching Ottawa and Toronto.
    Vancouver's threshold (8 mm) is raised for the same reason as its storm
    threshold — Pacific fronts routinely produce 4-6 mm/h without alarm.
    """

    event_type = EventType.PRECIPITATION_SURGE
    cooldown = timedelta(hours=3)
    _dry_threshold = 1.0

    async def evaluate(self, reading: Reading, session: AsyncSession) -> Event | None:
        if await self._is_in_cooldown(reading.city, session, _as_utc(reading.timestamp)):
            return None

        surge_threshold = CITY_PRECIP_SURGE_CONFIG[reading.city]
        if reading.precipitation < surge_threshold:
            return None

        stmt = (
            select(Reading)
            .where(Reading.city == reading.city, Reading.id != reading.id)
            .order_by(desc(Reading.timestamp))
            .limit(1)
        )
        previous = (await session.execute(stmt)).scalar_one_or_none()
        if previous is None or previous.precipitation >= self._dry_threshold:
            return None

        severity = round(min(reading.precipitation / (surge_threshold * 2.0), 1.0), 3)
        description = (
            f"Precipitation surge in {reading.city}: {reading.precipitation:.1f} mm/h "
            f"from a dry reading ({previous.precipitation:.1f} mm/h). "
            f"City surge threshold: {surge_threshold:.1f} mm/h."
        )
        return Event(
            type=self.event_type.value,
            city=reading.city,
            timestamp=reading.timestamp,
            description=description,
            severity=severity,
            reading_ids=[previous.id, reading.id],
            created_at=datetime.now(UTC),
        )
