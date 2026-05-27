from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.detectors.anomaly import TemperatureAnomalyDetector
from app.detectors.national import NationalContrastDetector
from app.detectors.rapid_change import RapidChangeDetector
from app.detectors.spells import SustainedColdSpellDetector, SustainedHeatSpellDetector
from app.detectors.storm import CompoundStormDetector, PrecipitationSurgeDetector
from app.detectors.transition import WeatherCodeTransitionDetector
from app.models import Event, Reading

EVENT_DETECTORS = [
    TemperatureAnomalyDetector(),
    RapidChangeDetector(),
    CompoundStormDetector(),
    WeatherCodeTransitionDetector(),
    SustainedHeatSpellDetector(),
    SustainedColdSpellDetector(),
    PrecipitationSurgeDetector(),
]

_national_detector = NationalContrastDetector()


async def evaluate_new_reading(reading: Reading, session: AsyncSession) -> list[Event]:
    events: list[Event] = []
    for detector in EVENT_DETECTORS:
        event = await detector.evaluate(reading, session)
        if event is not None:
            events.append(event)
    return events


async def evaluate_national_contrast(session: AsyncSession) -> Event | None:
    return await _national_detector.evaluate_cycle(session)
