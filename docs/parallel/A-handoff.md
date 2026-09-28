# 세션 A 인계 — 수집·주 조사·서비스 연결

기준일: 2026-09-28. 작업 시작 HEAD: `34ce50b0cf6f214726e64a3fbee1f4195c397d4e`, Windows / Python 3.12.7 (`.venv`). 기존 staged/unstaged/미추적 변경이 있는 동일 workspace에서 A 소유 파일만 편집한다. 기준 상태·파일 해시는 `output/parallel-a/baseline.json`에 기록한다.

## 소유 범위와 실행 제한

- `tracebridge/project_sources.py`, `report_agent.py`, `report_intake.py`, `report_contract.py`, `report_service.py`, `deadline.py`, `seed_project.py`, `change_policy.py`, `change_proposal.py`, `change_worker.py`
- `pages/2_Report_Agent.py`, `scripts/investigate_report.py`
- 새 `tests/test_parallel_a_*.py`, 이 인계 문서. 전용 산출물·DB: `output/parallel-a`.
- 기존 변경·`examples/seed_signup`·`examples/change_policy`는 보존한다. 다른 세션 파일·기존 테스트·공용 문서는 편집하지 않는다. 새 외부 모델/OCR 호출·실프로젝트 실행·원본 적용·배포는 수행하지 않는다.

## 시작 시 확인한 결함과 우선 작업

`project_sources.py`가 로그 입력을 300 KB / 5,000줄로 먼저 자르고, Docker stdout도 절단한다. 일부 상위 절단 상태가 최종 완전성 판단에 전달되지 않을 수 있다. 관측 범위 수집을 화면·모델 발췌 제한과 분리하고, 바이트·줄·관측 수·timeout/소스 실패를 종단으로 전달한다. 짧은 로그·약 481 KB·짧은 줄 6,000개의 같은 사건 500/422 충돌을 전용 doubles 검사로 확인한다.

## 외부 공개 API 연결점 (시작 상태)

- B: `triage` / `evidence`의 호환 API를 사용 중. 공통 계약 분석, 프로젝트 profile/계약 도구의 공개 API는 `B-handoff.md`와 실제 구현이 준비되면 읽어 소비한다.
- C: 현재 사건 기억 검색·저장 API를 사용 중. keyword-only `search_memory(enabled=False)`와 `export_manual`의 공개 API가 준비되면 화면·CLI·주 조사에 연결한다.
- D: 주 조사 도구·모델·시간·종료 이벤트를 받을 `nat_observability` API가 준비되면 연결한다. 실제 NAT 종단 실검증은 미완료로 남긴다.
- 시작 시 `docs/parallel` 인계와 B/D 새 모듈은 없었다. 기다리는 동안 로그 수정과 A 전용 doubles 검사를 먼저 수행한다.

## 완료 구현

A 소유 구현·전용 검사를 완료했다. 변경 파일은 `tracebridge/project_sources.py`, `report_agent.py`, `report_intake.py`, `report_contract.py`, `report_service.py`, `seed_project.py`, `change_policy.py`, `change_worker.py`, `pages/2_Report_Agent.py`, `scripts/investigate_report.py`, 새 `tests/test_parallel_a_collection.py`, `test_parallel_a_investigation.py`, `test_parallel_a_worker.py`, 이 문서다. `deadline.py`와 `change_proposal.py`는 기존 구현을 재사용하며 편집하지 않았다.

