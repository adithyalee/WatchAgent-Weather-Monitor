from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.detectors._base import BaseEventDetector
from app.event_types import EventType, is_clear_mild_weather_code, is_severe_weather_code
from app.models import Event, Reading


class WeatherCodeTransitionDetector(BaseEventDetector):
    """Qualitative state change using the authoritative WMO weather code scale.

    Fires only when the code crosses the clear/mild ↔ severe boundary (not on
    every code change), communicating meaningful transitions rather than noise.
    No cooldown: transitions are inherently infrequent (API updates hourly) and
    re-alerting on the same boundary is the right behaviour during oscillation.
    Degradation events score higher severity (0.6) than improvements (0.3) to
    reflect the asymmetric risk of worsening conditions.
    """

    event_type = EventType.WEATHER_DEGRADATION
    cooldown = None

    async def evaluate(self, reading: Reading, session: AsyncSession) -> Event | None:
        stmt = (
            select(Reading.weather_code)
            .where(Reading.city == reading.city, Reading.id != reading.id)
            .order_by(desc(Reading.timestamp))
            .limit(1)
        )
        previous_code = (await session.execute(stmt)).scalar_one_or_none()

        if previous_code is None or previous_code == reading.weather_code:
            return None

        if is_clear_mild_weather_code(previous_code) and is_severe_weather_code(reading.weather_code):
            event_type = EventType.WEATHER_DEGRADATION
            severity = 0.6
        elif is_severe_weather_code(previous_code) and is_clear_mild_weather_code(reading.weather_code):
            event_type = EventType.WEATHER_IMPROVEMENT
            severity = 0.3
        else:
            return None

        description = (
            f"Weather transition in {reading.city}: WMO code "
            f"{previous_code} → {reading.weather_code} ({event_type.value})."
        )
        return Event(
            type=event_type.value,
            city=reading.city,
            timestamp=reading.timestamp,
            description=description,
            severity=severity,
            reading_ids=[reading.id],
            created_at=datetime.now(UTC),
        )
