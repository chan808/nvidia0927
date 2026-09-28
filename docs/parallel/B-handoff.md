# 세션 B — 공통 판정·프로젝트 근거 인계

상태: **B 소유 구현·전용 검사 완료, API 고정**. 시작 HEAD `34ce50b0cf6f214726e64a3fbee1f4195c397d4e`. 시작 시 다수의 staged/unstaged 변경이 있었고 B 소유 기존 파일 두 개에는 변경이 없었다. 다른 세션 파일·등록 씨드·정책을 보존했다. 다른 세션의 편집·Git 상태 변경이 계속되므로 전체 제품 통과를 주장하지 않는다.

## 공개 API와 기본값

기존 `EvidenceSource`의 네 메서드, `analyze(trace_id, claim=None, source=None)`, `route_verdict(verdict, correlation)`, `summarize(result)`는 유지한다. 판정 결과에는 기본값을 갖는 `contract_analysis`, `responsibility`, `symptom_status`, `product_status`를 추가한다. 기존 필드는 제거하지 않는다.

```python
from tracebridge.contract_analysis import compare_contract, analyze_contract

compare_contract(request: dict | None, contract: dict | None, *,
                 request_fields: list[str] | None = None,
                 observed_types: dict[str, str] | None = None,
                 redacted_fields: list[str] | None = None) -> dict
analyze_contract(trace: dict, contract_data: dict | None = None) -> dict

from tracebridge.project_profile import (
    ProjectProfile, load_project_profile, read_project_logs, observe_project_version,
)

load_project_profile(path: str | Path) -> ProjectProfile
read_project_logs(profile: ProjectProfile, *,
                  max_bytes: int = 1_000_000, max_records: int = 1000) -> dict
observe_project_version(profile: ProjectProfile) -> dict

from tracebridge.project_contracts import load_project_contract

load_project_contract(profile: ProjectProfile, method: str, path: str, *,
                      runtime_version: str | None = None) -> dict

from tracebridge.evidence import ProjectEvidenceSource

ProjectEvidenceSource(profile: ProjectProfile, *,
                      event_source: EvidenceSource | None = None)
# get_trace/get_contract/get_backend_evidence/get_migration_state(trace_id)
```

`compare_contract`는 필수/실제/미등록/금지 필드, 확인 가능한 타입 차이, 미관측 타입, 표기 유사 후보를 반환한다. 값이나 타입을 비식별 표기로부터 추정하지 않는다. 후보는 rename 지시가 아니다. `analyze_contract`는 위 차이에 출처·버전 상태·책임 근거를 덧붙인다. 책임 기본값은 `UNCONFIRMED`, 제품 기본값은 `UNCONFIRMED`이며 422 자체는 제품 정상이나 사용자 책임의 근거가 아니다.

설정은 로컬 JSON 한 개다. 프로젝트 ID, 서비스/환경, 프로젝트 루트와 코드 루트, JSON/JSONL 로그 소스, 별도 OpenAPI JSON 경로, 선택 DTO/호출자 근거 경로, 버전 관측(`json_file`/`log_field`/`none`), 등록 정책 참조를 담는다. `root`는 설정 파일 기준 상대 경로 또는 명시적인 로컬 절대 경로이고 기본값은 `.`다. 소스 경로는 이 프로젝트 루트 안에서 다시 확인한다. URL·임의 명령·권한 설정은 받지 않는다. 정책 참조는 조회용 식별자이며 실행 허가가 아니다.

계약 결과는 기존 `method/path/openapi/backend_dto` 키에 `status`, `provenance`, `versions`, 선택 `caller`, `limitations`를 추가한다. 계약 부재는 `openapi=None`, `status=UNOBSERVED`로 전달한다. 로그에는 계약 snapshot이 없어도 된다. `ProjectEvidenceSource(..., event_source=...)`로 A의 이미 범위가 확인된 사건 소스를 감싸 별도 계약을 읽을 수 있다. 기본 `event_source=None`에서는 등록 로컬 로그를 읽으며 불완전/충돌이면 단일 사건 확정을 막는다.

