# 세션 C 인계 — 사건 기억·매뉴얼

> 과거 기록입니다. 당시 계획·검사·제약을 보존하며, 현재 상태는 [구현 상태](../../current-state.md)를 기준으로 확인하세요.

기준일: 2026-09-28. 기존 staged/unstaged/미추적 변경을 보존하는 동일 workspace 작업이다. 소유 파일은 `tracebridge/incident_memory.py`, `scripts/incident_memory.py`, `scripts/replay_incident_memory.py`, 새 `scripts/export_incident_manual.py`, 새 `tests/test_parallel_c_*.py`, 이 문서다. 전용 DB·검사·샘플은 `output/parallel-c`에 둔다. 공용 실제 DB, 주 조사·계약·서비스·화면, 다른 세션 파일은 편집하지 않는다.

## 공개 API (구현 전에 게시한 연결 계약; 현재 구현됨)

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

`export_manual`은 같은 프로젝트의 현재 `APPROVED`/`EDITED` 카드만 렌더링한다. `card_ids=None`이면 검토 카드 전체, 목록이면 그 프로젝트에서 존재하는 카드로 한정한다(목록 최대 1,000개, 중복 제거). 미검토·폐기 카드는 내보내지 않는다. 반환은 `{"status": "OK", "project_id": str, "card_count": int, "card_ids": list[str], "markdown": str}`다. 빈 검토 목록도 `OK/card_count=0`인 매뉴얼이다. 존재하지 않는 DB는 생성하지 않고 `FileNotFoundError`, 다른 프로젝트·존재하지 않는 카드 선택은 `ValueError`, DB 문제는 `sqlite3.Error`로 보고한다. 출력 파일은 호출자가 저장한다. DB 연결은 기존 `IncidentStore`의 스키마 호환 초기화를 사용한다. 실행 도구는 호출하지 않는다.

## 카드 확장과 검토 계약

기존 필드/검증 플래그를 보존하며 적용 조건(`applicability`: 프로젝트·서비스·환경·동작·메서드·경로·runtime/local SHA·계약/DTO/호출자 버전), 관측 특징, 근거가 붙은 확인 순서(`check_sequence`), 반증 조건(`disproof_conditions`), 부적합 조건(`invalid_conditions`), 한계(`limitations`), 출처 실행/마지막 확인을 추가한다. 제보 HTTP/실제 HTTP/증상/제품/책임 판정도 별도로 보존한다. B/A의 선택 `contract_analysis`/`responsibility`/`symptom_status`/`product_status`를 최소 투영하며, 이전 저장 자료는 `log_scope.assessment`에서도 읽을 수 있다.

승인은 `review`에만 기록한다. `verification`의 기존 세 boolean은 그대로 유지하고 `verification_results`에 네 상태를 분리한다. `cause_confirmation`은 기록된 원인 확인과 실행이 붙은 지지 근거가 있을 때만 `RECORDED_CONFIRMED`, 후보는 검증된 구조로 **저장된** `change_jobs`의 before/after/regression 기록이 있을 때 `VERIFIED`, 원본은 `NOT_APPLIED`/`RECORDED_APPLIED`, 회복은 `NOT_VERIFIED`/명시된 `RECORDED_VERIFIED`다. 원인·후보의 성공 플래그만 있고 필요한 지지·검사 출처가 없으면 `REPORTED_UNVERIFIED`다. `fix_verified=True`만으로 회복을 추론하지 않는다. 후보 검증 카드의 관측/버전/마지막 자료 확인은 원 조사 실행을, 후보 검사는 결과 실행과 작업 ID를 가리킨다.

`review_card(..., action="edit", changes=...)`는 기존 `symptom/finding/next_action` 문자열 변경을 계속 지원하며, 아래 입력으로 검토 내용을 정정할 수 있다. 사실 검증 플래그·지문·원본 실행·프로젝트 ID·권한은 수정할 수 없다. 정정 전 값과 검토 이력을 보존한다. SHA 제약을 정정해도 원 실행의 버전 관측은 바뀌지 않는다.

```python
changes = {
    "applicability": {"environment": "dev", "runtime_sha": "a" * 40},
    "check_sequence": ["현재 요청 로그 범위와 충돌을 확인한다.", "현재 계약·호출자 생성 위치를 확인한다."],
    "disproof_conditions": ["현재 계약 생성 동작이 과거와 다르면 기각한다."],
    "invalid_conditions": [{"when": {"environment": "prod"}, "reason": "운영 적용은 검증하지 않았다."}],
    "limitations": ["현재 버전과 자료 범위의 별도 확인이 필요하다."],
}
store.review_card(project_id, card_id, "edit", reviewer="local-owner", changes=changes)
```

