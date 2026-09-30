"""Owner knowledge review and paired quality reporting on isolated synthetic data."""
from copy import deepcopy

import pytest

from test_control_plane import project, remote, report
from test_incident_memory import record
from test_project_connection import configure_ui
from tracebridge.evaluation import response_quality_comparison
from tracebridge.incident_memory import IncidentStore, ReviewConflict
from tracebridge.local_runner import GatewayError


def test_sqlite_review_queue_requires_current_revision_and_recorded_recovery(tmp_path):
    with IncidentStore(tmp_path / "review.sqlite3") as store:
        run = record()
        store.save_run(run)
        card = store.list_cards("agolive")[0]
        assert card["card_id"] == run["run_id"] and store.list_cards("foreign") == []
        with pytest.raises(ValueError, match="Recorded service recovery"):
            store.review_card("agolive", card["card_id"], "approve", reviewer="owner",
                outcome="RECOVERY_VERIFIED", note="회복 주장", expected_revision=0)
        with pytest.raises(ValueError, match="needs a reason"):
            store.review_card("agolive", card["card_id"], "approve", reviewer="owner",
                outcome="GUIDANCE_CONFIRMED", expected_revision=0)
        approved = store.review_card("agolive", card["card_id"], "approve", reviewer="owner",
            note="합성 사건의 안내 검토", outcome="GUIDANCE_CONFIRMED", expected_revision=0)
        assert approved["review"]["outcome"] == "GUIDANCE_CONFIRMED"
        assert store.list_cards("agolive") == [] and len(store.list_cards("agolive", status="APPROVED")) == 1
        with pytest.raises(ReviewConflict):
            store.review_card("agolive", card["card_id"], "reject", reviewer="owner", expected_revision=0)
        rejected = store.review_card("agolive", card["card_id"], "reject", reviewer="owner", expected_revision=1)
        assert rejected["review"]["outcome"] == "INCONCLUSIVE"
        assert store.search("agolive", "oldguideword")["hit_count"] == 0


def test_private_review_api_lists_pending_and_preserves_project_scope(remote):
    owner, runner_api, runner, http, _, _, profile = remote
    receipt = report(owner, profile)
    runner.run_once()
    project = profile.project_id
    path = f"/v1/memory/{project}/cards"
    assert http.get(path).status_code == 401
    with pytest.raises(GatewayError) as runner_denied:
        runner_api.request("GET", path)
    assert runner_denied.value.status_code == 401
    pending = owner.request("GET", path)["cards"]
    assert len(pending) == 1 and pending[0]["review"]["status"] == "PENDING"
    assert "history" not in pending[0]["review"] and "evidence" not in pending[0]
    card_id = pending[0]["card_id"]
    with pytest.raises(GatewayError) as foreign:
        owner.request("GET", "/v1/memory/other/cards")
    assert foreign.value.status_code == 404
    with pytest.raises(GatewayError) as false_recovery:
        owner.request("POST", f"/v1/memory/{project}/{card_id}/review", {"action": "approve",
            "outcome": "RECOVERY_VERIFIED", "note": "회복 주장", "expected_revision": 0})
    assert false_recovery.value.status_code == 422
    approved = owner.request("POST", f"/v1/memory/{project}/{card_id}/review", {"action": "approve",
        "outcome": "GUIDANCE_CONFIRMED", "note": "현재 증거와 안내를 확인", "expected_revision": 0})
    assert approved["review"]["revision"] == 1
    assert owner.request("GET", path)["cards"] == []
    assert owner.request("GET", path + "?status=APPROVED")["cards"][0]["review"]["outcome"] == "GUIDANCE_CONFIRMED"
    with pytest.raises(GatewayError) as stale:
        owner.request("POST", f"/v1/memory/{project}/{card_id}/review", {"action": "reject", "expected_revision": 0})
    assert stale.value.status_code == 409
    assert owner.request("GET", f"/v1/jobs/{receipt['job_id']}")["state"] == "SUCCEEDED"


def test_review_queue_is_available_from_advanced_owner_page(remote, monkeypatch):
    owner, _, runner, _, _, _, profile = remote
    report(owner, profile)
    runner.run_once()
    page = configure_ui(remote, monkeypatch).run()
    assert not page.exception
    assert any(item.label == "사건 지식 검토" for item in page.expander)
    assert not any(item.key == "knowledge-card:" + profile.project_id + ":PENDING" for item in page.selectbox)
    page.button(key="knowledge-load:" + profile.project_id).click().run()
    assert not page.exception
    assert page.selectbox(key="knowledge-card:" + profile.project_id + ":PENDING")
    assert not page.sidebar.button


def test_paired_quality_report_counts_harm_and_honest_missing_cost():
    expected = {"allowed": {"route": ["REQUEST_CONTEXT"]}, "hold": True, "requires_questions": True,
        "forbid_cause_confirmation": True, "forbid_resolution_confirmation": True}
    base = {"case_id": "ambiguous", "category": "rough_text_photo", "execution_status": "COMPLETED",
        "execution_scope": "diagnosis_only", "provenance": "LOCAL_OFFLINE", "expected": expected}
    off = {**base, "condition": "tracebridge_memory_off", "result": {"route": "GUIDANCE", "questions": [],
        "cause_confirmed": True, "fix_applied": False, "fix_verified": True, "memory_search": {"hit_count": 0}},
        "assessment": {"status": "FAIL"}, "metrics": {"model_calls": 0, "total_elapsed_ms": 10}}
    on = {**deepcopy(base), "condition": "tracebridge_memory_on", "result": {"route": "REQUEST_CONTEXT",
        "questions": ["요청 ID를 알려주세요"], "fix_applied": False, "fix_verified": False,
        "memory_search": {"hit_count": 1}}, "assessment": {"status": "PASS"},
        "metrics": {"model_calls": 1, "total_elapsed_ms": 20}}
    result = response_quality_comparison([off, on])
    assert result["paired_cases"] == 1 and result["better_on_fixed_checks"] == 1
    assert result["totals"]["tracebridge_memory_off"]["wrong_guidance"] == 1
    assert result["totals"]["tracebridge_memory_off"]["unsupported_cause"] == 1
    assert result["totals"]["tracebridge_memory_off"]["unsupported_resolution"] == 1
    assert result["totals"]["tracebridge_memory_on"]["correct_holds"] == 1
    assert result["totals"]["tracebridge_memory_on"]["retrieval_hit_cases"] == 1
    assert result["memory_effect"] == "NOT_ESTABLISHED"
