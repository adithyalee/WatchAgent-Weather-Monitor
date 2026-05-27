# All detector logic has moved to app/detectors/.
# This module re-exports everything so existing imports continue to work.
from app.detectors import (  # noqa: F401
    CITIES,
    EVENT_DETECTORS,
    CompoundStormDetector,
    NationalContrastDetector,
    PrecipitationSurgeDetector,
    RapidChangeDetector,
    SustainedColdSpellDetector,
    SustainedHeatSpellDetector,
    TemperatureAnomalyDetector,
    WeatherCodeTransitionDetector,
    evaluate_national_contrast,
    evaluate_new_reading,
)
from app.detectors._base import (  # noqa: F401
    CITY_PRECIP_SURGE_CONFIG,
    CITY_RAPID_CHANGE_CONFIG,
    CITY_SPELL_CONFIG,
    CITY_STORM_CONFIG,
    SPELL_MIN_CONSECUTIVE,
    BaseEventDetector,
    _as_utc,
)
