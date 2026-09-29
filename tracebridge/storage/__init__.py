"""Central storage ports. Paired PCs keep their independent SQLite journals."""
from .control import open_control_store

def configured_target(default=None):
    """Read private connection config without exposing it in command arguments."""
    import os
    from pathlib import Path
    if os.getenv("TRACEBRIDGE_DATABASE_URL"):
        return os.environ["TRACEBRIDGE_DATABASE_URL"]
    if os.getenv("TRACEBRIDGE_DATABASE_URL_FILE"):
        return Path(os.environ["TRACEBRIDGE_DATABASE_URL_FILE"]).read_text(encoding="utf-8").strip()
    return os.getenv("TRACEBRIDGE_CONTROL_DB") or default

__all__ = ["open_control_store"]
