"""Enforce incident and project ownership for every derived record."""
from alembic import op

revision = "20260929_03"
down_revision = "20260929_02"
branch_labels = None
depends_on = None


def upgrade():
    # Explicit DDL keeps this revision independent of future Python metadata.
    for statement in (
        "ALTER TABLE tracebridge.runs ADD CONSTRAINT runs_scope_key UNIQUE(project_id,incident_id,run_id)",
        "ALTER TABLE tracebridge.cards ADD CONSTRAINT cards_project_key UNIQUE(project_id,card_id)",
        "ALTER TABLE tracebridge.change_jobs ADD CONSTRAINT changes_project_key UNIQUE(project_id,work_id)",
        "ALTER TABLE tracebridge.runs ADD CONSTRAINT runs_incident_fk FOREIGN KEY(project_id,incident_id) REFERENCES tracebridge.incidents(project_id,incident_id)",
        "ALTER TABLE tracebridge.cards ADD CONSTRAINT cards_run_scope_fk FOREIGN KEY(project_id,incident_id,run_id) REFERENCES tracebridge.runs(project_id,incident_id,run_id)",
        "ALTER TABLE tracebridge.change_jobs ADD CONSTRAINT changes_source_scope_fk FOREIGN KEY(project_id,incident_id,source_run_id) REFERENCES tracebridge.runs(project_id,incident_id,run_id)",
        "ALTER TABLE tracebridge.change_jobs ADD CONSTRAINT changes_result_scope_fk FOREIGN KEY(project_id,incident_id,result_run_id) REFERENCES tracebridge.runs(project_id,incident_id,run_id)",
        "ALTER TABLE tracebridge.card_embeddings ADD CONSTRAINT embeddings_card_scope_fk FOREIGN KEY(project_id,card_id) REFERENCES tracebridge.cards(project_id,card_id)",
        "ALTER TABLE tracebridge.project_applications ADD CONSTRAINT applications_change_scope_fk FOREIGN KEY(project_id,work_id) REFERENCES tracebridge.change_jobs(project_id,work_id)",
        # Incident upsert precedes its new run in the same transaction. Validate
        # the circular latest-run reference only when the transaction commits.
        "ALTER TABLE tracebridge.incidents ADD CONSTRAINT incidents_latest_scope_fk FOREIGN KEY(project_id,incident_id,latest_run_id) REFERENCES tracebridge.runs(project_id,incident_id,run_id) DEFERRABLE INITIALLY DEFERRED",
    ):
        op.get_bind().exec_driver_sql(statement)


def downgrade():
    raise RuntimeError("Export and verify records before switching backends")
