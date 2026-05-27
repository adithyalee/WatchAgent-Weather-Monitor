from enum import StrEnum


class EventType(StrEnum):
    TEMPERATURE_ANOMALY = "TEMPERATURE_ANOMALY"
    RAPID_CHANGE = "RAPID_CHANGE"
    STORM_CONDITIONS = "STORM_CONDITIONS"
    WEATHER_DEGRADATION = "WEATHER_DEGRADATION"
    WEATHER_IMPROVEMENT = "WEATHER_IMPROVEMENT"
    NATIONAL_CONTRAST = "NATIONAL_CONTRAST"
    SUSTAINED_HEAT_SPELL = "SUSTAINED_HEAT_SPELL"
    SUSTAINED_COLD_SPELL = "SUSTAINED_COLD_SPELL"
    PRECIPITATION_SURGE = "PRECIPITATION_SURGE"


CLEAR_MILD_CODES = frozenset(range(0, 4))


def is_severe_weather_code(code: int) -> bool:
    """WMO codes 45+ cover fog, drizzle, rain, snow, showers, and thunderstorms."""
    return code not in CLEAR_MILD_CODES and code >= 45


def is_clear_mild_weather_code(code: int) -> bool:
    return code in CLEAR_MILD_CODES
