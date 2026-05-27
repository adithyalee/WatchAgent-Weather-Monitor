from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.detectors._base import CITIES, BaseEventDetector
from app.event_types import EventType
from app.models import Event, Reading


class NationalContrastDetector(BaseEventDetector):
    """Cross-country apparent temperature spread across all three cities.

    A spread >20°C in a country the size of Canada is notable but not rare — the
    threshold is intentionally high to capture genuinely extreme simultaneous
    contrasts (e.g., Ottawa at -25°C while Vancouver sits at +5°C). Global cooldown
    prevents event floods during stable high-contrast conditions. Severity scales
    linearly up to 40°C spread.
    """

    event_type = EventType.NATIONAL_CONTRAST
    cooldown = timedelta(hours=6)
    global_cooldown = True

    async def evaluate(self, reading: Reading, session: AsyncSession) -> Event | None:
        return None

    async def evaluate_cycle(self, session: AsyncSession) -> Event | None:
        if await self._is_in_cooldown(None, session, datetime.now(UTC)):
            return None

        latest_by_city: dict[str, tuple[float, int]] = {}
        for city in CITIES:
            stmt = (
                select(Reading.apparent_temperature, Reading.id)
                .where(Reading.city == city)
                .order_by(desc(Reading.timestamp))
                .limit(1)
            )
            row = (await session.execute(stmt)).one_or_none()
            if row is None:
                return None
            latest_by_city[city] = (row[0], row[1])

        temps = {city: data[0] for city, data in latest_by_city.items()}
        hot_city = max(temps, key=temps.get)  # type: ignore[arg-type]
        cold_city = min(temps, key=temps.get)  # type: ignore[arg-type]
        spread = temps[hot_city] - temps[cold_city]

        if spread <= 20:
            return None

        reading_ids = [latest_by_city[city][1] for city in CITIES]
        description = (
            f"National temperature contrast {spread:.1f}°C: "
            f"{hot_city} {temps[hot_city]:.1f}°C vs {cold_city} {temps[cold_city]:.1f}°C."
        )
        return Event(
            type=self.event_type.value,
            city=None,
            timestamp=datetime.now(UTC),
            description=description,
            severity=round(min(spread / 40.0, 1.0), 3),
            reading_ids=reading_ids,
            created_at=datetime.now(UTC),
        )
