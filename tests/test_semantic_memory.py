"""Synthetic embedding doubles: scope, invalidation and failure contracts, no network."""
import json

import httpx
import pytest

from tracebridge.incident_memory import IncidentStore, recheck_memory, search_memory
from tracebridge.semantic_memory import NimEmbeddingClient, index_reviewed_cards, normalize_vector
from test_incident_memory import record as run_record


class EmbeddingsDouble:
    identity = "synthetic-multilingual-v1"

    def __init__(self, before_return=None):
        self.calls = []
        self.before_return = before_return

    def embed(self, texts, *, input_type):
        self.calls.append((input_type, list(texts)))
        if self.before_return:
            callback, self.before_return = self.before_return, None
            callback()
        return [[1.0, 0.0] if any(term in text for term in ("가입", "계정을", "account")) else [0.0, 1.0] for text in texts]


def saved(store, *, project="agolive", summary="회원가입이 실패했습니다.", service="api", environment="dev", review="approve"):
    run = run_record(project=project)
    run.update(summary=summary, scope={**run["scope"], "service": service, "environment": environment})
    store.save_run(run)
    if review:
        store.review_card(project, run["run_id"], review, reviewer="synthetic-owner")
    return run


def test_korean_prefix_and_exact_search_are_scoped_to_current_service_and_environment(tmp_path):
    with IncidentStore(tmp_path / "memory.sqlite3") as store:
        correct = saved(store, summary="가입이 실패했습니다.")
        saved(store, service="billing")
        saved(store, environment="prod")
        saved(store, project="foreign")
        result = store.search("agolive", "가입", scope_filters={"service": "api", "environment": "dev"})
        assert result["strategy"] == "FTS5" and [card["card_id"] for card in result["cards"]] == [correct["run_id"]]
        assert store.search("agolive", "/api/users", scope_filters={"service": "api", "environment": "dev"})["hit_count"] == 1
        with pytest.raises(ValueError):
            store.search("agolive", "가입", scope_filters={"service'); DROP TABLE cards; --": "api"})


def test_explicit_vector_index_finds_paraphrase_and_keeps_reviewed_project_scope(tmp_path):
    provider = EmbeddingsDouble()
    with IncidentStore(tmp_path / "memory.sqlite3") as store:
        correct = saved(store)
        saved(store, summary="승인 전 사건", review=None)
        saved(store, summary="반려된 사건", review="reject")
        saved(store, project="foreign")
        indexed = index_reviewed_cards(store, "agolive", provider)
        assert indexed["indexed"] == 1 and indexed["embedding_calls"] == 1
        assert index_reviewed_cards(store, "agolive", provider)["indexed"] == 0
        query = "계정을 만들 수 없어요"
        assert store.search("agolive", query)["hit_count"] == 0
        result = store.search("agolive", query, embedding_client=provider, scope_filters={"service": "api", "environment": "dev"})
        assert result["strategy"] == "VECTOR" and result["cards"][0]["card_id"] == correct["run_id"]
        assert provider.calls[0][0] == "passage" and provider.calls[-1][0] == "query"
        checked = recheck_memory(result, run_record())
        assert all(item["usable_as_current_evidence"] is False for item in checked["rechecks"])
        result = store.search("agolive", query, embedding_client=provider, exclude_incident_id=correct["incident_id"])
        assert result["hit_count"] == 0 and result["semantic"]["embedding_calls"] == 0


def test_vector_ranking_never_displaces_two_exact_fingerprints(tmp_path):
    provider = EmbeddingsDouble()
    with IncidentStore(tmp_path / "memory.sqlite3") as store:
        saved(store)
        saved(store)
        index_reviewed_cards(store, "agolive", provider)
        calls = len(provider.calls)
        result = store.search("agolive", "/api/users", embedding_client=provider)
        assert result["strategy"] == "EXACT" and result["hit_count"] == 2
        assert result["semantic"]["status"] == "SKIPPED_EXACT_MATCH" and len(provider.calls) == calls


def test_review_edit_and_rejection_immediately_invalidate_vectors(tmp_path):
    provider = EmbeddingsDouble()
    with IncidentStore(tmp_path / "memory.sqlite3") as store:
        run = saved(store)
        index_reviewed_cards(store, "agolive", provider)
        store.review_card("agolive", run["run_id"], "edit", reviewer="owner", changes={"finding": "결제 오류"})
        assert store.connection.execute("SELECT COUNT(*) FROM card_embeddings").fetchone()[0] == 0
        index_reviewed_cards(store, "agolive", provider)
        store.review_card("agolive", run["run_id"], "reject", reviewer="owner")
        assert store.search("agolive", "계정을 만들 수 없어요", embedding_client=provider)["cards"] == []


