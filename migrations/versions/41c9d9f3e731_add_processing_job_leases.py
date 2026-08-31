"""add processing job leases

Revision ID: 41c9d9f3e731
Revises: ca82b6fbd136
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "41c9d9f3e731"
down_revision: str | None = "ca82b6fbd136"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "processing_jobs", sa.Column("claimed_by", sa.String(length=255), nullable=True)
    )
    op.add_column(
        "processing_jobs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f("ix_processing_jobs_claimed_by"),
        "processing_jobs",
        ["claimed_by"],
        unique=False,
    )
    op.create_index(
        op.f("ix_processing_jobs_lease_expires_at"),
        "processing_jobs",
        ["lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_processing_jobs_lease_expires_at"), table_name="processing_jobs"
    )
    op.drop_index(op.f("ix_processing_jobs_claimed_by"), table_name="processing_jobs")
    op.drop_column("processing_jobs", "lease_expires_at")
    op.drop_column("processing_jobs", "claimed_by")
