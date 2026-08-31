"""Notify workers about new pending jobs.

Revision ID: a7c31435e842
Revises: 41c9d9f3e731
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a7c31435e842"
down_revision: str | None = "41c9d9f3e731"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION notify_streamforge_new_job()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            PERFORM pg_notify('streamforge_new_jobs', NEW.id::text);
            RETURN NEW;
        END;
        $$
        """)
    op.execute("""
        CREATE TRIGGER processing_jobs_notify_pending_insert
        AFTER INSERT ON processing_jobs
        FOR EACH ROW
        WHEN (NEW.status = 'PENDING')
        EXECUTE FUNCTION notify_streamforge_new_job()
        """)


def downgrade() -> None:
    op.execute("DROP TRIGGER processing_jobs_notify_pending_insert ON processing_jobs")
    op.execute("DROP FUNCTION notify_streamforge_new_job()")
