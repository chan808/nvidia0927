# A·D 진행 중 수행한 사전 통합 점검

> 과거 기록입니다. 당시 계획·검사·제약을 보존하며, 현재 상태는 [구현 상태](../current-state.md)를 기준으로 확인하세요.

2026-09-28. A·D의 원본 파일을 편집하지 않고 고정 사본에서 먼저 검사했다. **최종 통합·실모델·실프로젝트 성공 판정이 아니다.** 완료된 C 저장 모듈에서 재현된 저장 재시도 결함만 원본에 국소 수정했다.

## 미리 끝낸 작업

- 소스 고정 도구가 `tests/docs/skills`, 제출에 필요한 루트 문서·설정, 평가용 이미지도 포함하도록 보강했다. 미커밋·미추적 변경을 포함하며 키·기존 DB·임시 산출물은 제외한다.
- 별도 사본/DB에서 접수→카드 승인→매뉴얼 내보내기→재시작 후 답변→새 사건의 과거 카드 검색을 실행했다. 사건/실행 ID 보존·새 실행 저장·원본 미적용·모델 0회를 확인했다.
- 과거 카드가 검색돼도 현재 원인으로 승격되지 않았다. 실제 실행 SHA가 없는 사례는 `NOT_REVALIDATED / usable_as_current_evidence=False`를 유지했다.
- 고정 코드에서 전체 사전 회귀를 실행하고 실패를 재현 목록으로 만들었다. 원본의 A·D 편집이나 공용 DB·최종 ZIP·서비스 포트에는 간섭하지 않았다.

## 저장 재시도 결함과 국소 수정

기존 CLI는 조사 결과를 저장한 뒤 `presentation` 또는 `manual_export`를 붙였다. C의 저장 해시는 이를 새 사실처럼 포함해서 같은 CLI 결과 파일의 저장 재시도를 `Run ID already has different contents`로 거절했다. 조회한 기존 최소 기록의 재저장도 거절됐다.

수정은 `tracebridge/incident_memory.py::_insert_run`에 한정했다.

1. 저장 해시에서 알려진 표시/전송 필드 `persistence/presentation/manual_export`를 제외한다. 나머지 원문 입력까지 해시하므로 서로 다른 비공개 입력이나 사실을 같은 실행으로 처리하지 않는다.
2. 기존 해시는 다시 쓰지 않는다. 이전 방식의 해시도 같은 입력이면 인정한다.
3. 조회된 기존 최소 기록은 **이미 저장된 JSON과 정확히 같을 때만** 중복 저장으로 인정한다. 새 입력을 정규화해서 비슷한 기록이면 허용하는 방식이 아니다.
4. 요약·수정 검증 주장·버린 비공개 입력 등이 달라지면 계속 `RunConflict`다.

실제 CLI 결과 파일과 조회 기록 파일을 저장만 재시도해 둘 다 `ALREADY_SAVED`를 확인했다. 모델·조사·작업을 재실행하지 않았다. 소스 모듈 overlay 전후 해시와 결과는 `output/main-foundation/20260928T142620Z-d3a19ed9/memory-check-snapshot/output/main-memory-flow/save-retry-fixed.json`에 있다.

새 재시도 검사 6개 + 기반 검사 10개 + 기존 C 검사 47개 = **63개 통과(6.04초)**. `output/main-foundation/pytest-early-retry.xml`로 확인한다. 이 결과를 전체 회귀 통과로 합산하지 않는다.

## 사전 회귀의 범위와 결과

기준 사본: `output/main-foundation/20260928T142620Z-d3a19ed9/snapshot`.
소스 manifest SHA-256: `d2a27f867e185402d4413caf4a8ca5116e80039a98e212a278712e5c698b77de`.

