"""Process owner-requested reviewed-card indexing outside HTTP transactions."""
import argparse
import json
import os
import time
from pathlib import Path
from dotenv import load_dotenv
from tracebridge.semantic_memory import embedding_client_from_env
from tracebridge.storage import open_control_store, configured_target
from tracebridge.storage.rag_jobs import run_index_once

def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    target = configured_target()
    if not target:
        parser.error("Configure TRACEBRIDGE_DATABASE_URL or TRACEBRIDGE_CONTROL_DB")
    try:
        provider = embedding_client_from_env()
        if provider is None:
            parser.error("Explicitly enable and configure the embedding service")
        storage = open_control_store(target)
        try:
            if not storage.postgres:
                parser.error("The durable memory worker requires PostgreSQL; use scripts/incident_memory.py for SQLite indexing")
            while True:
                result = run_index_once(storage, provider)
                if result:
                    print(json.dumps(result, ensure_ascii=False), flush=True)
                if args.once:
                    break
                time.sleep(2 if result else 5)
        finally:
            storage.close()
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        parser.error(type(exc).__name__)

if __name__ == "__main__":
    main()
