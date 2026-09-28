# 세션 C 인계 — 사건 기억·매뉴얼

기준일: 2026-09-28. 기존 staged/unstaged/미추적 변경을 보존하는 동일 workspace 작업이다. 소유 파일은 `tracebridge/incident_memory.py`, `scripts/incident_memory.py`, `scripts/replay_incident_memory.py`, 새 `scripts/export_incident_manual.py`, 새 `tests/test_parallel_c_*.py`, 이 문서다. 전용 DB·검사·샘플은 `output/parallel-c`에 둔다. 공용 실제 DB, 주 조사·계약·서비스·화면, 다른 세션 파일은 편집하지 않는다.

## 공개 API (구현 전 연결 계약)

```python
search_memory(
    project_id: str, query: str, *, signals: dict | None = None,
    exclude_incident_id: str | None = None,
    db_path: str | Path | None = None, enabled: bool = True,
) -> dict

recheck_memory(search: dict, result: dict) -> dict

export_manual(
    project_id: str, *, db_path: str | Path | None = None,
    card_ids: list[str] | None = None,
) -> dict

IncidentStore.export_manual(project_id: str, *, card_ids: list[str] | None = None) -> dict
```

기존 인자와 기본값은 유지한다. `enabled=False`는 DB 연결·검색·카드 전달 없이 아래와 같은 결과를 반환한다. `recheck_memory`도 빈 카드·빈 재확인으로 유지한다. `persist_result` / `IncidentStore.save_run`은 이 옵션과 독립적으로 사건을 계속 저장한다.

```json
{
  "status": "DISABLED", "strategy": "NONE", "hit_count": 0,
  "elapsed_ms": 0.0,
  "query_signals": {"error_codes": [], "paths": [], "exceptions": [], "stack_fingerprints": []},
  "query_hash": "입력의 SHA-256", "cards": []
}
```

`elapsed_ms`는 실제 함수 시간이다. 정상 검색은 동일 키의 `OK`, 실패는 카드 없는 `FAILED`와 `error_type`을 반환한다. 끔 상태의 `current_recheck`는 비어 있고 `rechecks=[]`다.

`export_manual`은 같은 프로젝트의 현재 `APPROVED`/`EDITED` 카드만 렌더링한다. `card_ids=None`이면 검토 카드 전체, 목록이면 그 프로젝트에서 존재하는 카드로 한정한다. 미검토·폐기 카드는 내보내지 않는다. 반환은 `{"status": "OK", "project_id": str, "card_count": int, "card_ids": list[str], "markdown": str}`다. 빈 검토 목록도 `OK/card_count=0`인 매뉴얼이다. 잘못된 프로젝트·카드 선택 및 DB 오류는 예외로 보고한다. API는 파일을 쓰거나 도구를 실행하지 않는다.

## 카드 확장과 검토 계약

기존 필드/검증 플래그를 보존하며 적용 조건(`applicability`: 프로젝트·서비스·환경·동작·메서드·경로·버전), 관측 특징, 근거가 붙은 확인 순서(`check_sequence`), 반증 조건(`disproof_conditions`), 부적합 조건(`invalid_conditions`), 한계(`limitations`), 출처 실행/마지막 확인을 추가한다. 승인은 `review`에만 기록한다. 원인 확인·후보 검증·원본 적용·회복 확인은 별도 상태와 실행 출처를 갖는다.

`review_card(..., action="edit", changes=...)`는 기존 `symptom/finding/next_action` 문자열 변경을 계속 지원하며, 위 적용 조건과 확인·반증·부적합 조건/한계를 검토 문구로 정정할 수 있게 확장한다. 사실 검증 플래그·지문·원본 실행·권한은 이 API로 수정할 수 없다. 정정 전 값과 검토 이력은 보존한다. 자세한 입력 모양과 실행 결과는 구현 뒤 이 문서에 갱신한다.

JSON 카드의 기본값을 호환 보강한다. 기존 `runs`/내용 해시/중복 판정/`change_jobs`/후속 답변 이력은 다시 쓰지 않는다. SQLite 테이블 구조를 확장할 필요가 없는 경우 `user_version=2`를 유지한다.

## A/D 연결점

- A: 초기 접수·후속 답변의 선택 옵션을 `search_memory(..., enabled=memory_enabled)`로 전달하고 저장은 그대로 호출한다. `recheck_memory` 반환의 버전·조건 미관측/불일치 상태는 조사 단서로만 소비한다. 매뉴얼 다운로드는 `export_manual(...)["markdown"]`으로 연결할 수 있다.
- D: 기억 끔/켬 평가의 초기 검토 DB를 각각 전용 복제본으로 고정하고 `enabled`만 바꾼다. 카드 수/재확인 상태/현재 근거 참조를 원자료로 기록한다. 내보내기 API는 로컬 산출물 생성에 사용할 수 있다.
- 이 세션은 주 조사·서비스·화면·평가 러너를 수정하지 않는다. 새 외부 호출·벡터 DB·모델 학습은 추가하지 않는다.

## 검사·샘플·남은 게이트

진행 중. 반복 사건, 같은 경로/상태의 다른 현재 원인, 버전 변경/미관측, 충돌, 검토 정정·폐기, 재연결·실행/검토 이력, 후보 성공/원본 미적용, 검색 끔/저장 유지와 매뉴얼 출처를 새 C 전용 검사로 확인한다. 실제 기억 효과 평가와 코드 고정 후 전체 회귀는 메인/D의 후속 작업이다.