def test_edit_during_embedding_call_cannot_commit_a_stale_vector(tmp_path):
    path = tmp_path / "memory.sqlite3"
    with IncidentStore(path) as store:
        run = saved(store)
        def edit():
            with IncidentStore(path) as other:
                other.review_card("agolive", run["run_id"], "edit", reviewer="other-owner", changes={"finding": "새로운 판정"})
        provider = EmbeddingsDouble(before_return=edit)
        result = index_reviewed_cards(store, "agolive", provider)
        assert result["indexed"] == 0 and result["stale_skipped"] == 1
        assert store.connection.execute("SELECT COUNT(*) FROM card_embeddings").fetchone()[0] == 0


def test_embedding_failure_preserves_lexical_results(tmp_path):
    class Offline(EmbeddingsDouble):
        def embed(self, texts, *, input_type):
            raise TimeoutError("synthetic outage")
    with IncidentStore(tmp_path / "memory.sqlite3") as store:
        saved(store)
        index_reviewed_cards(store, "agolive", EmbeddingsDouble())
        result = store.search("agolive", "회원가입이", embedding_client=Offline())
        assert result["status"] == "OK" and result["hit_count"] == 1 and result["strategy"] == "FTS5"
        assert result["semantic"] == {"status": "FAILED", "error_type": "TimeoutError"}


def test_embedding_model_change_does_not_mix_vector_spaces(tmp_path):
    with IncidentStore(tmp_path / "memory.sqlite3") as store:
        saved(store)
        index_reviewed_cards(store, "agolive", EmbeddingsDouble())
        other = EmbeddingsDouble()
        other.identity = "different-model"
        result = store.search("agolive", "계정을 만들 수 없어요", embedding_client=other)
        assert result["semantic"]["status"] == "EMPTY_INDEX" and other.calls == []


@pytest.mark.parametrize("values", [[], [0, 0], [True], [float("nan")], [float("inf")], ["0.4"]])
def test_invalid_vectors_are_rejected(values):
    with pytest.raises(ValueError):
        normalize_vector(values)


def test_nim_transport_uses_input_modes_and_validates_response_indices():
    requests = []
    def transport(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.url.path == "/v1/embeddings"
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0, 2]}, {"index": 0, "embedding": [2, 0]}]})
    with httpx.Client(transport=httpx.MockTransport(transport)) as http:
        provider = NimEmbeddingClient("https://nim.example/v1", "synthetic-model", client=http)
        assert provider.embed(["account", "payment"], input_type="passage") == [[1, 0], [0, 1]]
        assert requests[0]["input_type"] == "passage"
        with pytest.raises(ValueError):
            provider.embed(["account"], input_type="query")


def test_configured_embeddings_are_opt_in_and_configuration_failure_degrades(tmp_path, monkeypatch):
    path = tmp_path / "memory.sqlite3"
    with IncidentStore(path) as store:
        saved(store)
    monkeypatch.setenv("TRACEBRIDGE_EMBEDDINGS_ENABLED", "1")
    monkeypatch.delenv("TRACEBRIDGE_EMBEDDING_URL", raising=False)
    disabled = search_memory("agolive", "회원가입이", db_path=path)
    assert disabled["hit_count"] == 1 and "semantic" not in disabled
    degraded = search_memory("agolive", "회원가입이", db_path=path, semantic_enabled=True)
    assert degraded["status"] == "OK" and degraded["hit_count"] == 1
    assert degraded["semantic"]["status"] == "FAILED"


def test_index_batch_limit_and_vector_scope_apply_before_external_query(tmp_path):
    provider = EmbeddingsDouble()
    with IncidentStore(tmp_path / "memory.sqlite3") as store:
        saved(store)
        saved(store, environment="prod")
        result = index_reviewed_cards(store, "agolive", provider, limit=1)
        assert result["indexed"] == 1 and result["pending_within_scan"] == 1
        calls = len(provider.calls)
        assert store.search("agolive", "계정을 만들 수 없어요", embedding_client=provider,
            scope_filters={"service": "unregistered-service"})["hit_count"] == 0
        assert len(provider.calls) == calls


def test_labeled_evaluation_reports_recall_and_no_relevant_case_false_hits(tmp_path):
    from scripts.evaluate_memory_retrieval import evaluate_cases
    path = tmp_path / "evaluation.sqlite3"
    provider = EmbeddingsDouble()
    with IncidentStore(path) as store:
        run = saved(store)
        index_reviewed_cards(store, "agolive", provider)
    cases = [{"project_id": "agolive", "query": "계정을 만들 수 없어요", "expected_card_ids": [run["run_id"]]},
             {"project_id": "agolive", "query": "결제 요금", "expected_card_ids": []}]
    lexical = evaluate_cases(cases, path)
    hybrid = evaluate_cases(cases, path, embedding_client=provider)
    assert lexical["mean_recall_at_2"] == 0
    assert hybrid["mean_recall_at_2"] == hybrid["mrr_at_2"] == 1
    assert hybrid["no_relevant_case_false_hit_rate"] == 0
    assert "query" not in hybrid["cases"][0]
    assert "synthetic" not in json.dumps(hybrid["cases"][0]["retrieved_card_ids"])
