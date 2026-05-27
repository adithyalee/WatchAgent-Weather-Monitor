from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.detectors._base import (
    CITY_SPELL_CONFIG,
    SPELL_MIN_CONSECUTIVE,
    BaseEventDetector,
    _as_utc,
)
from app.event_types import EventType
from app.models import Event, Reading


class _BaseSustainedSpellDetector(BaseEventDetector):
    """Base for heat/cold spell detectors.

    Fires when SPELL_MIN_CONSECUTIVE consecutive readings all exceed the city's
    threshold. Using three consecutive hourly readings (~3h of sustained conditions)
    distinguishes a genuine spell from a transient spike — the anomaly detector
    catches one-reading outliers, while this catches sustained extremes.

    City thresholds are calibrated to local risk levels:
    - Vancouver cold ≤-3°C: freeze warning level for uninsulated infrastructure
    - Vancouver heat ≥28°C: low AC penetration (<40% of homes) makes this dangerous
    - Ottawa cold ≤-20°C / heat ≥30°C: Environment Canada alert thresholds
    - Toronto cold ≤-15°C / heat ≥32°C: Lake Ontario-moderated equivalents

    Severity scales with consecutive reading count: 8+ hours = 1.0 (maximum).
    """

    event_type: EventType
    cooldown = timedelta(hours=12)
    _spell_label: str
    _threshold_key: str

    def _passes(self, t: float, city: str) -> bool:
        raise NotImplementedError

    async def evaluate(self, reading: Reading, session: AsyncSession) -> Event | None:
        if await self._is_in_cooldown(reading.city, session, _as_utc(reading.timestamp)):
            return None

        stmt = (
            select(Reading.apparent_temperature)
            .where(Reading.city == reading.city)
            .order_by(desc(Reading.timestamp))
            .limit(SPELL_MIN_CONSECUTIVE)
        )
        recent = list((await session.execute(stmt)).scalars().all())
        if len(recent) < SPELL_MIN_CONSECUTIVE:
            return None
        if not all(self._passes(t, reading.city) for t in recent):
            return None

        all_stmt = (
            select(Reading.apparent_temperature)
            .where(Reading.city == reading.city)
            .order_by(desc(Reading.timestamp))
            .limit(24)
        )
        all_temps = list((await session.execute(all_stmt)).scalars().all())
        consecutive = 0
        for t in all_temps:
            if self._passes(t, reading.city):
                consecutive += 1
            else:
                break

        cfg = CITY_SPELL_CONFIG[reading.city]
        threshold = cfg[self._threshold_key]
        direction = "above" if self._threshold_key == "heat" else "below"

        description = (
            f"{self._spell_label} spell in {reading.city}: {consecutive} consecutive "
            f"reading(s) {direction} {threshold:.1f}°C "
            f"(current apparent temp: {reading.apparent_temperature:.1f}°C)."
        )
        return Event(
            type=self.event_type.value,
            city=reading.city,
            timestamp=reading.timestamp,
            description=description,
            severity=round(min(consecutive / 8.0, 1.0), 3),
            reading_ids=[reading.id],
            created_at=datetime.now(UTC),
        )


class SustainedHeatSpellDetector(_BaseSustainedSpellDetector):
    event_type = EventType.SUSTAINED_HEAT_SPELL
    _spell_label = "Heat"
    _threshold_key = "heat"

    def _passes(self, t: float, city: str) -> bool:
        return t >= CITY_SPELL_CONFIG[city]["heat"]


class SustainedColdSpellDetector(_BaseSustainedSpellDetector):
    event_type = EventType.SUSTAINED_COLD_SPELL
    _spell_label = "Cold"
    _threshold_key = "cold"

    def _passes(self, t: float, city: str) -> bool:
        return t <= CITY_SPELL_CONFIG[city]["cold"]
