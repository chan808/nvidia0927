"""Central scalar records and pgvector; retained JSON/digests are immutable."""
from alembic import op

revision = "20260929_01"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    from tracebridge.storage.migrations import install_schema
    install_schema(op.get_bind())

def downgrade():
    raise RuntimeError("Export and verify all records before switching backends; destructive downgrade is disabled")