호환 보강(구현 전 명시한 대로 구현): 위 부재 구조는 `load_project_contract`의 반환이다. 기존 `EvidenceSource` 소비자는 부재 때 `EvidenceError`를 처리하므로 어댑터 `get_contract`도 계약 부재에서 예외를 유지한다. `EvidenceError(*args, details: dict | None = None)`의 선택 `details`에 같은 부재 구조를 붙인다. `analyze`는 이를 읽어 미관측 출처를 보존한다. 기존 예외 생성·처리 방식과 오프라인 소비자를 검사했다.

호출자 근거의 선택 구조는 `source`, `source_kind=caller_code`, `version`, `method/path`, `field_mapping`(전송 필드 → 입력 필드), `required_inputs`(필수 계약 필드 → 입력 필드), `input_fields`, 선택 `input_types`다. 실제 전송과 코드 매핑, 입력 관측, DTO, 실행 버전이 맞아야 호출자 결함/입력 누락을 구별한다. 문자열 유사성과 DTO만으로 작업 후보를 확정하지 않는다.

제공된 입력을 정상 타입으로 확인해야 하는 호출자 결함은 `input_types`가 미관측이면 보류한다. `source_hash_format`은 선택 사항이며 기본 `raw`다. 예제는 Git checkout의 줄바꿈만 바뀌어도 동일한 코드 근거를 유지하도록 명시적 `lf`(CRLF→LF 후 SHA-256)를 사용한다. 원본 바이트 해시도 출처에 별도로 남기며 의미가 바뀐 코드는 계속 거부한다.

`ProjectProfile`의 필수 값은 ID·서비스·환경·루트다. `code_roots/log_sources/policy_refs`는 빈 tuple, `openapi_path/dto_path/caller_evidence_path/config_path`는 `None`, 버전 관측은 `VersionObservation(method="none", path=None, field="version")`가 기본값이다. 설정 JSON에서도 생략 가능하며 필요한 근거가 없으면 미관측 상태로 남긴다. `LocalLogSource.format`은 `jsonl`이 기본값이다.

반환 구조:

- `compare_contract`: `status`, `complete`, `required_fields/request_fields/missing_fields/unexpected_fields/forbidden_fields`, `type_mismatches`, `unobserved_types`, `similarity_candidates`, `limitations`, `automatic_rename=False`. 중첩 객체·배열도 확인하며 마스킹된 컨테이너의 자식 필드를 만들어내지 않는다.
- `analyze_contract`: 위 구조 + `provenance`, `scope_status`, `versions`(`MATCHED/MISMATCH/UNOBSERVED`, 런타임/코드/계약/DTO/호출자별 값·미관측 구성·충돌), `responsibility`(`UNCONFIRMED/CALLER_DEFECT/INPUT_OMISSION/VERSION_MISMATCH`). `info.version`은 실행 버전으로 사용하지 않는다.
- `read_project_logs`: `project_id/events/complete/sources/limitations`, `execution_authorized=False`. 각 소스의 바이트·관측 수·SHA와 실패/한도 사유를 보존한다. 요청 값은 마스킹하고 관측 가능한 타입은 별도 메타데이터로 전달한다.
- `observe_project_version`: `status=OBSERVED/UNOBSERVED/CONFLICT`, `runtime_version/code_version/source/limitations`, `execution_authorized=False`. 로컬 코드 버전을 실행 버전으로 복사하지 않는다.
- `load_project_contract`: OpenAPI 3.x JSON의 정확한 메서드/경로와 JSON 요청 객체, 문서 내부 `$ref`, DTO/호출자 JSON을 읽는다. 출처별 SHA·코드 버전과 미관측 상태를 제공하며 실행 권한은 항상 없다. 정적 호출자 파일에서 현재 입력 관측을 만들어내지 않는다.

## 소유 범위와 연결 위치

