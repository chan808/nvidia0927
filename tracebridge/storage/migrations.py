"""Alembic-managed central schema, native pgvector and durable PostgreSQL audit."""
from pathlib import Path

from .schema import metadata


def install_schema(connection):
    connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS vector")
    connection.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    metadata.create_all(connection)
    statements = [
        "ALTER TABLE tracebridge.card_embeddings ADD COLUMN IF NOT EXISTS embedding vector",
        "ALTER TABLE tracebridge.card_embeddings ADD COLUMN IF NOT EXISTS dimensions integer",
        "CREATE INDEX IF NOT EXISTS jobs_dispatch ON tracebridge.jobs(runner_id,state,priority,created)",
        "CREATE INDEX IF NOT EXISTS jobs_project_created ON tracebridge.jobs(project_id,created,id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS runner_one_active ON tracebridge.jobs(runner_id) WHERE runner_id IS NOT NULL AND state IN ('RUNNING','CANCEL_REQUESTED','RECOVERY_REQUIRED')",
        "CREATE INDEX IF NOT EXISTS events_job ON tracebridge.job_events(job_id,id)",
        "CREATE INDEX IF NOT EXISTS cards_scope ON tracebridge.cards(project_id,review_status)",
        "CREATE INDEX IF NOT EXISTS search_fts ON tracebridge.card_search USING gin(to_tsvector('simple',search_text))",
        "CREATE INDEX IF NOT EXISTS search_trigram ON tracebridge.card_search USING gin(search_text gin_trgm_ops)",
    ]
    for statement in statements:
        connection.exec_driver_sql(statement)
    connection.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION tracebridge.audit_job() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='INSERT' THEN
                INSERT INTO tracebridge.job_events(job_id,kind,to_state,epoch,occurred,metadata_json)
                VALUES(NEW.id,'SUBMITTED',NEW.state,NEW.epoch,extract(epoch from clock_timestamp()),
                    json_build_object('actor','authenticated-owner','assessment',NEW.assessment_json::json)::text);
            ELSE
                IF OLD.state<>NEW.state THEN
                    INSERT INTO tracebridge.job_events(job_id,kind,from_state,to_state,epoch,occurred,metadata_json)
                    VALUES(NEW.id,'STATE_CHANGED',OLD.state,NEW.state,NEW.epoch,extract(epoch from clock_timestamp()),
                        json_build_object('actor','control-plane','runner_id',NEW.runner_id)::text);
                END IF;
                IF OLD.assessment_revision<>NEW.assessment_revision THEN
                    INSERT INTO tracebridge.job_events(job_id,kind,from_state,to_state,epoch,occurred,metadata_json)
                    VALUES(NEW.id,'ASSESSMENT_CHANGED',OLD.state,NEW.state,NEW.epoch,extract(epoch from clock_timestamp()),
                        json_build_object('actor','authenticated-owner','revision',NEW.assessment_revision,
                            'before',OLD.assessment_json::json,'after',NEW.assessment_json::json)::text);
                END IF;
            END IF;
            RETURN NEW;
        END $$
    """)
    connection.exec_driver_sql("CREATE TRIGGER job_audit AFTER INSERT OR UPDATE ON tracebridge.jobs FOR EACH ROW EXECUTE FUNCTION tracebridge.audit_job()")
    connection.exec_driver_sql("""
        CREATE OR REPLACE FUNCTION tracebridge.audit_model() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP='INSERT' OR OLD.state<>NEW.state THEN
                INSERT INTO tracebridge.job_events(job_id,kind,epoch,occurred,metadata_json)
                SELECT NEW.job_id,'MODEL_STEP',epoch,extract(epoch from clock_timestamp()),
                    json_build_object('step_id',NEW.step_id,'state',NEW.state)::text
                    FROM tracebridge.jobs WHERE id=NEW.job_id;
            END IF;
            RETURN NEW;
        END $$
    """)
    connection.exec_driver_sql("CREATE TRIGGER model_audit AFTER INSERT OR UPDATE ON tracebridge.model_steps FOR EACH ROW EXECUTE FUNCTION tracebridge.audit_model()")


def upgrade(engine):
    from alembic import command
    from alembic.config import Config
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).with_name("alembic")))
    with engine.begin() as connection:
        connection.exec_driver_sql("SELECT pg_advisory_xact_lock(78301942)")
        connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS tracebridge")
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
