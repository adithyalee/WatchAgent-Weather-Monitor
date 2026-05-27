"""Add severity column to events table.

Revision ID: 002
Revises: 001
Create Date: 2026-05-27
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default="0.0" satisfies the NOT NULL constraint for existing rows
    op.add_column(
        "events",
        sa.Column("severity", sa.Float(), nullable=False, server_default="0.0"),
    )


def downgrade() -> None:
    op.drop_column("events", "severity")