- `LogRead` / `LogText` / `file_log_stream`으로 상위 수집 상태를 전달한다. 파일은 처음부터 64 KB 청크로 검사하며 제공 문자열도 청크 인코딩한다. 4 MB / 50,000줄의 별도 검사 예산, 64 KB 개별 레코드, 100개 관측 보존 한도를 둔다. 이 값은 표시 한도 20줄/모델 발췌와 별개다. 바이트·줄·개별 레코드·관측·timeout·읽기 실패·잘못된 인코딩·미확인 범위·조회 중 소스 변경은 `sources[].complete/reasons`와 최종 `aggregate.complete/incomplete_reasons`로 전파한다. 상위에서 이미 불완전한 빈 문자열도 연결 소스로 유지한다.
- 관측 보존 한도를 넘어도 검사 범위 안에서는 충돌 검사를 계속하며 최초 관측과 반증을 보존한다. 합산 실제 관측 수와 보존 수를 구분한다. 표시 발췌에서 빠진 응답은 `log_scope.retained_observations`의 값 없는 구조 자료로 저장한다. 이 범위의 EOF를 확인한 사실은 실제 프로젝트 전체 로그나 미래 관측의 완전성을 보장하지 않는다.
- Docker는 `--tail`을 제거하고 사건 시각 ±5분의 절대 `--since/--until`로 조회한다. bounded pipe/queue와 공유 deadline을 사용하고 비정상 종료·timeout의 앞서 받은 관측을 유지한다. 발생 시각 없는 최근 조회, 아직 닫히지 않은 미래 구간, cleanup 미완료는 불완전하게 남긴다. 기존 문자열 API는 상태가 붙은 제한 발췌를 반환한다. 이번 검사는 Docker doubles만 사용했다.
- 주 조사 최종 라우팅을 마지막 조회 후 다시 계산한다. `GUIDANCE/WORK_CANDIDATE`는 불완전성·충돌에서 확정하지 않는다. 조회 목적·조건·`NO_MATCH/OBSERVED`·현재 근거 ID를 `steps`와 저장되는 `log_scope.investigation.lookup_records`에 남긴다. 평면 최종 스키마를 검증하고 최종 반환 유무를 중간 조회 성공과 분리한다. 5xx 뒤 최종 요약은 한 번만 허용하며 원 실패를 유지한다. timeout·잘못된 최종 반환·최종 반환 없는 반복은 전체 성공으로 기록하지 않는다. 미반환 토큰은 `None`, 알려진 합계·누락 호출 수는 별도다.
- 등록 씨드의 실제 해시가 맞는 호출자 AST·고정 입력·계약·DTO·버전을 `SeedEvidenceSource`로 B의 공통 판정에 연결했다. 씨드의 입력 대응은 이 한 등록 대상에만 적용된다. 기준 파일을 바꾸거나 일반 계약 판정에 샘플 이름 분기를 넣지 않았다.
- 수동/자동 준비가 `preparation_blockers`를 공유한다. 작업자는 저장된 최신 자료, 조사만 요청, 정확 상관, 완전성, 충돌, 현재 지원 역할 `Frontend/Caller Repair`, 현재 씨드·정책·기준 해시를 검사 직전과 제안/편집/검증 단계마다 재확인한다. 같은 사건의 후속 후보는 기존 작업을 재사용한다. 소스별 예약 파일은 DB 저장 실패 후 진입점 재시도를 **확보한 결과의 저장만** 수행하게 한다. 제안 도중 후속 답변으로 소스가 바뀌면 편집/후속 검사를 중단하며 최신 답변을 덮지 않는다. C의 `RunConflict` 규칙에 따라 그 오래된 작업의 DB 저장은 실패로 남고 로컬 결과 파일은 보존된다.
- 화면/CLI는 제보 HTTP 검증, 실제 요청 실패, 제품/책임 판정, 조사 종료, 후보 검증, 원본 적용, 회복을 따로 표시한다. 사용자 조사만 요청이나 자료 보류에서는 준비 버튼/실행을 막는다. 기존 소스 연결 digest의 호환 경로를 유지하고, 후속 답변에서 제공 로그 연결을 버리는 것은 같은 사건으로 허용하지 않는다.

## 세 절단 재현 결과

같은 요청 ID·서비스·환경·시각의 500과 422를 앞/뒤에 두었다. 독립 최종 파일 재현 원자료는 `output/parallel-a/log-reproductions/results.json`과 그 폴더의 로그/전용 DB다.

| 재현 | 실제 파일 바이트 | 범위 검사 | 보존 응답 | 결과 |
| --- | ---: | --- | --- | --- |
| 짧은 로그 | 996 | EOF 완료, 관측 2개 | 500 / 422 | 충돌, `REQUEST_CONTEXT` |
| 약 481 KB, 중간 채움 30,000줄 | 480,996 | EOF 완료, 관측 2개 | 500 / 422 | 충돌, `REQUEST_CONTEXT` |
| 짧은 줄 6,000개 | 18,996 | EOF 완료, 관측 2개 | 500 / 422 | 충돌, `REQUEST_CONTEXT` |

파일·제공 로그·Docker double 각각에서 사건 목록 유무를 바꾼 18변형도 같은 보류를 확인했다. 강제로 바이트/줄 예산을 낮춘 6변형, 121번째 관측의 반증, 상위 빈 절단 자료, 레코드 크기 초과, 읽기/timeout/인코딩 실패에서는 `complete=False`로 보류하고 확보한 앞부분을 보존했다. 최종 세 파일 재현은 모두 전용 SQLite에 `SAVED`, 새 모델 호출 0회다. 정상 안내/작업 후보는 현재 입력·호출자·타입·버전 근거가 있는 doubles, 등록 씨드, 별도 프로젝트 설정에서 유지했다.

## 준비된 공개 API와 연결 상태

```python
investigate_submission(
    ...,
    project_profile: ProjectProfile | str | Path | None = None,
    memory_enabled: bool = True,
    observer=None,
    observer_output_dir: str | Path | None = None,
) -> dict

triage_report(..., source_adapter=None) -> dict
presentation_status(result: dict, change_job: dict | None = None) -> dict
preparation_blockers(result: dict) -> list[str]
```

