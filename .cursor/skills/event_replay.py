#!/usr/bin/env python3
"""Replay stored readings through the event detection engine.

Two modes:
  --fresh  Replay all readings in a clean in-memory database from scratch.
           Cooldowns are not influenced by any stored events, showing what
           the detector logic would emit on a brand-new dataset.
  (default) Replay against the live production database. Stored events affect
            cooldowns, so results reflect what new events would fire *now*
            if the readings were re-evaluated in their original order.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add project root to sys.path to resolve app imports
project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.event_detector import evaluate_national_contrast, evaluate_new_reading
from app.models import Base, Event, Reading


def _async_db_url() -> str:
    return os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./data/weather.db")


def _sqlite_path() -> Path:
    url = _async_db_url()
    if url.startswith("sqlite+aiosqlite:///"):
        return Path(url.removeprefix("sqlite+aiosqlite:///"))
    raise SystemExit(f"Unsupported DATABASE_URL: {url}")


def _check_db_exists() -> None:
    path = _sqlite_path()
    if not path.exists():
        raise SystemExit(f"Database not found at {path}. Run docker compose up first.")


def _event_to_dict(event: Event) -> dict:
    return {
        "type": event.type,
        "city": event.city,
        "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        "description": event.description,
        "severity": event.severity,
        "reading_ids": event.reading_ids,
    }


async def _load_readings(session: AsyncSession, since: str | None, limit: int) -> list[Reading]:
    stmt = select(Reading).order_by(Reading.timestamp.asc())
    if since:
        stmt = stmt.where(Reading.timestamp >= datetime.fromisoformat(since))
    stmt = stmt.limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def replay(since: str | None, limit: int, fresh: bool) -> dict:
    prod_url = _async_db_url()
    prod_engine = create_async_engine(prod_url)
    prod_factory = async_sessionmaker(prod_engine, expire_on_commit=False)

    reading_dicts: list[dict] = []
    stored_events: int = 0

    async with prod_factory() as session:
        readings = await _load_readings(session, since, limit)
        for r in readings:
            reading_dicts.append(
                {
                    "city": r.city,
                    "timestamp": r.timestamp,
                    "temperature": r.temperature,
                    "apparent_temperature": r.apparent_temperature,
                    "precipitation": r.precipitation,
                    "wind_speed": r.wind_speed,
                    "weather_code": r.weather_code,
                    "fetched_at": r.fetched_at,
                }
            )
        stmt = select(func.count()).select_from(Event)
        if since:
            stmt = stmt.where(Event.timestamp >= datetime.fromisoformat(since))
        stored_events = (await session.scalar(stmt)) or 0

    await prod_engine.dispose()

    if fresh:
        replay_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with replay_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    else:
        replay_engine = create_async_engine(prod_url)

    replay_factory = async_sessionmaker(replay_engine, expire_on_commit=False)
    simulated: list[dict] = []

    async with replay_factory() as session:
        if fresh:
            for rd in reading_dicts:
                reading = Reading(**rd)
                session.add(reading)
                await session.flush()

                events = await evaluate_new_reading(reading, session)
                for event in events:
                    session.add(event)
                    simulated.append(_event_to_dict(event))

                national = await evaluate_national_contrast(session)
                if national:
                    session.add(national)
                    simulated.append(_event_to_dict(national))

            await session.commit()
        else:
            readings_live = await _load_readings(session, since, limit)
            for reading in readings_live:
                events = await evaluate_new_reading(reading, session)
                for event in events:
                    simulated.append(_event_to_dict(event))
            national = await evaluate_national_contrast(session)
            if national:
                simulated.append(_event_to_dict(national))

    await replay_engine.dispose()

    return {
        "mode": "fresh" if fresh else "live",
        "readings_replayed": len(reading_dicts),
        "simulated_events": len(simulated),
        "stored_events_in_window": stored_events,
        "delta": len(simulated) - stored_events,
        "simulated": simulated,
    }


def _markdown(payload: dict) -> None:
    print(f"# Event Replay Report (mode={payload['mode']})")
    print(f"\n## Summary")
    print(f"- Readings replayed: {payload['readings_replayed']}")
    print(f"- Simulated events: {payload['simulated_events']}")
    print(f"- Stored events in window: {payload['stored_events_in_window']}")
    delta = payload["delta"]
    sign = "+" if delta >= 0 else ""
    print(f"- Delta (simulated − stored): {sign}{delta}")
    if payload["simulated"]:
        print(f"\n## Simulated Events")
        by_type: dict[str, int] = {}
        for e in payload["simulated"]:
            by_type[e["type"]] = by_type.get(e["type"], 0) + 1
        for t, n in sorted(by_type.items(), key=lambda x: -x[1]):
            print(f"- {t}: {n}")
        print(f"\n## Top 10 by Severity")
        top = sorted(payload["simulated"], key=lambda x: x.get("severity", 0), reverse=True)[:10]
        for e in top:
            city = e["city"] or "national"
            ts = (e["timestamp"] or "")[:16]
            print(f"- [{e['type']}] severity={e['severity']:.2f} {city} {ts} — {e['description'][:80]}")
    else:
        print("\nNo events simulated in this window.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay event detection on stored readings")
    parser.add_argument("--since", help="ISO timestamp lower bound for readings")
    parser.add_argument("--limit", type=int, default=500, help="Max readings to replay")
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Replay in a clean in-memory database (no cooldown carryover)",
    )
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()

    _check_db_exists()
    payload = asyncio.run(replay(args.since, args.limit, args.fresh))
    if args.format == "json":
        print(json.dumps(payload, indent=2, default=str))
    else:
        _markdown(payload)


if __name__ == "__main__":
    main()