`applicability`는 부분 dict이고 변경 가능한 키는 `service/environment/operation/method/path/runtime_sha/local_sha`다. 조건 문자열은 1~200자, SHA는 7~64자리 hex, 미확인 제약은 `None`으로 표시할 수 있다. 단계/반증/한계는 최대 12개의 1~500자 문자열이다. 부적합 조건은 최대 12개 `{when: 조건 dict, reason: 짧은 문자열}`이며 현재 조건이 일치하면 보류한다. 자유문 반증 정정은 자동으로 참/거짓을 해석하지 않고 현재 별도 확인이 필요한 상태로 보류한다. CLI는 `review --changes-file <JSON>`으로 같은 객체를 받는다.

JSON 카드 `card_format=reviewed_manual_v2`의 기본값을 읽을 때 호환 보강한다. 테이블/인덱스 구조와 `user_version=2`를 유지한다(기존 0/1 DB는 원래의 2 초기화 경로). 기존 `runs`/내용 해시/중복 판정/`change_jobs`/후속 답변 이력을 다시 쓰지 않는다. 이전 카드의 JSON은 조회/내보내기만으로 재작성하지 않으며, 다음 검토 변경 때 보강한 카드와 전체 검토 이력을 같은 트랜잭션에서 저장한다. 원 실행 해시는 유지되므로 저장 재시도와 기존 작업 출처 해시가 충돌하지 않는다.

## 재확인 결과

현재 자료·버전·조건이 모두 확인된 안내의 독립 관측은 `CURRENT_GUIDANCE_OBSERVED`다. 다른 HTTP/예외/오류/스택/진단은 `REJECTED`, 버전 변경·조건 변경·미관측·불완전성·충돌·조사 중단은 `NOT_REVALIDATED`다. 이미 기각된 다른 원인은 버전 문제 때문에 보류로 되돌리지 않는다.

각 재확인에 `applicability_status=MATCH|MISMATCH|NOT_OBSERVED`, `current_guidance_observed`, `usable_as_current_evidence=False`, 현재 실행의 `current_evidence_refs`를 추가한다. 독립 안내를 관측해도 버전이 미관측이면 최종 상태는 `NOT_REVALIDATED/current_guidance_observed=True`다. 과거 참조는 `historical:<run_id>:<id>`이며 현재 observations에 합치지 않는다. 새 검색은 최신 검토 상태를 읽는다. 이미 전달한 검색 snapshot을 나중 검토 변경으로 소급 갱신하는 기능은 없다.

## A/D 연결점

- A: `report_agent.investigate_submission(..., memory_enabled=True)`가 `search_memory(..., enabled=memory_enabled)`를 소비하는 코드, 접수 CLI의 `--no-memory`/`--manual-output`, 화면의 검색 checkbox·매뉴얼 내보내기 소비 코드를 확인했다. 후속 답변에서도 같은 옵션과 기존 저장 경로를 사용한다. 이 파일들을 C가 편집한 것은 아니다. `recheck_memory`의 버전·조건 미관측/불일치는 조사 단서이며 현재 근거가 아니다.
- D: `evaluation.CONDITIONS`의 `tracebridge_memory_off/on`과 `AdapterContext.memory_enabled`가 A의 선택 옵션을 사용하는 코드를 확인했다. 초기 검토 DB는 조건/실행마다 전용 복제본으로 고정한다. 카드 수/현재 참조/`applicability_status`/`current_guidance_observed`를 함께 소비해야 한다. 특히 미관측 버전에서 `NOT_REVALIDATED`와 현재 안내 관측 true가 동시에 있을 수 있다. C는 D의 12건 평가를 실행하거나 결과를 통과로 주장하지 않았다.
- 패키징: 새 `scripts/export_incident_manual.py`와 이 문서의 API/샘플을 포함한다. `output/parallel-c`의 DB·임시 산출물은 제외하고 필요한 최종 매뉴얼은 메인/D의 지정 산출 경로로 새로 내보낸다.
- 이 세션은 주 조사·서비스·화면·평가 러너를 수정하지 않는다. 새 외부 호출·벡터 DB·모델 학습은 추가하지 않는다.