- 구현: `tracebridge/triage.py`, `evidence.py`, 새 `contract_analysis.py`, `project_profile.py`, `project_contracts.py`.
- 자료/검사: 새 `tests/test_parallel_b_*.py`, `tests/fixtures/parallel_b/**`, `examples/parallel_b/**`, 이 문서.
- A 연결: `report_agent.ToolSession.call("get_contract", ...)`에서 선택 사건을 `ProjectEvidenceSource(profile, event_source=selected_source)`로 감싸 조회한다. 계약 부재의 출처까지 필요하면 잡은 `EvidenceError.details`를 전달한다.
- A 연결: `report_intake.triage_report(..., source_adapter=...)`에서 선택 소스에 같은 어댑터를 적용한다. `project_sources`의 구조화 로그 수집은 마스킹 전에 `request_types`, 현재 `input_fields/input_types`, 선택 `caller/contract_context`를 보존해야 한다.
- A 연결: 프로필의 `root/code_roots/log_sources`를 수집·검색에 사용하고, `contract_analysis/responsibility/symptom_status/product_status`를 최종 결과와 저장/화면으로 전달한다. CLI 예시는 `--profile examples/parallel_b/ledger_demo/profile.json`이다.
- 현재 workspace에서 A의 위 어댑터·계약 도구·추가 판정 필드·CLI 연결 코드를 확인했다. **A 전용/종단 검사를 B에서 대신 실행하지 않았으며 연결 완료 판정은 A/메인 담당이다.**
- D 패키징: `examples/parallel_b/**`를 포함해야 고정 합성 회귀 출처와 별도 프로젝트를 재현한다. B는 패키징 파일을 수정하지 않았다.

## 시작 해시

`triage.py`: `B2E1A3942AE1168D449589F93D2D1A1E51B0BB391A90E30000438BF951A91F6C`
`evidence.py`: `30EA50DBAED50F66EE15F98B9202EBC92E74307E6B01E947F90E00417BAC1D62`

등록 정책과 씨드 6개 기준 파일의 시작/종료 해시는 `output/parallel-b/protected-files.json`에 기록했다. **7/7 해시 동일**. B는 원본·기준 파일·다른 세션 소유 파일을 수정하지 않았다. 새 외부 호출·실서비스 접속·배포는 0회다.

## 판정 변형 결과

23개 실제 로컬 합성 변형의 기대/실제·출처·제보/관측·책임·버전·필드 차이는 `output/parallel-b/variant-results.json`과 `.md`에 있다. 전용 검사는 더 세부적인 91개 조건을 검증한다. 합성 결과를 실제 프로젝트 정확도로 확대하지 않는다.

| 변형 | 결과 |
| --- | --- |
| `user_id/userId`, `account_id/accountId`, 다른 필드 이름 + 현재 입력 타입·호출자·DTO·버전 근거 | 같은 구조적 차이, `CALLER_DEFECT / WORK_CANDIDATE`; 자동 rename 없음 |
| 표기 유사성과 DTO만 있음 | 필드 차이 보존, `UNCONFIRMED / INVESTIGATE` |
| 현재 입력 누락 + 입력을 보존하는 호출자 + 같은 버전 | `INPUT_OMISSION / GUIDANCE`; 제품 전체 정상·사용자 과실은 미확정 |
| 정상 타입 입력을 호출자가 누락/다른 키로 전달/다른 타입으로 변환 | `CALLER_DEFECT / WORK_CANDIDATE` |
| 422와 누락만 있음 | `REQUEST_REJECTED` 보존, 책임 미확정·조사 |
| 제보 500 / 실제 422 / 가입 실패 / 호출자 결함 | 보고 상태 불일치와 요청 실패·호출자 결함을 각각 보존 |
| 실제 200/201 또는 500/502 + 계약 차이 | 차이는 보존, 원인 `unknown`, `INVESTIGATE`; 5xx 예외 단서가 있으면 미확정 예외 조사 |
| 계약·DTO·현재 입력 타입·실행 버전 미관측 | 책임 확정 보류 |
| 실행/코드/계약/DTO/호출자 버전 불일치 | `VERSION_MISMATCH / INVESTIGATE` |
| 비식별 값·자식 필드 미관측 | 타입/자식 필드를 추정하지 않음 |
| 임의 DB 컬럼 오류, 실제 관련 마이그레이션 상태 없음 | 미검증 DB 가설·조사 |
| 임의 DB 컬럼 오류 + 관측된 관련 마이그레이션 불일치 | 기존 `migration_missing / WORK_CANDIDATE` 유지; DB 작업자는 추가하지 않음 |
| Agolive 폴더명 없는 `ledger-demo`, 로그에 계약 없음 | 등록 OpenAPI·DTO·호출자 코드·현재 입력·버전을 별도로 조회, 호출자 후보 확인 |

