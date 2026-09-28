# 세션 B — 공통 판정·프로젝트 근거 인계

상태: API 초안 기록 후 구현 중. 기준 HEAD `34ce50b0cf6f214726e64a3fbee1f4195c397d4e`. 시작 시 다수의 staged/unstaged 변경이 있었고 B 소유 기존 파일 두 개에는 변경이 없었다. 다른 세션 파일·등록 씨드·정책을 보존한다.

## 공개 API와 기본값 (A 연결 준비용)

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

설정은 로컬 JSON 한 개다. 프로젝트 ID, 서비스/환경, 설정 기준 루트와 코드 루트, JSON/JSONL 로그 소스, 별도 OpenAPI JSON 경로, 선택 DTO/호출자 근거 경로, 버전 관측(`json_file`/`log_field`/`none`), 등록 정책 참조를 담는다. 경로는 설정 루트 안의 로컬 자료만 허용하고 URL·임의 명령·권한 설정은 받지 않는다. 정책 참조는 조회용 식별자이며 실행 허가가 아니다.

계약 결과는 기존 `method/path/openapi/backend_dto` 키에 `status`, `provenance`, `versions`, 선택 `caller`, `limitations`를 추가한다. 계약 부재는 `openapi=None`, `status=UNOBSERVED`로 전달한다. 로그에는 계약 snapshot이 없어도 된다. `ProjectEvidenceSource(..., event_source=...)`로 A의 이미 범위가 확인된 사건 소스를 감싸 별도 계약을 읽을 수 있다. 기본 `event_source=None`에서는 등록 로컬 로그를 읽으며 불완전/충돌이면 단일 사건 확정을 막는다.

호출자 근거의 선택 구조는 `source`, `source_kind=caller_code`, `version`, `method/path`, `field_mapping`(전송 필드 → 입력 필드), `required_inputs`(필수 계약 필드 → 입력 필드), `input_fields`, 선택 `input_types`다. 실제 전송과 코드 매핑, 입력 관측, DTO, 실행 버전이 맞아야 호출자 결함/입력 누락을 구별한다. 문자열 유사성과 DTO만으로 작업 후보를 확정하지 않는다.

## 소유 범위와 연결 위치

- 구현: `tracebridge/triage.py`, `evidence.py`, 새 `contract_analysis.py`, `project_profile.py`, `project_contracts.py`.
- 자료/검사: 새 `tests/test_parallel_b_*.py`, `tests/fixtures/parallel_b/**`, `examples/parallel_b/**`, 이 문서.
- A 연결: `report_agent.py` 도구 등록에 별도 계약 조회, `report_intake.py`의 선택된 `EvidenceSource`를 위 어댑터로 감싸기, `project_sources.py`의 프로젝트 루트/소스 설정에서 `ProjectProfile` 소비. 화면·CLI는 A 소유다.
- 최종 결과/저장으로 B의 추가 판정 필드를 전달하는 작업은 A/메인이 담당한다.

## 시작 해시

`triage.py`: `B2E1A3942AE1168D449589F93D2D1A1E51B0BB391A90E30000438BF951A91F6C`
`evidence.py`: `30EA50DBAED50F66EE15F98B9202EBC92E74307E6B01E947F90E00417BAC1D62`

등록 정책과 씨드 6개 기준 파일의 시작/종료 해시는 `output/parallel-b`에 기록한다. 새 외부 호출·실서비스 접속·원본 수정은 하지 않는다.

## 검사와 잔여 사항

구현 완료 뒤 B 전용 검사 명령·변형 결과·기존 기대값과의 충돌·최종 연결 상태를 이곳에 갱신한다. 공유 파일 편집 중 전체 회귀 통과를 주장하지 않는다. 실제 계약/실행 버전/마이그레이션 관측이 없는 사건은 추가 조사 상태로 남긴다.
