#!/usr/bin/env python3
"""Query stored weather readings and events for analysis inside Cursor."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path


def _db_path() -> Path:
    url = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./data/weather.db")
    if url.startswith("sqlite+aiosqlite:///"):
        return Path(url.removeprefix("sqlite+aiosqlite:///"))
    raise SystemExit(f"Unsupported DATABASE_URL for analyzer: {url}")


def _connect() -> sqlite3.Connection:
    path = _db_path()
    if not path.exists():
        raise SystemExit(f"Database not found at {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _window_start(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat()


def _compute_slope(points: list[tuple[float, float]]) -> float:
    """Least-squares slope (°C per day) for a list of (julianday, temperature) pairs."""
    n = len(points)
    if n < 2:
        return 0.0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    xm = sum(xs) / n
    ym = sum(ys) / n
    num = sum((x - xm) * (y - ym) for x, y in zip(xs, ys))
    den = sum((x - xm) ** 2 for x in xs)
    return round(num / den, 3) if den > 1e-10 else 0.0


def analyze(city: str | None, days: int, question: str | None) -> dict:
    conn = _connect()
    since = _window_start(days)
    city_params: list[object] = [since]
    city_filter = ""
    if city:
        city_filter = " AND city = ?"
        city_params.append(city)

    readings_count = conn.execute(
        f"SELECT COUNT(*) FROM readings WHERE timestamp >= ?{city_filter}", city_params
    ).fetchone()[0]

    events_count = conn.execute(
        f"SELECT COUNT(*) FROM events WHERE timestamp >= ?{city_filter}", city_params
    ).fetchone()[0]

    by_type = conn.execute(
        "SELECT type, COUNT(*) AS n FROM events WHERE timestamp >= ? GROUP BY type ORDER BY n DESC",
        (since,),
    ).fetchall()

    hottest = conn.execute(
        "SELECT city, apparent_temperature, timestamp FROM readings WHERE timestamp >= ? ORDER BY apparent_temperature DESC LIMIT 1",
        (since,),
    ).fetchone()

    coldest = conn.execute(
        "SELECT city, apparent_temperature, timestamp FROM readings WHERE timestamp >= ? ORDER BY apparent_temperature ASC LIMIT 1",
        (since,),
    ).fetchone()

    windiest = conn.execute(
        "SELECT city, wind_speed, timestamp FROM readings WHERE timestamp >= ? ORDER BY wind_speed DESC LIMIT 1",
        (since,),
    ).fetchone()

    worst_event = conn.execute(
        "SELECT id, type, city, description, severity, timestamp FROM events WHERE timestamp >= ? ORDER BY severity DESC LIMIT 1",
        (since,),
    ).fetchone()

    # Per-city average temperature and event count
    per_city = conn.execute(
        """
        SELECT city,
               ROUND(AVG(apparent_temperature), 2) AS avg_apparent_temp,
               ROUND(AVG(wind_speed), 2) AS avg_wind_speed,
               COUNT(*) AS reading_count
        FROM readings
        WHERE timestamp >= ?
        GROUP BY city
        """,
        (since,),
    ).fetchall()

    # Temperature trend: least-squares slope (°C/day) per city
    trend_rows = conn.execute(
        "SELECT city, julianday(timestamp) AS jd, apparent_temperature FROM readings WHERE timestamp >= ? ORDER BY city, timestamp",
        (since,),
    ).fetchall()
    from collections import defaultdict
    by_city: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in trend_rows:
        by_city[row["city"]].append((row["jd"], row["apparent_temperature"]))
    trends = {c: _compute_slope(pts) for c, pts in by_city.items()}

    # Events per city breakdown
    events_by_city = conn.execute(
        "SELECT city, COUNT(*) AS n FROM events WHERE timestamp >= ? GROUP BY city ORDER BY n DESC",
        (since,),
    ).fetchall()

    result: dict = {
        "window_days": days,
        "city_filter": city,
        "readings_count": readings_count,
        "events_count": events_count,
        "events_by_type": {row["type"]: row["n"] for row in by_type},
        "events_by_city": {row["city"]: row["n"] for row in events_by_city},
        "hottest_reading": dict(hottest) if hottest else None,
        "coldest_reading": dict(coldest) if coldest else None,
        "windiest_reading": dict(windiest) if windiest else None,
        "worst_event": dict(worst_event) if worst_event else None,
        "per_city_stats": [dict(r) for r in per_city],
        "temperature_trend_c_per_day": trends,
    }

    if question:
        q = question.lower()
        if "hottest" in q:
            result["answer"] = result["hottest_reading"]
        elif "coldest" in q:
            result["answer"] = result["coldest_reading"]
        elif "windiest" in q or "windiest" in q:
            result["answer"] = result["windiest_reading"]
        elif "most events" in q or "most event" in q:
            top = conn.execute(
                "SELECT city, COUNT(*) AS n FROM events WHERE timestamp >= ? AND city IS NOT NULL GROUP BY city ORDER BY n DESC LIMIT 1",
                (since,),
            ).fetchone()
            result["answer"] = dict(top) if top else None
        elif "worst event" in q or "most severe" in q:
            result["answer"] = result["worst_event"]
        elif "trend" in q or "warming" in q or "cooling" in q:
            result["answer"] = {
                "temperature_trend_c_per_day": trends,
                "note": "Positive = warming, negative = cooling over the window",
            }
        else:
            result["answer"] = (
                "Supported questions: 'hottest reading', 'coldest reading', 'windiest reading', "
                "'city with most events', 'worst event', 'temperature trend'."
            )

    conn.close()
    return result


def _markdown(payload: dict) -> None:
    print(f"# WatchAgent Analysis ({payload['window_days']}d)")
    if payload["city_filter"]:
        print(f"City filter: {payload['city_filter']}")
    print(f"\n## Summary")
    print(f"- Readings: {payload['readings_count']}")
    print(f"- Events: {payload['events_count']}")
    print(f"\n## Events by type")
    for t, n in payload["events_by_type"].items():
        print(f"- {t}: {n}")
    print(f"\n## Events by city")
    for c, n in payload["events_by_city"].items():
        print(f"- {c}: {n}")
    print(f"\n## Extremes")
    if payload["hottest_reading"]:
        h = payload["hottest_reading"]
        print(f"- Hottest: {h['apparent_temperature']}°C in {h['city']} at {h['timestamp']}")
    if payload["coldest_reading"]:
        c = payload["coldest_reading"]
        print(f"- Coldest: {c['apparent_temperature']}°C in {c['city']} at {c['timestamp']}")
    if payload["windiest_reading"]:
        w = payload["windiest_reading"]
        print(f"- Windiest: {w['wind_speed']} km/h in {w['city']} at {w['timestamp']}")
    if payload["worst_event"]:
        e = payload["worst_event"]
        print(f"- Most severe event: [{e['type']}] severity={e['severity']} in {e['city']} — {e['description']}")
    print(f"\n## Per-city statistics")
    for row in payload["per_city_stats"]:
        print(f"- {row['city']}: avg apparent temp {row['avg_apparent_temp']}°C, avg wind {row['avg_wind_speed']} km/h ({row['reading_count']} readings)")
    print(f"\n## Temperature trend (°C/day, positive = warming)")
    for city, slope in payload["temperature_trend_c_per_day"].items():
        arrow = "↑" if slope > 0.1 else ("↓" if slope < -0.1 else "→")
        print(f"- {city}: {slope:+.3f} {arrow}")
    if payload.get("answer"):
        print(f"\n## Answer")
        print(json.dumps(payload["answer"], indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze WatchAgent stored weather data")
    parser.add_argument("--city", help="Filter by city")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--question", help="Natural-language question about the data")
    args = parser.parse_args()

    payload = analyze(args.city, args.days, args.question)
    if args.format == "json":
        print(json.dumps(payload, indent=2, default=str))
    else:
        _markdown(payload)


if __name__ == "__main__":
    main()