## 변경 파일과 사용 예

- `tracebridge/incident_memory.py`: 선택 `enabled`, JSON 카드 보강·정정·조건/버전 재확인, 매뉴얼 API, B/A의 선택 판정 메타데이터 저장.
- `scripts/incident_memory.py`: `search --disable-memory`, `review --changes-file`.
- `scripts/replay_incident_memory.py`: `--cards-only` 독립 재생, 주 조사 재생의 명시적 합성 입력/호출자 출처, 매뉴얼·실패 기록.
- 새 `scripts/export_incident_manual.py`: 명시한 DB/프로젝트의 검토 카드 Markdown 출력 또는 `--output *.md` 저장.
- 새 `tests/test_parallel_c_memory.py`, 이 문서. 기존 테스트·등록 씨드/정책·다른 세션 파일은 C가 수정하지 않았다.

```python
from pathlib import Path
from tracebridge.incident_memory import search_memory, recheck_memory, persist_result, export_manual

db = Path("output/parallel-c/intake-replay-20260928-v2/incidents.sqlite3")
disabled = search_memory("agolive", "회원가입", db_path=db, enabled=False)
# fresh_result는 아직 저장하지 않은 새 실행 결과다. 기존 실행 내용을 바꾸지 않는다.
fresh_result["memory_search"] = recheck_memory(disabled, fresh_result)
persist_result(fresh_result, db_path=db)  # 검색 끔과 독립된 저장
manual = export_manual("agolive", db_path=db)
Path("output/parallel-c/incident-manual.md").write_text(manual["markdown"], encoding="utf-8")
```

```powershell
.\.venv\Scripts\python.exe -m scripts.incident_memory --db output/parallel-c/intake-replay-20260928-v2/incidents.sqlite3 --project agolive search --disable-memory
.\.venv\Scripts\python.exe -m scripts.export_incident_manual --db output/parallel-c/intake-replay-20260928-v2/incidents.sqlite3 --project agolive --output output/parallel-c/incident-manual.md
# 재생은 매번 새 디렉터리로 실행한다.
.\.venv\Scripts\python.exe -m scripts.replay_incident_memory --cards-only --directory output/parallel-c/card-replay-new
.\.venv\Scripts\python.exe -m scripts.replay_incident_memory --directory output/parallel-c/intake-replay-new
```

## 검사와 샘플

