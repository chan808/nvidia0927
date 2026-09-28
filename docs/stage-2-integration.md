# 접수·판정 통합과 실사건 조사 연결 결과

**후속 상태:** [3단계 사건 기억](stage-3-memory.md)에서 로컬 SQLite 저장·조회·검토·검색·재시작 답변과 합성 검증을 완료했다. 최신 회귀는 157개다. 아래는 2단계 당시의 기록이며 SQLite 미착수 문구도 당시 범위를 뜻한다. 실제 연결/실사건 자료 확보 대기와 수정·평가 단계 미착수는 유지한다.

기준: 2026-09-28. 이번 작업은 [대회용 계획](competition-mvp.md)의 2단계 연결 범위다. **통합 구현과 합성 회귀 검증을 완료했다. 실제 Agolive 실행 로그·배포 연결과 허가된 실사건 검증은 미완료다.** SQLite 사건 기억, 수정 작업자, 비교 평가·제출물 구현은 시작하지 않았다.

통합 기준 87개 통과 뒤 발견된 세 결함을 국소 수정했고, 회귀·경계 검사 23개를 추가해 **110개 통과**를 확인했다. 기존 미커밋·미추적 작업을 보존했으며 새 외부 모델 호출이나 실제 서비스 변경은 수행하지 않았다.

추가로 사건 목록의 선택 관측과 현재 로그 사이의 충돌 보완만 수행했다. 직전 110개를 유지하고 신규 17개를 추가해 **127개 통과**를 확인했다. 전체 구조·판정 엔진을 변경하지 않았으며 실제 자료 확보 대기와 다음 단계 미착수 상태를 유지한다.

## 완료한 동작

- [공통 계약](../tracebridge/report_contract.py)의 선택 입력 `ReportContext`와 결과를 [규칙 접수](../tracebridge/report_intake.py), [제보 조사](../tracebridge/report_agent.py), [화면](../pages/2_Report_Agent.py), [CLI](../scripts/investigate_report.py)가 공유한다. 프로젝트·세션 사건 ID·요청/후보·상관 근거·관측·가설·질문·라우팅·버전 출처·실행 종료 상태를 반환한다.
- 판정은 기존 `triage.analyze`를 재사용하고 라우팅은 `triage.route_verdict` 한 곳에서 정한다. 명확한 관측의 안내와 작업 후보는 모델 0회다. `고쳐줘`는 의도이며 실행 권한이 아니다.
- 등록 파일을 같은 경로에서 조회해 관측된 응답/계약을 판정한다. 지원하는 구조화 로그의 선택적인 계약·DTO·마이그레이션 snapshot은 기존 bundle 스키마로 검사한다. 본문에 적힌 HTTP 숫자나 오류 추측으로 응답 필드를 만들지 않는다.
- 사진만 접수할 수 있고, OCR는 별도 `service_calls.phase=input_processing`으로 기록한다. OCR 문구와 시각 모델 설명은 `report_clues`이며 서버 원인의 관측이 아니다. 시각 모델 또는 조사 모델이 만든 ID는 검색 단서만 될 수 있고 정확 상관에 사용하지 않는다.
- 화면의 **같은 사건에 답변 반영**과 CLI의 반복 가능한 `--answer`가 같은 `incident_id`를 유지한다. 매 답변에서 자료를 다시 읽고 이전 관측은 `history`에 남긴다. 과거 근거 ID는 현재 가설의 지지 근거가 아니다. 프로젝트·등록 경로·범위 설정이 바뀌면 새 접수를 요구한다.
- 화면의 **조사 시작**은 새 사건 접수이며 시작할 때 이전 활성 결과를 지운다. 새 입력이 실패하면 오류만 표시한다. 같은 사건의 후속 답변 실패에서는 기존 결과를 유지하며, 다음 새 조사 성공은 새 사건 ID와 결과를 표시한다.
- 현재 사건의 반대 로그 근거 ID를 검사해 이전 가설을 `REVISED/REJECTED`로 기록하는 합성 사례를 검증했다. 반증 자료가 확인되지 않거나 요청 연결이 바뀌면 `NOT_REVALIDATED`로 남긴다. 이는 모델 제안과 현재 참조의 검사이며 의미적 원인 검증을 대신하지 않는다.

