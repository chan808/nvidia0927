"""Storage-neutral scalar records; original JSON and opaque digests are preserved."""
from sqlalchemy import Column, Float, Integer, MetaData, Table, Text, UniqueConstraint, ForeignKey, CheckConstraint
from sqlalchemy.types import UserDefinedType

class NativeVector(UserDefinedType):
    cache_ok = True
    def get_col_spec(self, **kwargs):
        return "vector"
    def bind_processor(self, dialect):
        # Loaded only for native PostgreSQL vector statements, keeping the local
        # SQLite install independent of the optional pgvector Python package.
        from pgvector.sqlalchemy import VECTOR
        return VECTOR().bind_processor(dialect)

metadata = MetaData()

def table(name, *columns, **kwargs):
    return Table(name, metadata, *columns, **kwargs)

def text(name, *constraints, **kwargs):
    return Column(name, Text, *constraints, **kwargs)

pairings = table("pairings", text("digest", primary_key=True), text("projects", nullable=False), Column("expires", Float, nullable=False))
runners = table("runners", text("id", primary_key=True), text("digest", nullable=False, unique=True), text("projects", nullable=False),
    Column("expires", Float, nullable=False), Column("revoked", Integer, nullable=False, default=0), Column("last_seen", Float, nullable=False))
bindings = table("bindings", text("project_id", primary_key=True), text("runner_id", ForeignKey("runners.id"), nullable=False), text("manifest", nullable=False))
jobs = table("jobs", text("id", primary_key=True), text("project_id", nullable=False), text("incident_id", nullable=False), text("run_id", nullable=False),
    text("kind", nullable=False), text("body", nullable=False), text("input_hash", nullable=False), text("idempotency_key", nullable=False),
    text("state", nullable=False), text("runner_id", ForeignKey("runners.id")), Column("epoch", Integer, nullable=False, default=0), Column("lease_until", Float, nullable=False, default=0),
    Column("expires", Float, nullable=False), text("result"), text("result_hash"), Column("created", Float, nullable=False),
    Column("priority", Integer, nullable=False, default=2), text("assessment_json", nullable=False, default="{}"),
    Column("assessment_revision", Integer, nullable=False, default=1), UniqueConstraint("project_id", "idempotency_key"),
    CheckConstraint("priority BETWEEN 0 AND 3"), CheckConstraint("epoch >= 0"), CheckConstraint("assessment_revision >= 1"))
model_steps = table("model_steps", text("job_id", ForeignKey("jobs.id"), primary_key=True), text("step_id", primary_key=True), text("input_hash", nullable=False), text("state", nullable=False), text("response"))
job_events = table("job_events", Column("id", Integer, primary_key=True, autoincrement=True), text("job_id", ForeignKey("jobs.id"), nullable=False), text("kind", nullable=False),
    text("from_state"), text("to_state"), Column("epoch", Integer, nullable=False), Column("occurred", Float, nullable=False), text("metadata_json", nullable=False))

incidents = table("incidents", text("project_id", primary_key=True), text("incident_id", primary_key=True), text("created_at", nullable=False),
    text("updated_at", nullable=False), text("latest_run_id", nullable=False), Column("latest_revision", Integer, nullable=False))
runs = table("runs", text("run_id", primary_key=True), text("project_id", nullable=False), text("incident_id", nullable=False), Column("revision", Integer, nullable=False),
    text("executed_at", nullable=False), text("route", nullable=False), text("run_status", nullable=False), text("content_hash", nullable=False), text("record_json", nullable=False),
    UniqueConstraint("project_id", "incident_id", "revision"))
cards = table("cards", text("card_id", primary_key=True), text("project_id", nullable=False), text("incident_id", nullable=False), text("run_id", ForeignKey("runs.run_id"), nullable=False, unique=True),
    text("review_status", nullable=False), text("signals_json", nullable=False), text("card_json", nullable=False))
change_jobs = table("change_jobs", text("work_id", primary_key=True), text("project_id", nullable=False), text("incident_id", nullable=False), text("source_run_id", ForeignKey("runs.run_id"), nullable=False, unique=True),
    text("result_run_id", ForeignKey("runs.run_id"), nullable=False, unique=True), text("content_hash", nullable=False), text("record_json", nullable=False))
card_search = table("card_search", text("card_id", ForeignKey("cards.card_id"), primary_key=True), text("search_text", nullable=False))
project_applications = table("project_applications", text("work_id", primary_key=True), text("project_id", nullable=False), text("record_json", nullable=False))

# Dimensions and native vector columns are installed by the PostgreSQL migration.
card_embeddings = table("card_embeddings", text("project_id", primary_key=True), text("card_id", ForeignKey("cards.card_id"), primary_key=True), text("model_key", primary_key=True),
    text("content_sha256", nullable=False), text("vector_json", nullable=False), Column("indexed_at", Float, nullable=False),
    Column("embedding", NativeVector), Column("dimensions", Integer))

index_jobs = table("index_jobs", text("id", primary_key=True), text("project_id", nullable=False), text("model_key", nullable=False), text("state", nullable=False),
    Column("limit_count", Integer, nullable=False), Column("created", Float, nullable=False), Column("started", Float), text("result_json"), text("error_type"),
    text("lease_token"), Column("lease_until", Float, nullable=False, default=0))
storage_settings = table("storage_settings", text("key", primary_key=True), text("value", nullable=False))
query_embeddings = table("query_embeddings", text("job_id", ForeignKey("jobs.id"), primary_key=True), text("query_hash", primary_key=True),
    text("model_key", primary_key=True), text("state", nullable=False), text("vector_json"), text("error_type"), Column("created", Float, nullable=False))