기존 `FixtureEvidenceSource`의 세 시연/회귀 기대값은 모두 유지했다. `examples/parallel_b/fixture-context.json`에 합성 입력·코드 출처·버전을 명시하고 실제 코드 해시와 연결했다. 일반 JSON/프로젝트 소스에서 trace ID로 이 출처를 자동 주입하지 않는다.

## 검사 명령·결과

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp=output/parallel-b/pytest-final --junitxml=output/parallel-b/pytest-results.xml tests/test_parallel_b_contract_analysis.py tests/test_parallel_b_project_sources.py
# 91 passed (1.72s)

.\.venv\Scripts\python.exe -B -m pytest -q -p no:cacheprovider --basetemp=output/parallel-b/pytest-existing-final --junitxml=output/parallel-b/existing-api-results.xml tests/test_triage.py tests/test_incident_inputs.py tests/test_report_intake.py -k 'not repro_templates'
# 21 passed, 1 failed, 1 deselected (2.49s)

git diff --check
# PASS (Git의 LF→CRLF 안내 외 whitespace 오류 없음)
```

최초 B 전용 실행의 파일 기반 검사는 Windows 샌드박스의 pytest 임시 폴더 ACL 접근 오류로 시작하지 못했다. 같은 B 전용 경로만 사용하는 정상 사용자 권한 실행에서 통과했다. 실프로젝트 실행·외부 모델 호출·서비스 접속은 없었다. 기존 `repro_templates` 검사는 공용 `generated` 경로에 쓰기 때문에 이 제한적 호환 검사에서 제외했다. 전체 회귀를 실행하거나 통과로 주장하지 않는다.

남은 기존 기대값 충돌: `tests/test_report_intake.py:18`의 `test_exact_id_routes_observed_validation_and_real_work_differently`는 `examples/report_events.json`의 계약/DTO/422만으로 `GUIDANCE`를 기대한다. 이 파일에는 현재 입력·호출자 코드·실행 버전 출처가 없어 결과는 `INVESTIGATE`다. 같은 테스트의 `contract-001` 후보도 해당 책임 근거가 필요하다. 이 검사의 기대값이나 공용 예제를 B에서 바꾸지 않았다. A/메인은 **합성 시연임을 명시한 검증된 입력/호출자/버전 소스 어댑터**를 해당 카탈로그에 붙여 원래 강한 기대값을 검증해야 한다. 일반 422를 안내로 되돌리는 분기는 만들지 않는다. 다른 기존 접수/날짜/화면 검사에서도 같은 불완전한 출처를 쓰면 연결 보강이 필요하다.

초기 호환 검사의 성공 응답 진단 이름 충돌은 `unknown`을 유지하고 계약 차이를 추가 필드에 보존해 해결했다. 기존 소비자의 반환 필드·호출 방식·부재 예외를 검사했다.

## 실제 자료가 없어 남은 한계

실제 개발 프로젝트의 계약·DTO·입력 수집·실행 SHA·DB 마이그레이션 상태 및 서빙/회복을 검증하지 않았다. 정적 JSON 호출자 매핑은 담당자가 코드 출처·해시·버전과 연결한 관측이며 모든 언어의 코드를 자동 해석하지 않는다. 값 제약/복합 스키마·외부/재귀 참조는 완전 검사가 아니며 보류한다. 로컬 버전 snapshot이 실제 배포에서 수집됐다는 보장은 이번 합성 검사 범위에 없다. A/메인의 코드 고정 뒤 전체 회귀·접수 종단 연결·실제 자료 검증이 남았다.

수정 파일: 기존 `tracebridge/triage.py`, `evidence.py`; 새 `contract_analysis.py`, `project_profile.py`, `project_contracts.py`; 두 `tests/test_parallel_b_*.py`; `examples/parallel_b/README.md`, `fixture-context.json`, `fixture-caller.py`, `ledger_demo/profile.json`, `openapi.json`, `dto.json`, `caller.json`, `version.json`, `events.jsonl`, `src/client.py`; 이 문서. 검사/변형/보존 산출물은 모두 `output/parallel-b`다.
