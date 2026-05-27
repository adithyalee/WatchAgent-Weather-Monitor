import logging
import sys

from pythonjsonlogger.json import JsonFormatter

from app.config import get_settings


def setup_logging(service: str) -> logging.Logger:
    settings = get_settings()
    logger = logging.getLogger(service)
    if logger.handlers:
        return logger

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter(
            "%(asctime)s %(name)s %(levelname)s %(message)s",
            rename_fields={"asctime": "timestamp", "levelname": "level"},
        )
    )
    logger.addHandler(handler)
    logger.setLevel(settings.log_level.upper())
    logger.propagate = False
    return logger