- **B 연결 완료:** `load_project_profile`, `ProjectEvidenceSource(profile, event_source=...)`, `read_project_logs`, `observe_project_version`를 공개 API로 소비한다. 선택된 현재 사건을 어댑터로 감싸 별도 OpenAPI/DTO/해시가 맞는 호출자 근거를 읽는다. 주 조사에 `get_contract`를 추가했다. 코드 검색은 등록 `code_roots`로 제한하며 Agolive 폴더명 없는 프로젝트도 조회한다. `contract_analysis/responsibility/symptom_status/product_status`를 반환하고 `log_scope.assessment`에도 호환 보존한다. JSON/JSONL의 B 수집 한도/부재도 불완전성을 지우지 않는다. 로컬 버전 snapshot은 실제 배포 확인으로 표시하지 않는다. `policy_refs`는 수정 권한이 아니다.
- **C 연결 완료:** `memory_enabled`를 keyword-only `search_memory(..., enabled=...)`로 전달한다. `False`면 카드/검색 없이 `DISABLED`, 현재 사건 저장은 계속된다. 후속 답변에서도 선택할 수 있다. 화면의 검토 매뉴얼 다운로드와 CLI `--manual-output`은 `export_manual`을 소비한다. 재연결·검토 출처와 후보/원본 상태를 별도로 보존한다.
- **D 연결 완료(로컬/doubles):** `InvestigationObserver.record_tool/record_model/finish`를 실제 주 조사 완료 경계에 호출한다. `run_id`, 도구 시도·모델 호출 수, 종료 상태가 일치하는 검사와 injected sink 전달 검사를 통과했다. 본문·프롬프트·코드·도구 인자를 D observer에 전달하지 않는다. 원자료는 각 A 검사 경로의 `metrics/<run_id>/events.jsonl/summary.json`이다. sink 없는 경우 `NOT_CONFIGURED`, sink double 전달도 `main_flow_verified=False`다. 실제 NAT manager + 실제 모델 주 흐름 검증은 완료로 주장하지 않는다.

CLI 옵션: `--profile <JSON>`, `--no-memory`, `--metrics-dir <A전용경로>`, `--manual-output <MD>`. 기존 `--repo/--registered-seed/--resume/--answer/--prepare-change`를 유지한다. 화면은 `TRACEBRIDGE_PROJECT_PROFILE`로 등록 프로젝트를 추가하고 `TRACEBRIDGE_METRICS_DIR`가 있을 때만 계측 출력을 생성한다. 기존 공유 DB/서비스 포트를 이번 검사에 사용하지 않았다.

## 검사 명령·결과와 보존

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_parallel_a_collection.py tests/test_parallel_a_worker.py tests/test_parallel_a_investigation.py --junitxml=output/parallel-a/a-final-results.xml
# 66 passed. 같은 검사에는 CLI subprocess와 Streamlit AppTest, 실제 등록 씨드 사본의 before/after/regression이 포함된다.

git -c core.safecrlf=false diff --check
# PASS
```

`output/parallel-a/a-final-results.xml`, `final-manifest.json`, `protected-files.json`에 전용 검사·소스/API 해시·기준 보존을 기록한다. 최초 pytest private temp 경로는 Windows sandbox ACL로 접근 실패했다. A 검사만 일반 고유 디렉터리를 생성하도록 바꾸었고 긴 채움 문자열의 pytest ID를 짧게 지정했다. 최종 검사에는 그 setup/cleanup 오류가 없다. 이는 승인 검토 거절이나 제품 성공 기록이 아니다.

등록 씨드 6파일과 정책 1파일의 시작 해시 **7개 모두 보존**했다. 작업 중 공유 HEAD가 `17003f839d305b1c79ff5b4d4ed1b77cad61812b`로 이동했으며 A는 commit/reset/clean/staging 명령을 수행하지 않았다. 비교 기준은 최초 working-tree 파일 해시다. 다른 세션의 검사 수와 합산하지 않으며 전체 회귀를 실행/통과로 주장하지 않는다. **이번 세션의 새 외부 모델/OCR 호출은 0회**다.

## 미완료·메인 인계

1. B가 기록한 기존 `tests/test_report_intake.py:18` 및 동일한 공용 `examples/report_events.json`/일부 scoped 예제는 현재 입력·호출자·타입·실행 버전 출처가 없어 이제 `INVESTIGATE`다. 일반 422를 안내로 되돌리지 않았다. 메인에서 합성 시연임이 명시되고 해시·입력·버전이 확인된 시연 소스를 붙여 기존 강한 기대값을 검사해야 한다. A 전용 정상 경로는 출처가 충분한 자료로 검증했고 등록 씨드 연결은 완료했다. 공용 예제/기존 테스트는 편집하지 않았다.
2. A/B/C/D 편집 종료와 소스/API 해시 고정 뒤 **전체 회귀·12건 평가·최종 묶음 재현**이 필요하다. 현재 A의 66개 통과는 그 대체물이 아니다. B/C/D가 API 계약을 추가로 변경하면 해당 연결과 A 전용 검사를 다시 확인한다.
3. 현재 최종 스키마의 **실제 모델 반복 조사, 조회 실패/반증 후 적응, 서빙 장애, 실제 NAT manager 주 흐름**은 실검증 재개 때 확인한다. 로컬/doubles의 전체 반환 성공을 실제 모델 성공으로 확대하지 않는다.
4. **실프로젝트 사건·실행 버전·실제 로그/계약 연결·기억 효과, OS 격리/OpenShell, 원본 적용·배포·서비스 회복**은 미완료다. 씨드 사본의 동일 검사/회귀 통과는 `CHANGE_PREPARED / WAITING_REVIEW` 범위다. 사용자 대기 지시를 유지한다.
