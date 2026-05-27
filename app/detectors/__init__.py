from app.detectors._base import CITIES
from app.detectors.anomaly import TemperatureAnomalyDetector
from app.detectors.national import NationalContrastDetector
from app.detectors.rapid_change import RapidChangeDetector
from app.detectors.registry import EVENT_DETECTORS, evaluate_national_contrast, evaluate_new_reading
from app.detectors.spells import SustainedColdSpellDetector, SustainedHeatSpellDetector
from app.detectors.storm import CompoundStormDetector, PrecipitationSurgeDetector
from app.detectors.transition import WeatherCodeTransitionDetector

__all__ = [
    "CITIES",
    "EVENT_DETECTORS",
    "evaluate_new_reading",
    "evaluate_national_contrast",
    "TemperatureAnomalyDetector",
    "RapidChangeDetector",
    "CompoundStormDetector",
    "WeatherCodeTransitionDetector",
    "SustainedHeatSpellDetector",
    "SustainedColdSpellDetector",
    "PrecipitationSurgeDetector",
    "NationalContrastDetector",
]
