"""Initial schema for readings and events.

Revision ID: 001
Revises:
Create Date: 2026-05-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "readings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("city", sa.String(length=64), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=False),
        sa.Column("apparent_temperature", sa.Float(), nullable=False),
        sa.Column("precipitation", sa.Float(), nullable=False),
        sa.Column("wind_speed", sa.Float(), nullable=False),
        sa.Column("weather_code", sa.Integer(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("city", "timestamp", name="uq_reading_city_timestamp"),
    )
    op.create_index("ix_readings_city", "readings", ["city"])

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("city", sa.String(length=64), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("description", sa.String(length=512), nullable=False),
        sa.Column("reading_ids", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_city", "events", ["city"])
    op.create_index("ix_events_type", "events", ["type"])


def downgrade() -> None:
    op.drop_index("ix_events_type", table_name="events")
    op.drop_index("ix_events_city", table_name="events")
    op.drop_table("events")
    op.drop_index("ix_readings_city", table_name="readings")
    op.drop_table("readings")
