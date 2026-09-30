"""Durable public intake, owner policies and shared request budgets."""
from alembic import op

revision = "20260929_04"
down_revision = "20260929_03"
branch_labels = None
depends_on = None


def upgrade():
    for ddl in (
        "CREATE UNIQUE INDEX IF NOT EXISTS jobs_public_scope_key ON tracebridge.jobs(project_id,id)",
        "CREATE TABLE IF NOT EXISTS tracebridge.service_policies (project_id text PRIMARY KEY REFERENCES tracebridge.bindings(project_id), policy_json text NOT NULL, revision integer NOT NULL CHECK(revision >= 1), updated double precision NOT NULL)",
        "CREATE TABLE IF NOT EXISTS tracebridge.public_reports (id text PRIMARY KEY, project_id text NOT NULL REFERENCES tracebridge.bindings(project_id), access_digest text NOT NULL, submission_digest text NOT NULL, input_hash text NOT NULL, dedupe_hash text NOT NULL, service text NOT NULL, root_job_id text NOT NULL, latest_job_id text NOT NULL, created double precision NOT NULL, expires double precision NOT NULL, answers integer NOT NULL DEFAULT 0, UNIQUE(project_id,submission_digest), FOREIGN KEY(project_id,root_job_id) REFERENCES tracebridge.jobs(project_id,id), FOREIGN KEY(project_id,latest_job_id) REFERENCES tracebridge.jobs(project_id,id))",
        "CREATE INDEX IF NOT EXISTS public_report_dedupe ON tracebridge.public_reports(project_id,dedupe_hash,created)",
        "CREATE TABLE IF NOT EXISTS tracebridge.public_limits (key text PRIMARY KEY, count integer NOT NULL, expires double precision NOT NULL)",
    ):
        op.get_bind().exec_driver_sql(ddl)


def downgrade():
    raise RuntimeError("Export and verify public receipts before switching backends")
