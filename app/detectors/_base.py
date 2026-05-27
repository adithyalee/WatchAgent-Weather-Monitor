from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.event_types import EventType
from app.models import Event, Reading

CITIES = ("Ottawa", "Toronto", "Vancouver")

# ---------------------------------------------------------------------------
# City-specific meteorological thresholds
#
# Ottawa (45°N, continental): annual range -35°C to +35°C, 833 mm/yr.
#   Front passages produce 4°C/h swings regularly. Cold extreme alerts issued
#   by Environment Canada at ≤-25°C; we use ≤-20°C for earlier warning.
#   Storm bar is lower (2 mm + 50 km/h) because Ottawa is not a high-rainfall city.
#
# Toronto (43°N, continental + Lake Ontario moderation): range -18°C to +32°C,
#   831 mm/yr. Lake effect reduces extremes; cold alert threshold shifted to -15°C.
#
# Vancouver (49°N, oceanic): annual range -3°C to +28°C, 1155 mm/yr.
#   Oceanic stability makes even a 3°C/h change unusual enough to warrant detection.
#   Heat spell threshold: 28°C — city lacks AC infrastructure; the 2021 heat dome
#   killed 619 people in BC, almost all in the Metro Vancouver area.
#   Cold spell threshold: -3°C — Environment Canada issues Metro Vancouver freeze
#   warnings at this level because water infrastructure is uninsulated for hard frost.
#   Storm precipitation threshold raised to 8 mm because moderate Pacific rain
#   (4-5 mm/h) is completely unremarkable in autumn and winter.
# ---------------------------------------------------------------------------

CITY_STORM_CONFIG: dict[str, dict[str, float]] = {
    "Ottawa":    {"wind_kmh": 50.0, "precip_mm": 2.0},
    "Toronto":   {"wind_kmh": 50.0, "precip_mm": 2.5},
    "Vancouver": {"wind_kmh": 55.0, "precip_mm": 8.0},
}

CITY_RAPID_CHANGE_CONFIG: dict[str, dict[str, float]] = {
    "Ottawa":    {"temp_c": 4.0, "wind_kmh": 25.0},
    "Toronto":   {"temp_c": 4.0, "wind_kmh": 25.0},
    "Vancouver": {"temp_c": 3.0, "wind_kmh": 20.0},
}

CITY_SPELL_CONFIG: dict[str, dict[str, float]] = {
    "Ottawa":    {"heat": 30.0, "cold": -20.0},
    "Toronto":   {"heat": 32.0, "cold": -15.0},
    "Vancouver": {"heat": 28.0, "cold": -3.0},
}

CITY_PRECIP_SURGE_CONFIG: dict[str, float] = {
    "Ottawa":    4.0,
    "Toronto":   4.0,
    "Vancouver": 8.0,
}

SPELL_MIN_CONSECUTIVE = 3


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class BaseEventDetector(ABC):
    event_type: EventType
    cooldown: timedelta | None = None
    global_cooldown: bool = False

    async def _is_in_cooldown(
        self,
        city: str | None,
        session: AsyncSession,
        as_of: datetime | None = None,
    ) -> bool:
        if self.cooldown is None:
            return False

        reference = _as_utc(as_of or datetime.now(UTC))
        cutoff = reference - self.cooldown
        stmt = select(Event).where(
            Event.type == self.event_type.value,
            Event.timestamp >= cutoff,
        )
        if not self.global_cooldown and city is not None:
            stmt = stmt.where(Event.city == city)
        elif self.global_cooldown:
            stmt = stmt.where(Event.city.is_(None))

        result = await session.execute(stmt.limit(1))
        return result.scalar_one_or_none() is not None

    @abstractmethod
    async def evaluate(
        self,
        reading: Reading,
        session: AsyncSession,
    ) -> Event | None:
        ...