## 주요 변경 파일

| 파일 | 핵심 변경 |
| --- | --- |
| `tracebridge/report_contract.py` | 선택적 입력, 공통 결과, 한국어 날짜/환경 답변, 실행 상태 계약 |
| `tracebridge/report_intake.py`, `tracebridge/triage.py` | 등록 관측 adapter, 범위/후보 상관, 기존 규칙과 단일 라우팅 정책. 이번 보완은 `report_intake.py`의 공통 충돌 함수와 선택 사건/로그 대조 |
| `tracebridge/report_agent.py` | 규칙 빠른 경로, 제한 조사, OCR/시각 단서 분리, 세션 후속 답변·반증·기록 보존. 이번 보완은 충돌 출처 기록·보류와 양쪽 관측 보존 |
| `tracebridge/project_sources.py` | 구조화 로그/MDC 범위 검사, 요청 값/비밀 제외, 선택 실행 Docker label의 버전 출처 |
| `tracebridge/deadline.py`, `tracebridge/nemo_ocr.py` | 공유 deadline의 남은 timeout, OCR 통신 시간 초과의 구분 |
| `pages/2_Report_Agent.py` | 네 라우팅·실행 상태 표시, 쉬운 후속 답변 form, 개발자 범위/출처 기록 |
| `scripts/investigate_report.py`, `scripts/triage_report.py` | 공통 접수 CLI의 사건 목록/등록 로그/후속 답변과 선택 범위 |
| `tests/test_intake_integration.py`, `tests/test_report_agent.py` | 새 통합 경계 검사와 강화된 미확인 상관 기대값 |
| `tests/test_intake_regressions.py` | 합산 한도/충돌, 새 접수/후속 실패, 시간 예산 23개와 이번 사건 목록/현재 로그 비교 17개 |
| `examples/scoped_agolive.log`, `.env.example`, 관련 문서 | 합성 구조화 재현 자료, 등록 설정, 현재/과거 검증 구분 |

## 라우팅과 조사 실행 상태

| 결과 | 의미 |
| --- | --- |
| `GUIDANCE` | 정확 연결된 관측의 필수 입력 누락/검증 응답 안내. 제보자의 책임은 미확정 |
| `WORK_CANDIDATE` | 정확 연결된 요청·계약/DTO 또는 마이그레이션의 관측 불일치. 수정 실행이 아닌 담당자 검토 후보 |
| `INVESTIGATE` | 한 범위 후보의 연결 확인, 또는 정확 연결된 서버 오류 등 추가 진단 |
| `REQUEST_CONTEXT` | 0/복수 후보, 관측 충돌, 합산 한도 초과·중단 등 불완전한 로그, 자료 범위 미확인. 문구 유사성이나 모델 가설로 우회하지 않음 |

`run_status`는 위 라우팅과 독립적으로 `COMPLETED`, `WAITING_CONTEXT`, `PARTIAL_FAILURE`, `TIMED_OUT`, `BUDGET_EXHAUSTED`를 구분한다. `COMPLETED`는 이번 제한된 판정/조사의 종료이며 원인 확인·사건 해결이 아니다. 실패/한도 도달 시에도 이미 확보한 관측과 이전 세션 기록을 보존한다.

모델은 한 조사 실행에 최대 4회(시각 해석 포함), 읽기 도구는 자동 상관·버전 조회를 포함해 최대 6회다. OCR는 이 모델 횟수와 별도다. 후속 답변은 새로 요청한 제한된 조사 실행이며 `session_model_calls`로 누적을 표시한다. 후속 답변은 최대 6회다.

