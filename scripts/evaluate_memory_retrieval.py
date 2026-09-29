"""Compare retrieval on owner-labeled cases; never infer diagnosis quality from hits."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from dotenv import load_dotenv

from tracebridge.incident_memory import search_memory
from tracebridge.semantic_memory import embedding_client_from_env
from tracebridge.storage import configured_target, open_control_store


def evaluate_cases(cases: list[dict], db_path: Path, *, embedding_client=None) -> dict:
    if not isinstance(cases, list) or not 1 <= len(cases) <= 200:
        raise ValueError("Evaluation requires 1 to 200 labeled cases")
    # Validate the whole suite before any external transmission.
    for case in cases:
        if (not isinstance(case, dict) or set(case) - {"project_id", "query", "expected_card_ids", "scope_filters", "exclude_incident_id"}
                or not isinstance(case.get("project_id"), str) or not case["project_id"].strip()
                or not isinstance(case.get("query"), str) or not 1 <= len(case["query"]) <= 12000
                or not isinstance(case.get("expected_card_ids"), list) or len(case["expected_card_ids"]) > 50
                or any(not isinstance(value, str) or not value for value in case["expected_card_ids"])
                or not isinstance(case.get("scope_filters", {}), dict)
                or set(case.get("scope_filters", {})) - {"service", "environment"}
                or any(not isinstance(value, str) or not 1 <= len(value) <= 80 for value in case.get("scope_filters", {}).values())):
            raise ValueError("Every case needs a query, project, labeled card IDs and supported scope")
    observations = []
    for index, case in enumerate(cases):
        result = search_memory(case["project_id"], case["query"], db_path=db_path,
            scope_filters=case.get("scope_filters"), exclude_incident_id=case.get("exclude_incident_id"),
            embedding_client=embedding_client)
        retrieved = [card["card_id"] for card in result["cards"]]
        expected = set(case["expected_card_ids"])
        rank = next((i for i, value in enumerate(retrieved, start=1) if value in expected), None)
        observations.append({"case": index + 1, "project_id": case["project_id"],
            "query_sha256": hashlib.sha256(case["query"].encode()).hexdigest(), "status": result["status"],
            "strategy": result["strategy"], "retrieved_card_ids": retrieved, "expected_card_ids": sorted(expected),
            "recall_at_2": len(expected.intersection(retrieved)) / len(expected) if expected else None,
            "reciprocal_rank_at_2": 1 / rank if rank else 0,
            "unexpected_hit": bool(retrieved) if not expected else None,
            "elapsed_ms": result["elapsed_ms"], "semantic": result.get("semantic", {"status": "DISABLED"})})
    positives = [item for item in observations if item["expected_card_ids"]]
    negatives = [item for item in observations if not item["expected_card_ids"]]
    failures = sum(item["status"] != "OK" for item in observations)
    semantic_failures = sum(item["semantic"]["status"] == "FAILED" for item in observations)
    latencies = sorted(item["elapsed_ms"] for item in observations)
    return {"executed_at": datetime.now(timezone.utc).isoformat(),
        "mode": "HYBRID" if embedding_client is not None else "LEXICAL", "case_count": len(observations),
        "failed_cases": failures, "semantic_failures": semantic_failures,
        "mean_recall_at_2": sum(item["recall_at_2"] for item in positives) / len(positives) if positives else None,
        "mrr_at_2": sum(item["reciprocal_rank_at_2"] for item in positives) / len(positives) if positives else None,
        "no_relevant_case_false_hit_rate": sum(item["unexpected_hit"] for item in negatives) / len(negatives) if negatives else None,
        "latency_p95_ms": latencies[min(len(latencies) - 1, (95 * len(latencies) + 99) // 100 - 1)],
        "limitations": ["Only retrieval against supplied labels, not cause/fix accuracy or speed superiority.",
                        "Vector coverage and provider failures are reported per case; use the same DB and labels for both modes."],
        "cases": observations}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, required=True, help="JSON list of labeled query cases")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--db", type=Path)
    source.add_argument("--central", action="store_true", help="Use configured central PostgreSQL")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--semantic", action="store_true", help="Explicitly allow redacted queries to the configured embedding service")
    args = parser.parse_args()
    storage = None
    try:
        if args.output.resolve() in {args.suite.resolve(), args.db.resolve() if args.db else None}:
            raise ValueError("Evaluation output must preserve the input DB and labeled suite")
        if (args.db is not None and not args.db.is_file()) or args.suite.stat().st_size > 1_000_000:
            raise ValueError("Existing incident DB and a suite up to 1 MB required")
        provider = embedding_client_from_env() if args.semantic else None
        if args.semantic and provider is None:
            parser.error("Configure and explicitly enable the embedding service first")
        target = args.db
        if args.central:
            connection = configured_target()
            if not connection:
                raise ValueError("Configure the central database URL first")
            storage = open_control_store(connection)
            if not storage.postgres:
                raise ValueError("Central evaluation requires PostgreSQL")
            target = storage.incident_target
        result = evaluate_cases(json.loads(args.suite.read_text(encoding="utf-8-sig")), target, embedding_client=provider)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except (OSError, ValueError, KeyError) as exc:
        parser.error(type(exc).__name__)
    finally:
        if storage is not None:
            storage.close()
    print(json.dumps({key: value for key, value in result.items() if key != "cases"}, ensure_ascii=False, indent=2))
    if result["failed_cases"] or result["semantic_failures"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
