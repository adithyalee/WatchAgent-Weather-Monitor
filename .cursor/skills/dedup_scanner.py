#!/usr/bin/env python3
"""Scan stored readings for deduplication anomalies and data-quality issues."""

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
    raise SystemExit(f"Unsupported DATABASE_URL for scanner: {url}")


def _connect() -> sqlite3.Connection:
    path = _db_path()
    if not path.exists():
        raise SystemExit(f"Database not found at {path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def scan(days: int) -> dict:
    conn = _connect()
    since = (datetime.now(UTC) - timedelta(days=days)).isoformat()

    # 1. Multiple readings per city per hour = dedup constraint failure
    hourly_dupes = conn.execute(
        """
        SELECT city,
               strftime('%Y-%m-%dT%H', timestamp) AS hour,
               COUNT(*) AS reading_count
        FROM readings
        WHERE timestamp >= ?
        GROUP BY city, hour
        HAVING reading_count > 1
        ORDER BY reading_count DESC
        """,
        (since,),
    ).fetchall()

    # 2. Large fetch lag: fetched_at > timestamp + 2h (clock drift or backfill)
    late_fetches = conn.execute(
        """
        SELECT id, city, timestamp, fetched_at,
               ROUND((julianday(fetched_at) - julianday(timestamp)) * 24, 2) AS lag_hours
        FROM readings
        WHERE timestamp >= ?
          AND (julianday(fetched_at) - julianday(timestamp)) * 24 > 2
        ORDER BY lag_hours DESC
        LIMIT 20
        """,
        (since,),
    ).fetchall()

    # 3. Per-city summary
    per_city = conn.execute(
        """
        SELECT city, COUNT(*) AS count,
               MIN(timestamp) AS earliest,
               MAX(timestamp) AS latest
        FROM readings
        WHERE timestamp >= ?
        GROUP BY city
        """,
        (since,),
    ).fetchall()

    # 4. Largest gap between consecutive readings per city
    gaps: list[dict] = []
    for city_row in per_city:
        city = city_row["city"]
        rows = conn.execute(
            "SELECT timestamp FROM readings WHERE city = ? AND timestamp >= ? ORDER BY timestamp",
            (city, since),
        ).fetchall()
        if len(rows) >= 2:
            max_gap = 0.0
            for i in range(1, len(rows)):
                gap = conn.execute(
                    "SELECT (julianday(?) - julianday(?)) * 24",
                    (rows[i]["timestamp"], rows[i - 1]["timestamp"]),
                ).fetchone()[0]
                if gap > max_gap:
                    max_gap = gap
            gaps.append({"city": city, "max_gap_hours": round(max_gap, 2)})

    # 5. Events referencing non-existent reading IDs
    orphaned = conn.execute(
        """
        SELECT e.id AS event_id, e.type, e.reading_ids
        FROM events e
        WHERE e.reading_ids IS NOT NULL
          AND e.timestamp >= ?
        """,
        (since,),
    ).fetchall()
    orphan_count = 0
    for row in orphaned:
        if row["reading_ids"]:
            try:
                ids = json.loads(row["reading_ids"])
                for rid in ids:
                    exists = conn.execute(
                        "SELECT 1 FROM readings WHERE id = ?", (rid,)
                    ).fetchone()
                    if exists is None:
                        orphan_count += 1
            except (json.JSONDecodeError, TypeError):
                pass

    conn.close()

    return {
        "window_days": days,
        "anomalies_found": len(hourly_dupes) + len(late_fetches) + orphan_count,
        "hourly_duplicates": [dict(r) for r in hourly_dupes],
        "late_fetches": [dict(r) for r in late_fetches],
        "per_city_summary": [dict(r) for r in per_city],
        "max_consecutive_gaps": gaps,
        "orphaned_reading_references": orphan_count,
    }


def _markdown(payload: dict) -> None:
    print(f"# Deduplication Scan ({payload['window_days']}d)")
    print(f"- **Anomalies found:** {payload['anomalies_found']}")
    print(f"- **Orphaned reading refs:** {payload['orphaned_reading_references']}")
    print()
    print("## Per-city reading summary")
    for city in payload["per_city_summary"]:
        print(f"- {city['city']}: {city['count']} readings  ({city['earliest']} → {city['latest']})")
    print()
    print("## Max gap between consecutive readings")
    for g in payload["max_consecutive_gaps"]:
        flag = "⚠️ " if g["max_gap_hours"] > 3 else "✓ "
        print(f"- {flag}{g['city']}: {g['max_gap_hours']}h")
    if payload["hourly_duplicates"]:
        print()
        print("## Hourly duplicates (deduplication failures)")
        for r in payload["hourly_duplicates"]:
            print(f"- {r['city']} at {r['hour']}: {r['reading_count']} readings stored")
    if payload["late_fetches"]:
        print()
        print("## Suspicious fetch latency (>2h behind reading timestamp)")
        for r in payload["late_fetches"]:
            print(f"- id={r['id']} {r['city']} {r['timestamp']}: {r['lag_hours']}h lag")


def main() -> None:
    parser = argparse.ArgumentParser(description="Scan for deduplication and data-quality anomalies")
    parser.add_argument("--days", type=int, default=7, help="Lookback window in days")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    args = parser.parse_args()

    payload = scan(args.days)
    if args.format == "json":
        print(json.dumps(payload, indent=2, default=str))
    else:
        _markdown(payload)


if __name__ == "__main__":
    main()