기본 시간 예산은 90초(`max_seconds`: 1~180초)다. 접수 시작의 monotonic 시각으로 만든 공유 deadline을 선택 인자로 전달한다. OCR HTTP는 `min(35초, 남은 시간)`, 시각/조사 모델은 `min(45초, 남은 시간)`을 사용하고 모델 자동 재시도는 0회다. Git 조회(5초)/상태(8초), Docker 로그(15초), 실행 컨테이너 조회·inspect(각 8초)도 각 프로세스 시작 직전에 남은 시간을 다시 계산한다. 예산이 만료된 뒤 새 HTTP/프로세스/읽기 도구 호출을 시작하지 않는다. 로그 파일 청크·범위 검사 행·코드 파일/행 경계에서도 만료를 확인한다. 소스별 관측을 먼저 보존하므로 다음 Docker 호출이나 범위 검사에서 시간 초과가 나도 확보한 관측은 남는다. `timeout_reasons`에 OCR HTTP·모델·Git/Docker·소스 수집·전체 deadline의 원인을 기록하며 후속 오류가 기존 시간 초과 기록을 덮어쓰지 않는다.

이 예산은 전체 함수의 엄격한 실시간 종료 보장이 아니다. HTTP 클라이언트의 timeout은 연결/읽기/쓰기 등 통신 단계별 대기 제한이며 단계의 합계나 데이터를 계속 보내는 응답의 전체 시간을 강제로 끊지 않는다. 로컬 파일 stat/읽기·디렉터리 탐색, 초기 저장소/사건 파일 처리, 이미지 변환 등 동기 작업도 진행 중에 강제 취소할 수 없다. 프로세스 시작/정리 시간은 timeout보다 더 걸릴 수 있다. 해당 작업 뒤 또는 다음 경계에서 만료를 확인해 추가 호출을 막고 결과를 `TIMED_OUT`으로 남긴다. 새 실행 프레임워크나 배경 작업은 추가하지 않았다.

`SUPPORTED_HYPOTHESIS`는 범위가 맞고 정확 연결된 로그와 코드가 가설을 지지한다는 상태다. 로컬/실행 SHA의 일치와 검색 소스 변경 없음도 검사하지만 실행 코드의 동일성, 원인 확정, 재현, 수정 검증을 보장하지 않는다. 실행 버전 미확인은 `LOG_CANDIDATE/CODE_ONLY`, 관측된 버전 불일치나 반대 인용은 `CONTESTED_HYPOTHESIS`로 낮춘다. `cause_confirmed`, `fix_applied`, `fix_verified`는 이번 경로에서 항상 false다.

## 등록 로그와 버전 출처

`TRACEBRIDGE_LOG_FILE` 또는 `--logs-file`로 소유자가 지정한 일반 `.log/.txt/.jsonl` 파일을 읽는다. 마지막 300 KB의 잘린 첫 줄은 버리고 최대 5천 줄을 범위 검사한다. 소스별 규칙 입력은 최대 100개이며, 이 소스들의 **합산 관측 한도도 100개**다. 전달된 관측은 한도로 잘라 판정하지 않고 모두 충돌 검사한다. 같은 ID의 응답·서비스·환경·메서드/경로·요청/작업·버전·계약/DTO/마이그레이션이 상충하면 해당 사건을 제외하고 보류 이유를 남긴다. 충돌 없는 중복도 합산 수에 포함되며 합계가 100개를 넘으면 보류한다. 모델/화면의 로그 최대 20줄 선택은 판정의 완전성과 별개다. 출력에는 요청 값 대신 필드 이름을 남기고 흔한 비밀·개인정보 형태를 가린다. 모델은 경로를 정할 수 없다.

`log_scope.aggregate`는 전달 관측 수·합산 한도·`complete`·충돌 ID/필드를 기록한다. 소스별 100개 한도 초과, 합산 100개 초과, 조회 실패, 읽기 전 만료나 수집 중 만료는 불완전 상태로 남긴다. 이 목록으로 `GUIDANCE/WORK_CANDIDATE`를 확정하지 않는다. 별도 사건 snapshot에서 이미 확보한 서버 오류는 로그 소스 실패와 함께 `INVESTIGATE/PARTIAL_FAILURE`로 유지할 수 있으며, 이는 안내·작업 후보 확정이나 원인 확인이 아니다. `complete`는 위 읽기 범위 안에서 전달된 판정 목록의 완전성이고 원본 파일 전체나 운영 로그 전체의 수집 완료를 뜻하지 않는다.

