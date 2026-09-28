# B 공통 판정·로컬 프로젝트 예시

모두 합성 자료다. `ledger_demo`는 Agolive 폴더명과 무관하며 로그에는 계약/DTO snapshot이 없다. 등록 OpenAPI, DTO, 현재 입력 필드, 해시가 붙은 호출자 코드, 버전 snapshot을 각각 읽는다. 이 설정은 실행 권한을 주지 않는다.

```python
from tracebridge.evidence import ProjectEvidenceSource
from tracebridge.project_profile import load_project_profile
from tracebridge.triage import analyze, route_verdict

profile = load_project_profile("examples/parallel_b/ledger_demo/profile.json")
source = ProjectEvidenceSource(profile)
verdict = analyze("ledger-001", "계정 생성이 안 돼요. HTTP 500", source=source)
print(verdict["contract_analysis"]["missing_fields"])  # ["accountId"]
print(verdict["responsibility"]["status"])  # CALLER_DEFECT
print(route_verdict(verdict, "EXACT_ID")["route"])  # WORK_CANDIDATE
```

`account_id/accountId`는 표기 후보일 뿐이다. 실제 입력과 호출자 매핑·DTO·버전이 함께 일치하기 때문에 호출자 결함을 조사 대상으로 확인한다. 아직 수정안 검증이나 원본 적용은 없다. 등록 정책이 없으므로 실행 권한도 없다.

OpenAPI는 3.x JSON의 정확한 메서드/경로 및 JSON 요청 객체, 문서 내부 `$ref`를 지원한다. 외부 참조·재귀 참조·복합/값 제약을 완전히 검사하지 않으며 보류 사유로 남긴다. `info.version`은 API 버전이고 실행 버전과 비교하지 않는다. 비교용 코드 버전은 `x-code-version`/DTO·호출자의 `code_version`에 기록한다. 버전 미관측/불일치에서는 책임 확정을 보류한다.

호출자 `field_mapping`은 전송 필드에서 입력 필드로의 매핑이고 `required_inputs`는 계약의 필수 필드에서 입력 필드로의 대응이다. 현재 `input_fields/input_types`는 선택 사건 로그에만 기록하며 정적 호출자 파일에서 가져오지 않는다. 등록 호출자 파일의 `source_sha256`이 실제 소스와 다르면 해당 근거를 사용하지 않는다.

`source_hash_format`의 기본값은 원본 바이트 해시 `raw`다. 이 예제와 합성 회귀 출처는 명시적으로 `lf`를 등록해 CRLF→LF 후 해시를 비교한다. 내용 변경은 계속 거부하고 관측한 원본 바이트 해시도 따로 반환한다.

`fixture-context.json`은 기존 세 합성 회귀 사례의 출처와 입력 관측을 보강한다. 일반 프로젝트나 JSON bundle에 이 판정을 이식하지 않는다. `fixture-caller.py`는 기존 입력 누락 사례의 합성 수동 직렬화 출처이며 실제 사용자 관측을 뜻하지 않는다.
