from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.detectors._base import BaseEventDetector, _as_utc
from app.event_types import EventType
from app.models import Event, Reading


class TemperatureAnomalyDetector(BaseEventDetector):
    """Statistical anomaly: apparent temp deviates >2σ from the city's own 24h mean.

    Using the city's own rolling mean makes this inherently city-relative — a 30°C
    reading in Vancouver after days of 15°C is far more anomalous than the same
    reading in Ottawa during a summer heat wave. Requires ≥6 readings (6h of data)
    before firing to avoid false positives from short windows.
    """

    event_type = EventType.TEMPERATURE_ANOMALY
    cooldown = timedelta(hours=4)
    min_readings = 6

    async def evaluate(self, reading: Reading, session: AsyncSession) -> Event | None:
        if await self._is_in_cooldown(reading.city, session, _as_utc(reading.timestamp)):
            return None

        window_start = reading.timestamp - timedelta(hours=24)
        stmt = select(Reading.apparent_temperature).where(
            Reading.city == reading.city,
            Reading.timestamp >= window_start,
            Reading.timestamp <= reading.timestamp,
        )
        values = list((await session.execute(stmt)).scalars().all())
        if len(values) < self.min_readings:
            return None

        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        std = variance**0.5
        if std < 0.01:
            return None

        value = reading.apparent_temperature
        if value > mean + 2 * std:
            direction = "above"
        elif value < mean - 2 * std:
            direction = "below"
        else:
            return None

        sigma = abs(value - mean) / std
        description = (
            f"Apparent temp {value:.1f}°C is {sigma:.1f}σ {direction} 24h mean "
            f"({mean:.1f}°C, σ={std:.1f}) in {reading.city}."
        )
        return Event(
            type=self.event_type.value,
            city=reading.city,
            timestamp=reading.timestamp,
            description=description,
            severity=round(min(sigma / 4.0, 1.0), 3),
            reading_ids=[reading.id],
            created_at=datetime.now(UTC),
        )