명시적인 사건 목록이 있어도 선택된 사건을 현재의 범위 확인된 로그와 대조한다. 기존 로그 간 검사와 같은 `observation_conflicts`를 재사용하며, 원본 범위 확인 기록을 보존해 표시용 선별이나 중복 병합으로 반대 관측이 사라지지 않게 한다. 같은 프로젝트·요청 ID·서비스(등록 별칭 포함)·환경이면서 **선택 사건의 시각 ±5분**인 기록만 비교한다. 입력 시각을 기준으로 `VERIFIED`인 로그라도 선택 사건과 다른 서비스·환경 또는 시각 범위이면 이 사건의 충돌 근거로 섞지 않는다.

양쪽에 알려진 응답·메서드/경로·작업·버전·요청 필드·계약/DTO/마이그레이션이 상충하면 `REQUEST_CONTEXT/NEEDS_CONTEXT`로 보류한다. `log_scope.aggregate.conflicts[].sources/fields`에 충돌한 출처와 필드를 기록하고, 단일 `observed_status`는 null, `claim_status`는 `UNVERIFIABLE`, `claim_items`는 빈 목록으로 남긴다. 사건 목록의 규칙 관측과 현재 `VERIFIED` 로그는 출처를 유지해 보존한다. `complete=true`와 충돌 기록이 함께 있을 수 있다. 이는 수집 목록이 완전하지만 관측이 서로 상충한다는 뜻이다. 목록 snapshot은 로그의 100개 수에 추가하지 않는다.

누락/null 값과 `[VALUE]`, `[REDACTED]` 등의 비식별화 표시는 알려진 값의 반대 증거가 아니다. 사전은 양쪽에 있는 알려진 값만 비교하고, 로그에 캡처된 요청 필드 이름은 값이 비식별화되어도 비교한다. 로그 입력 처리에서 요청값을 비식별화하므로 그 실제 값의 일치/불일치는 검증할 수 없다. 이는 알려진 필드의 충돌 검사이며 의미적 원인 검증이나 원본 자료의 진위 확인이 아니다.

발생 시각 ±5분·서비스·환경·프로젝트 연결을 검사한다. 로그에 필요한 값이 없으면 `UNVERIFIED/NOT_OBSERVED`로 남겨 판정 관측으로 쓰지 않는다. 서비스·환경은 관리자 등록 범위를 사용할 수 있으나 `REGISTERED_SOURCE`로 표시해 로그 필드 관측과 구분한다. 시간대 없는 파일 로그에는 명시적인 `TRACEBRIDGE_LOG_TIMEZONE=+09:00` 또는 `UTC`가 필요하다. 제보/모델의 서비스·환경·시각 추측으로 로그의 누락 필드를 채우지 않는다. 관측 응답이 없으면 상태는 null이며 오류 문구에서 500을 추정하지 않는다.

JSON의 최상위/`trace` 식별 필드, Agolive MDC 접두사만 요청 연결에 사용한다. 메시지 속 ID, 다른 요청의 스택, 다른 범위 로그는 제외한다. ID가 없어 시간·서비스·환경이 맞는 한 후보를 찾아도 `CONTEXT_CANDIDATE`로 조사한다. 복수 후보나 재사용된 ID의 상충 관측은 보류한다. 알려진 작업명과 간단한 한국어 날짜·시각·환경만 해석하며 자유로운 자연어 전체의 구조화는 보장하지 않는다.

Docker는 명시적으로 선택한 로컬 Compose의 기존 `api/realtime`만 조회하며 `--timestamps`, `--since`, `--tail`을 사용한다. Docker 시각과 Spring MDC/스택을 함께 검사한다. 배포 버전은 허용된 서비스의 실행 컨테이너가 한 개일 때 `State.Running`과 `org.opencontainers.image.revision` label만 읽는다. 전체 container env나 자격 증명은 수집하지 않는다.

