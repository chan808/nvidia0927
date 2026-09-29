"""Register daily's local request log, embedded build and source-derived contract."""
import argparse
import json
from pathlib import Path
import sys

from tracebridge.daily_observation import configure_daily
from tracebridge.project_registry import registry_directory


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--project", default="daily-local")
    parser.add_argument("--registry", type=Path, default=registry_directory())
    parser.add_argument("--artifacts", type=Path, default=Path("output/daily-observation/artifacts"))
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--backend-url", default="http://127.0.0.1:8081")
    parser.add_argument("--frontend-url", default="http://127.0.0.1:3100")
    args = parser.parse_args()
    try:
        result = configure_daily(args.root, registry=args.registry, artifacts=args.artifacts,
            observation=args.observations or args.root / "backend/app/logs/tracebridge", project_id=args.project,
            backend_url=args.backend_url, frontend_url=args.frontend_url)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