최종 C 전용 명령:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_parallel_c_memory.py --basetemp output/parallel-c/pytest-c-20260928-04 --junitxml output/parallel-c/pytest-c-04.xml
git diff --check
```

**47개 통과, 4.87초**. JUnit은 `output/parallel-c/pytest-c-04.xml`이다. 첫 제한 권한 실행은 Windows pytest 임시 폴더 ACL 접근 오류로 제품 검사 전에 중단됐고, 새 C 임시 경로의 정상 권한 실행으로 확인했다. 이후 카드·계약 버전/조건·재생 검사를 추가해 위 최종 C 검사 전체를 실행했다. 공유 코드의 전체 회귀는 실행하지 않았으며 다른 세션 결과를 합산하지 않았다. `git diff --check`는 exit 0이고 LF/CRLF 안내만 있었다.

| 검사 자료/조건 | 확인 결과 |
| --- | --- |
| 같은 문제·같은 관측 버전/조건 | 기록된 확인 순서 전달, `CURRENT_GUIDANCE_OBSERVED/MATCH`, 현재/과거 근거 ID 분리 |
| 같은 경로·HTTP 500, SQLiteException → IllegalStateException | 과거 원인 `REJECTED`; 버전 충돌을 추가해도 기각 유지 |
| runtime/local 또는 계약·DTO·호출자 버전 변경/미관측 | `NOT_REVALIDATED`, `MISMATCH` 또는 `NOT_OBSERVED`; 수동 SHA/검토 문구로 관측을 대체하지 않음 |
| 현재 조건 변경/미관측·조회 불완전/미관측·관측 충돌 | 보류; 독립 안내 관측과 과거 적용 가능성 분리 |
| 정정·부적합 조건·반증 문구·폐기/재검토 | 정정 전 내용/검토 이력 보존, FTS 갱신, 폐기 카드 검색·매뉴얼 제외 |
| DB 재연결·후속 실행·같은 근거 ID | 실행별 이력/근거 유지, `resume_result` 최신 revision/답변 횟수 유지 |
| 동일 run/job 저장 재시도·run/revision 충돌 | 중복 `ALREADY_SAVED`, 충돌 원자적 거절, 검토/최신 실행/작업 출처 해시 보존 |
| 후보 성공/원본 미적용 | 저장된 검사 결과와 출처 작업 표시, 후보 `VERIFIED`, 원본 `NOT_APPLIED`, 회복 `NOT_VERIFIED` |
| 검색 끔·잘못된 DB 경로·저장/CLI/재시작 | DB 연결 없이 `DISABLED/cards=[]`, 사건 저장 `SAVED`, 재연결·검색 다시 켬 가능 |
| 레거시 카드·원문/개인정보·Markdown 지시문 | JSON 기본값 보강, 원 행/해시 보존, 비식별화·원문 제외, HTML/링크/코드 구문을 텍스트로 렌더링 |

실행된 독립 CLI 기록은 `output/parallel-c/card-replay-cli.json`, 주 조사 재생은 `output/parallel-c/intake-replay-v2-cli.json`과 재생 폴더의 `replay.json`이다. 후자는 `GUIDANCE/HTTP 422`를 유지하며 실제 runtime 미관측 때문에 과거 카드는 `NOT_REVALIDATED/current_guidance_observed=True`, 다른 현재 원인은 `REJECTED`, 충돌은 `REQUEST_CONTEXT/NOT_REVALIDATED`다. 이전/후속 실행 2개·중복 저장·현재 근거 출처를 확인했다.

처음 기존 자료만 사용한 주 조사 재생은 입력·호출자 버전/책임 근거가 없어 `contract_difference_unconfirmed / INVESTIGATE`로 보류됐다. 실패 원자료는 `output/parallel-c/intake-replay-failure.json`에 보존했다. **기대값을 약하게 바꾸지 않고**, C 소유 재생의 임시 로그에 실행하지 않은 합성 요청 생성 코드 파일/해시·입력 필드·계약/DTO/호출자 버전을 명시해 안내 양성 조건을 보강했다. 공유 `examples/scoped_agolive.log`와 다른 기존 테스트는 변경하지 않았다. 기존 단독 예제/검사에 같은 책임 전제가 남아 있으면 A/B/메인이 명시된 근거와 대조해야 한다.

샘플:

- 주 조사 재생 매뉴얼 (`output/parallel-c/incident-manual.md`; 로컬 산출물): 승인된 안내 단서, 제보 500/실제 422, 계약/호출자 출처와 미관측 실제 runtime, 원인·원본·회복 미확인.
- 후보 상태 매뉴얼 (`output/parallel-c/candidate-manual.md`; 로컬 산출물): **합성 TEST_DOUBLE 검사 기록**의 출처 작업·검사 산출물/해시. 실제 검사 명령/프로젝트 코드는 실행하지 않았다. 후보/원본/회복 표시를 확인하는 샘플이다.

후보 샘플 발췌(각 표시의 실제 출처 실행 `ebb389a820bf463396738e69a471c669`, 작업 `08deca96f33b428283005e0bad605a3e`):

```markdown
- 승인·정정: APPROVED (검토 revision 1; 출처 실행: ebb389a820bf463396738e69a471c669)
- 원인 확인: NOT_CONFIRMED (출처 실행: ebb389a820bf463396738e69a471c669)
- 후보 사본 검증: VERIFIED (출처 실행: ebb389a820bf463396738e69a471c669; 수정안 검증, 원본 적용·회복은 별도)
- 원본 적용: NOT_APPLIED (출처 실행: ebb389a820bf463396738e69a471c669)
- 회복 확인: NOT_VERIFIED (출처 실행: ebb389a820bf463396738e69a471c669)
```

## 남은 게이트

새 외부 모델/OCR 호출은 **0회**다. 공용 실제 DB·원본 Agolive·등록 씨드/정책을 변경하지 않았다. 벡터 DB·임베딩·모델 학습을 추가하지 않았다. 실제 사건의 기억 효과(이득/동률/악화), 코드 고정 후 전체 회귀, 실제 버전/로그/조사·후보 실행·원본 적용/회복은 메인/D 및 사용자 실검증 재개 뒤의 작업이다. 이번 합성/재생 결과로 자가학습·시간 절감·실사건 정확도 개선을 주장하지 않는다.