`version_provenance`는 다음을 분리한다.

- `local_head`: 로컬 Git HEAD와 검색 대상 소스의 변경 여부.
- `configured`: `TRACEBRIDGE_DEPLOYED_SHA`의 수동 설정. 이 설정으로 `deployment_observed`가 true가 되지 않는다.
- `runtime`: 실행 컨테이너에서 실제 읽은 선언된 revision label과 조회 시각/출처. 이 label은 바이너리/원인 검증이 아니다. 환경 연결은 등록 범위이며 실행 서비스가 환경을 직접 증명했다는 뜻이 아니다.
- `event_snapshot`: 제공 사건/로그에 기록된 버전 문자열. 현재 실행 서비스의 관측과 별개다.

## 검증과 재현

전체 회귀 검사는 기존 50개와 통합·경계 검사 37개, 앞선 결함 검사 23개의 기준 110개와 이번 사건 목록/현재 로그 비교 검사 17개를 합쳐 **127개 통과**했다(19.49초). 이번 보완에서 기존 110개의 기대값은 변경하지 않았다. `git diff --check`는 종료 코드 0이었다. 최종 정상 권한 실행 결과는 아래 명령으로 확인했다. 제한된 Windows 실행의 pytest 임시 폴더 접근 실패는 기능 실패와 구분했다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
# 이번 결함만 재현/검증
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_intake_regressions.py -k catalog
git diff --check
```

앞선 세 결함은 수정 전 검사 16개에서 14개 실패·2개 통과로 재현했고, 경계를 보강한 23개와 관련 접수/통합/OCR 77개를 통과했다. 이번 사건 목록/로그 비교 검사 17개는 수정 전 4개 실패·13개 통과로 응답·경로·DTO 충돌 누락을 재현했다. 수정 후 관련 회귀/규칙 접수/통합 묶음 85개와 최종 전체 127개가 통과했다. 추가 검사는 합성 자료이며 새 외부 모델 호출·긴 sleep은 없다.

| 결함 | 원인과 수정 | 확인한 회귀 |
| --- | --- | --- |
| 여러 소스의 충돌 관측 누락 | 합산 후 `events[:100]`이 뒤의 500을 버렸다. 합산 수/완전성을 표시하고 전달 관측 전체의 필드 충돌을 검사한다. 한도·충돌·조회 실패는 안내/작업 후보 확정을 막는다. | 등록 422×100+제공 500×1, 한도 안의 소스 간 응답/서비스/환경/요청 충돌, 충돌 없는 합산 초과, 정상 안내·작업 후보 |
| 새 제보 실패 때 이전 결과 노출 | 새 버튼의 예외 경로가 기존 `report_agent_result`를 남겼다. 새 시작에서 활성 결과를 먼저 비우고, 같은 사건 답변은 성공한 뒤에만 교체한다. | 빈 새 제보의 이전 안내 제거, 다음 성공의 새 사건 ID/작업 후보, 빈 후속 답변의 기존 결과 보존 |
| OCR/읽기 작업의 고정 timeout | 호출 뒤 종료 상태만 검사하고 OCR 35초·프로세스별 고정 대기를 사용했다. 공유 deadline의 남은 timeout을 각 호출에 전달하고 소스별 관측을 먼저 보존한다. | 사진 1초 예산과 준비 후 0.4초 OCR 전달, 만료 후 OCR/프로세스/모델 차단, Git·Docker ps/inspect의 남은 시간, Docker 시간 초과/범위 수집 중단의 관측 보존, 모델 0.4초, 앞선 OCR 시간 초과 원인의 보존 |
| 사건 목록과 현재 로그의 충돌 누락 | `rule_result`가 명시 목록의 판정과 로그 간 충돌 검사만 사용했다. 선택 목록 사건과 현재 로그를 같은 필드 충돌 함수로 대조하고 출처/필드를 기록해 보류한다. | 목록 422+로그 500의 안내/작업 후보 보류, 일치 시 기존 경로 유지, 응답/선택 필드 누락, 다른 ID·환경·서비스·시각·프로젝트 제외, 비식별화 요청값 제외, 경로/DTO 충돌 |

요청한 `claim-003` 재현을 `investigate_submission`에 직접 전달해 확인했다. 결과는 `NEEDS_CONTEXT / REQUEST_CONTEXT / WAITING_CONTEXT`, `observed_status=null`, `claim_status=UNVERIFIABLE`, `claim_items=[]`, 모델 0회였다. `aggregate.complete=true`, 충돌 ID `claim-003`, 필드 `response_status`, 출처 `local_event_catalog/provided-log`를 기록했다. 목록의 HTTP 422 관측과 로그의 HTTP 500 관측은 모두 보존됐다.

경로를 생략한 `pytest`의 전체 자동 수집도 실행했으나 기존 `generated/`의 수정 전 재현용 두 테스트가 의도한 계약/마이그레이션 실패를 냈다(회귀 검사 103개 통과 당시 2개 실패). 이 파일은 수정하지 않았다. 기준 회귀 범위는 문서와 README의 `tests`다.

[통합 검사](../tests/test_intake_integration.py)는 같은 `investigate_submission`, 화면, CLI에서 500 제보/실제 422 안내, 관측 계약 불일치 작업 후보, 서버 오류 추가 조사, 0/복수 후보 보류, 사진과 쉬운 후속 답변, 다른 로그 범위·모델 실패·배포 불일치, 반대 관측의 가설 폐기를 검사한다. 모델·OCR·실행 Docker는 가짜 클라이언트/결과로 경계를 검사했으며 실제 모델 품질·운영 연결의 증거는 아니다. 화면 검사는 글/후속 답변이며 실제 사진 업로드+API의 새 화면 종단 검증은 수행하지 않았다.

같은 접수 CLI의 모델 0회 재현:

```powershell
# 명시 제공한 사건 목록: GUIDANCE / WORK_CANDIDATE
.\.venv\Scripts\python.exe -m scripts.investigate_report --repo tests/fixtures/agolive_repo --events examples/report_events.json --report '500 requestId=claim-003'
.\.venv\Scripts\python.exe -m scripts.investigate_report --repo tests/fixtures/agolive_repo --events examples/report_events.json --report '500 requestId=contract-001'

