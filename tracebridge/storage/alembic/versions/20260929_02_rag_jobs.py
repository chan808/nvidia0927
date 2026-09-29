"""Durable RAG work and cached query vectors; no cached card conclusions."""
from alembic import op
from tracebridge.storage import schema as s

revision = "20260929_02"
down_revision = "20260929_01"
branch_labels = None
depends_on = None

def upgrade():
    connection = op.get_bind()
    for table in (s.index_jobs, s.storage_settings, s.query_embeddings):
        table.create(connection, checkfirst=True)
    connection.exec_driver_sql("""CREATE FUNCTION tracebridge.audit_embedding() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='INSERT' OR OLD.state<>NEW.state THEN
                INSERT INTO tracebridge.job_events(job_id,kind,epoch,occurred,metadata_json)
                SELECT NEW.job_id,'QUERY_EMBEDDING',epoch,extract(epoch from clock_timestamp()),
                    json_build_object('query_hash',NEW.query_hash,'state',NEW.state)::text
                    FROM tracebridge.jobs WHERE id=NEW.job_id;
            END IF;
            RETURN NEW;
        END $$""")
    connection.exec_driver_sql("CREATE TRIGGER embedding_audit AFTER INSERT OR UPDATE ON tracebridge.query_embeddings FOR EACH ROW EXECUTE FUNCTION tracebridge.audit_embedding()")

def downgrade():
    raise RuntimeError("Export and verify records before switching backends")
