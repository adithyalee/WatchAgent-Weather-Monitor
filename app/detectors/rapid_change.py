from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.detectors._base import CITY_RAPID_CHANGE_CONFIG, BaseEventDetector, _as_utc
from app.event_types import EventType
from app.models import Event, Reading


class RapidChangeDetector(BaseEventDetector):
    """Single-hour delta: catches front passages and pressure drops.

    City-specific thresholds reflect baseline variability: Vancouver's oceanic
    stability (annual temp range ~15°C) makes a 3°C/h change remarkable, whereas
    Ottawa's continental climate (range ~55°C) normalises 4°C/h swings.
    """

    event_type = EventType.RAPID_CHANGE
    cooldown = timedelta(hours=2)

    async def evaluate(self, reading: Reading, session: AsyncSession) -> Event | None:
        if await self._is_in_cooldown(reading.city, session, _as_utc(reading.timestamp)):
            return None

        cfg = CITY_RAPID_CHANGE_CONFIG[reading.city]
        stmt = (
            select(Reading)
            .where(Reading.city == reading.city, Reading.id != reading.id)
            .order_by(desc(Reading.timestamp))
            .limit(1)
        )
        previous = (await session.execute(stmt)).scalar_one_or_none()
        if previous is None:
            return None

        delta_hours = (
            abs((_as_utc(reading.timestamp) - _as_utc(previous.timestamp)).total_seconds()) / 3600
        )
        if delta_hours > 1.0:
            return None

        delta_temp = abs(reading.apparent_temperature - previous.apparent_temperature)
        delta_wind = abs(reading.wind_speed - previous.wind_speed)

        reasons: list[str] = []
        if delta_temp >= cfg["temp_c"]:
            reasons.append(
                f"apparent temperature changed {delta_temp:.1f}°C "
                f"({previous.apparent_temperature:.1f}°C → {reading.apparent_temperature:.1f}°C)"
            )
        if delta_wind >= cfg["wind_kmh"]:
            reasons.append(
                f"wind speed changed {delta_wind:.1f} km/h "
                f"({previous.wind_speed:.1f} → {reading.wind_speed:.1f})"
            )
        if not reasons:
            return None

        description = f"Rapid change in {reading.city}: " + "; ".join(reasons) + "."
        return Event(
            type=self.event_type.value,
            city=reading.city,
            timestamp=reading.timestamp,
            description=description,
            severity=round(min(max(delta_temp / 8.0, delta_wind / 50.0), 1.0), 3),
            reading_ids=[previous.id, reading.id],
            created_at=datetime.now(UTC),
        )