# 등록 파일의 합성 관측: GUIDANCE / WORK_CANDIDATE / INVESTIGATE
.\.venv\Scripts\python.exe -m scripts.investigate_report --repo tests/fixtures/agolive_repo --logs-file examples/scoped_agolive.log --report '500 requestId=intake-guidance' --environment dev --service backend --occurred-at '2026-09-28T10:00:00+09:00'
.\.venv\Scripts\python.exe -m scripts.investigate_report --repo tests/fixtures/agolive_repo --logs-file examples/scoped_agolive.log --report '500 requestId=intake-contract' --environment dev --service backend --occurred-at '2026-09-28T10:10:00+09:00'
.\.venv\Scripts\python.exe -m scripts.investigate_report --repo tests/fixtures/agolive_repo --logs-file examples/scoped_agolive.log --report '500 requestId=intake-server' --environment dev --service backend --occurred-at '2026-09-28T10:20:00+09:00'

# 복수 후보: REQUEST_CONTEXT. 기술 항목 없이도 접수 가능
.\.venv\Scripts\python.exe -m scripts.investigate_report --repo tests/fixtures/agolive_repo --events examples/report_events.json --report '회원가입이 안 돼요' --answer '개발 환경에서 2026-09-28 오전 10시 1분쯤이었어요'