| 실행 | 결과 | 해석 |
| --- | --- | --- |
| 첫 전체 사전 회귀 | 415 통과 / 74 실패, 91.58초 | 검증 도구의 로컬 socketpair 차단과 평가 이미지 누락이 섞인 결과 |
| 환경/자료 보완 후 실패 74건만 재검사 | 21 통과 / 53 실패, 36.17초 | 제품 코드는 같은 이전 사본. 내부 loopback 허용·외부 Python 연결 차단, 평가 이미지의 기존 suite SHA 확인 후 추가 |
| C 재시도 수정 후 전용 검사 | 63 통과 | 수정한 저장 경로·기반 도구·기존 C 범위만 확인 |

첫 검증 도구는 Streamlit/asyncio의 로컬 socketpair도 막았다. 이후 loopback만 허용해 재검사했다. 빠진 `signup-error.png`는 당시 suite에 기록된 SHA와 일치하는 바이트만 별도 사본에 추가했다. 원래 결과와 재검사 원자료는 덮어쓰지 않았다.

**53은 이전 사본의 실패 건수이지 현재 제품의 결함 수가 아니다.** 이후 C 수정과 A·D의 계속되는 변경은 그 사본에 반영되지 않는다. 원자료와 실패 목록을 사용해 통합 작업을 좁힌다.

근거:

- 첫 실행: `snapshot/output/main-preintegration/regression.xml`, `regression.log`
- 실패 재검사: `repaired-check-snapshot/output/main-preintegration/recheck.xml`, `recheck.log`
- 기억 흐름: `memory-check-snapshot/output/main-memory-flow/checks-corrected.json`, `manual.md`

위 경로는 모두 같은 `output/main-foundation/20260928T142620Z-d3a19ed9` 아래다. 매뉴얼 상태의 underscore가 Markdown에서 escape된 것을 검사기가 실패로 본 경우도 정정했으며 원래 체크 파일은 보존했다.

## 통합할 때 우선 처리할 항목

| 우선 | 항목 | 처리 방향 |
| --- | --- | --- |
| 완료 | CLI 저장만 재시도·조회 기록 재저장 | 위 C 국소 수정. API/기존 DB/해시 보존, 다른 내용 거절 유지 |
| 1 | 기존 합성 GUIDANCE/WORK_CANDIDATE 양성 자료 | 현재 입력·타입·호출자 코드·버전 근거를 명시적으로 보강. 일반 422 책임 판정을 느슨하게 하지 않는다 |
| 1 | 시간 초과·수집 불완전성·충돌의 결과 규약 | `claim_items`, 종료 라우팅, 완전성/충돌 필드와 기존 검사의 기대를 대조. 관측 보존·미확정·작업 차단은 유지 |
| 2 | 새 계약 조회 도구와 근거 ID | 도구/근거 검사를 새 공용 인터페이스에 맞춤. 과거 카드의 권한 확대·악성 도구 금지 조건은 그대로 유지 |
| 2 | 수정 전 실패 모의 자료 | 강화된 정책이 요구하는 고정 검사·출처 정보를 갖춘 fake로 미재현/다른 실패를 검사. 정책을 완화해 NOT_REPRODUCED로 보내지 않는다 |
| 2 | 패키지 검사 | D의 명시적 source root·추가 lock/공지 파일과 기존 monkeypatch 가정을 대조. DB/키/임시 파일 제외는 유지 |

실패 53건의 정확한 이름·메시지는 같은 실행 폴더의 `remaining-failures.json`/`.md`에 있다. 기대값 차이를 모두 테스트 수정으로 처리하지 말고 실제 동작·제품 계약을 먼저 대조한다.

## 다음 순서

A·D 완료 후 해당 파일의 새 해시와 이전 사본을 비교한다. 위 목록의 겹치는 수정부터 확인하고, 고정 통합 코드에서 최종 전체 회귀·평가·묶음 재현을 수행한다. 현재 단계에서 새 외부 모델/OCR 호출·운영 접속·원본 프로젝트 변경·배포·제출은 하지 않았다.
