"""Use the caller-owned connection; never print or embed database credentials."""
from alembic import context
from tracebridge.storage.schema import metadata

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Use python scripts/storage_admin.py init with a configured database")
context.configure(connection=connection, target_metadata=metadata, version_table_schema="tracebridge")
with context.begin_transaction():
    context.run_migrations()