# 이전 합성 사진을 재사용한 오프라인 접수→같은 사건 후속 답변
.\.venv\Scripts\python.exe -m scripts.investigate_report --repo tests/fixtures/agolive_repo --logs-file examples/scoped_agolive.log --image output/validation/loading_screen.png --answer '개발 환경의 방 입장에서 ROOM_FULL이 떴어요. 2026-09-28 오전 10시 30분쯤이에요'
```

`examples/scoped_agolive.log`, 사건 목록, 테스트 저장소와 기존 사진은 모두 합성 자료다. 사진 파일은 이전 로컬 검증 기록이며 새 checkout에서는 없을 수 있다. 현재 작업의 오프라인 재현 JSON은 `output/validation/stage2-*.json`에 보관할 수 있으며 Git/제출에 포함하지 않는다. 실제 사진 OCR·Super·Omni 성공 기록은 [기존 NVIDIA 기록](nvidia-validation.md)의 네 결과 파일을 확인해 재사용했다. 이번 작업의 새로운 외부 API 호출은 **0회**다.

위 CLI를 실제로 실행해 다음 로컬 기록을 확인했다. 모두 모델 0회이며 실제 배포 관측은 false다.

| 로컬 기록 | 결과 |
| --- | --- |
| `stage2-guidance.json` | 보고 500/관측 422 → `EXACT_ID`, `GUIDANCE`, `COMPLETED` |
| `stage2-contract.json` | 관측 필드/계약 불일치 → `EXACT_ID`, `WORK_CANDIDATE`, `COMPLETED` |
| `stage2-server.json` | 관측 500/백엔드 예외 → `EXACT_ID`, `INVESTIGATE`, `WAITING_CONTEXT` |
| `stage2-ambiguous.json` | 쉬운 답변 후에도 두 후보 → 같은 사건 revision 2, `REQUEST_CONTEXT` |
| `stage2-photo-followup.json` | 사진 접수 후 쉬운 답변 → 같은 사건 revision 2, `CONTEXT_CANDIDATE`, `INVESTIGATE`, 관측 409 |

## 실제 Agolive와 다음 확인 사항

직전 통합 작업에서 실제 `C:\Users\freetime\Desktop\projects\agolive`의 HEAD는 `a25116f57c876d74c072b1151ee5a87494e2b11a`였다. 기존 `dev_run.bat` 변경을 보존했고 원본 코드·병합·배포는 수행하지 않았다. backend `logback-spring.xml`은 콘솔 appender만 사용했고 등록 로그 설정/파일이 없으며 Docker daemon 연결도 실패했다. 로컬 코드 검색은 가능했지만 실행 중인 배포 SHA를 관측하지 못했다. 이번 국소 수정에서 실제 환경을 다시 조회하거나 실사건을 조사하지 않았으며 자료 확보 대기를 유지한다.

직전 통합 경로의 실제 저장소 오프라인 확인은 `ROOM_FULL` 코드 근거 3개·로그 0개·모델 0회, `REQUEST_CONTEXT/WAITING_CONTEXT`, `log_scope.connected=false`, `deployment_observed=false`였다. 실제 코드/민감 로그 원문은 재현 JSON과 제출 자료로 내보내지 않았다. 당시 `docker ps` 재확인도 daemon 연결 실패였다. 이는 코드 접근 확인이며 실제 사건 검증이 아니다.

허가된 로그 파일 또는 접근 가능한 로컬 Docker 연결, 제보의 화면·동작·대략적인 시각·환경을 요청했다. 이 자료를 받지 못해 **실사건 조사는 실행하지 않았다**. 현재의 `log_scope.connected` 합성 성공을 실제 운영 연결로 집계하지 않는다.

메인 세션은 다음 단계 전에 (1) 읽기 허가된 실제 로그 소스와 서비스/환경/시간대 등록, (2) 실제 실행 버전 출처, (3) 한 사건의 화면·동작·시각과 요청 연결, (4) 사건 조사 결과의 관측/가설/미확인 항목을 확인해야 한다. 실제 서비스의 revision label이 없으면 배포는 미확인으로 유지한다. 2단계의 실자료 게이트가 해소되기 전에는 합성 검증만으로 실프로젝트 조사 전체를 완료했다고 기록하지 않는다.
