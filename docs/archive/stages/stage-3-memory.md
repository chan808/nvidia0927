# SQLite 사건 기록·검토·유사 사건 검색

> 과거 기록입니다. 당시 계획·검사·제약을 보존하며, 현재 상태는 [구현 상태](../../current-state.md)를 기준으로 확인하세요.

기준: 2026-09-28. [대회용 최소 구현](../plans/competition-mvp.md)의 **3단계 로컬 구현과 합성 검증을 완료했다.** 접수/후속 답변 → 저장 → 재연결 후 조회 → 담당자 카드 검토 → 다음 제보의 검색·현재 재확인을 연결했다. 실제 Agolive 로그·배포 연결과 실사건 검증은 자료 확보 대기다. 수정 작업자, 벡터 DB, 병합·배포, 비교 평가·제출 작업은 수행하지 않았다.

## 저장 구조와 범위

[incident_memory.py](../../../tracebridge/incident_memory.py)는 표준 `sqlite3`와 FTS5를 사용한다. 확인한 Python 번들 SQLite는 3.45.3이며 FTS5와 JSON1이 동작했다. ORM·새 API 서버·작업 큐는 없다.

| 저장 대상 | 구조·역할 |
| --- | --- |
| `incidents` | `(project_id, incident_id)`별 최신 `run_id/revision` 투영과 생성/갱신 UTC 시각 |
| `runs` | 기존 `run_id`, 사건 ID, `revision`, 라우팅·종료 상태·시각, 입력 내용 해시, 최소 `record_json`. 같은 사건의 실행을 각각 보존 |
| `cards` | 실행당 초안 1개. `card_id=run_id`, 출처 실행, 지문, 짧은 증상/판정/다음 확인, 사실 검증 상태, 카드 검토 상태·검토 이력 |
| `card_search` | 승인/수정 검토된 카드의 FTS5 인덱스. 검토 변경과 같은 트랜잭션에서 갱신 |

`record_json`에서 `observations`, `report_clues`, `evidence`, `hypotheses`, `version_provenance`, `steps/service_calls/model_trace/usage`, 사실 검증 플래그와 `memory_search`를 별도 필드로 구분한다. 전체 제품 자료 모델의 객체마다 테이블을 만들지 않았다. `cause_confirmed/fix_applied/fix_verified`는 현재 조사 경로에서 모두 false다.

기본 DB는 **workspace의 `output/tracebridge/incidents.sqlite3`**다. `TRACEBRIDGE_DB_PATH` 또는 CLI `--db`로 바꾼다. 상대 경로는 workspace 기준이며 파일 확장자는 `.db/.sqlite/.sqlite3`를 사용한다. 재시작 시 사라지는 `:memory:` 설정은 거절한다. DB·journal/WAL/SHM·백업·임시 파일은 Git 제외 규칙과 패키지 필터에서 제외한다. 패키지 필터는 이름을 바꾼 SQLite 파일도 파일 헤더로 제외한다. 이번 패키지 코드 변경은 이 제외 검사에 한정했다.

저장은 사건·실행·초안을 한 트랜잭션으로 처리한다. 같은 결과의 재저장은 `ALREADY_SAVED`이며 카드 검토를 초기화하지 않는다. `persistence` 표시를 제외한 입력 전체의 SHA-256을 비교하므로 버린 입력 필드가 달라도 같은 `run_id`를 조용히 덮어쓰지 않는다. 다른 내용의 같은 실행 ID나 다른 실행의 같은 사건 revision은 `RunConflict`다. 실패한 트랜잭션은 최신 실행 투영도 바꾸지 않는다.

근거 ID는 **`(run_id, evidence_id)`**로 참조한다. 저장된 가설에는 실행이 붙은 지지/반대 참조가 있으며 `get_evidence(project_id, run_id, evidence_id)`로 조회한다. 모델에 전달하는 과거 참조는 `historical:<run_id>:<id>`다. 현재 실행의 `L1/C1/R1`과 합치거나 현재 observations/evidence에 추가하지 않는다.

원본 제보/답변, 요청 본문·값, 사진 바이트, 로그/코드 원문을 DB에 자동 보관하지 않는다. 규칙 관측은 비식별화된 짧은 사실, 로그·코드·사진 단서는 출처/범위/단서 유형·해시와 오류 지문을 남긴다. 사진 해시·OCR 신뢰도와 서비스 호출 메타데이터는 남기며 endpoint는 해시로 보관한다. `symptom_summary`는 관측 작업/경로·상태·오류에서 구성한다. 자유문 요약·가설·검토 문구의 비식별화는 패턴 기반이며 임의 민감정보 탐지나 운영 보관/삭제 정책은 구현하지 않았다.

현재 화면과 두 접수 CLI, `investigate_submission/follow_up_submission`이 저장 경로다. 내부 `triage_report`는 순수 판정기로 유지한다. 원래 합성 데모/NAT와 별도 Agolive 조사 페이지 전체를 영속화한 것은 아니다.

## 담당자 검토와 사실 검증

| 카드 검토 상태 | 재사용 검색 |
| --- | --- |
| `PENDING` | 실행 저장 시 만드는 미검토 초안. 제외 |
| `APPROVED` | 담당자가 승인한 안내/조사 단서. 포함 |
| `EDITED` | 담당자가 내용을 수정하면서 검토 완료한 카드. 포함 |
| `REJECTED` | 반려 기록·검토 이력은 보존. 제외 |

`edit`는 `symptom/finding/next_action`의 짧은 문구만 바꾼다. 원 실행·지문·버전·가설 검증·수정 검증을 바꾸지 않는다. 승인·수정·반려는 검토자 표시·시각·내용 변경·메모를 남기고 인덱스를 즉시 갱신한다. 검토자 표시는 로컬 감사용 문자열이며 인증된 신원을 뜻하지 않는다.

`card_kind=GUIDANCE`는 정확 연결된 정상 완료 안내 사례다. 다른 실행은 `UNCONFIRMED` 조사 단서다. 중단·충돌·지지된 가설도 해결 카드로 자동 승격하지 않는다. 검토된 미확정 카드는 검색할 수 있지만 미확정 표시와 원 검증 상태를 유지하고 검증 상태에 따른 해결책 우대는 없다. 정확 검색의 동점에서는 안내 사례를 먼저 둔다. `COMPLETED`는 제한된 판정/조사의 종료이며 사건 해결·수정 성공이 아니다.

## 검색과 현재 재확인

같은 프로젝트의 오류 코드·경로·예외·정규화 스택 해시를 먼저 정확 조회한다. 현재 스택 지문은 JVM의 `at 함수(파일:행)` 형태를 처리하고 행 번호를 제거한다. 경로의 query/fragment와 흔한 숫자/UUID 식별 세그먼트는 제외/정규화한다. 정확 적중이 없으면 최대 12개 lexical token의 FTS5 검색을 한다. 결과는 **최대 2개**이며 현재 사건 자신의 카드는 제외한다. 한국어 의미 유사도나 어미 변형 검색을 제공하는 기능은 아니다.

모든 조회/검토는 필수 project scope를 검사하고 SQL 값을 매개변수로 전달한다. 전문 검색은 문장에서 뽑은 토큰을 따옴표로 감싸므로 빈 입력·특수문자·FTS 연산자 형태가 검색을 중단시키지 않는다. 검색에는 모델 호출이 없다.

현재 접수는 기존과 같이 요청을 상관하고 등록/선택 로그를 다시 읽으며 `get_version`으로 로컬 HEAD·수동 SHA·선택 실행 label의 출처를 다시 확인한다. 선택하지 않은 Docker를 카드 지시로 켜지 않는다. 그 뒤 과거 카드를 별도 **과거 조사 단서**로 전달한다. 모델 최대 4회, 읽기 도구 최대 6회, 코드 6개·로그 20줄의 기존 상한과 도구 허용 목록을 유지한다.

`memory_search`에는 적중 수·전략·검색 시간·검색 지문/입력 해시, 반환 카드와 현재 요청 연결·로그 읽기/완전성·현재 버전 출처·현재 실행의 근거 참조를 기록한다. 카드별 재확인은 다음 상태를 사용한다.

- `CURRENT_GUIDANCE_OBSERVED`: 현재 요청의 새 로그·계약으로 같은 안내를 독립 판정. 원인·수정 검증이 아님.
- `REJECTED`: 현재 응답·오류/예외/스택·알려진 진단 유형이 달라 과거 결론을 현재 사건에 적용하지 않음.
- `NOT_REVALIDATED`: 연결/자료 부족·충돌·중단·미확정 가설 등. 기존 조사/정보 대기를 유지.

실행 버전 미관측은 별도 이유로 남긴다. 현재 안내 관측이 있어도 배포·원인·수정 검증으로 표시하지 않는다. 검색/재확인은 규칙 라우팅을 변경하거나 도구·수정 권한을 추가하지 않는다. 저장 조회 자체만으로 현재 로그/버전을 재확인했다고 표시하지 않으며 실제 후속 실행에서 재조회한다.

검색 시간은 DB 연결·정확/전문 검색·결과 읽기를 포함한 `perf_counter` 실측이다. 조사 `elapsed_ms`에는 검색이 포함되고 종료 뒤 저장 시간은 포함되지 않는다. DB lock 대기는 0.25초로 제한하지만 파일 작업 등 전체 함수의 엄격한 실시간 종료를 보장하지 않는 기존 제한은 유지한다. 빠른 응답·호출 절감의 비교 효과는 측정하지 않았다.

## CLI와 화면 사용

작업 폴더에서 다음 합성 입력으로 접수·조회·검토를 실행할 수 있다. 모델 호출은 없다.

```powershell
$db = 'output/validation/memory-local.sqlite3'
$r = .\.venv\Scripts\python.exe -m scripts.investigate_report --db $db --repo tests/fixtures/agolive_repo --logs-file examples/scoped_agolive.log --report '회원가입 500 requestId=intake-guidance' --environment dev --service backend --occurred-at '2026-09-28T10:00:00+09:00' | ConvertFrom-Json
.\.venv\Scripts\python.exe -m scripts.incident_memory --db $db --project agolive list
.\.venv\Scripts\python.exe -m scripts.incident_memory --db $db --project agolive incident $r.incident_id
.\.venv\Scripts\python.exe -m scripts.incident_memory --db $db --project agolive run $r.run_id
.\.venv\Scripts\python.exe -m scripts.incident_memory --db $db --project agolive card $r.run_id
.\.venv\Scripts\python.exe -m scripts.incident_memory --db $db --project agolive review $r.run_id --action approve --reviewer local-maintainer
.\.venv\Scripts\python.exe -m scripts.incident_memory --db $db --project agolive search --path /api/users --query '회원가입'
.\.venv\Scripts\python.exe -m scripts.incident_memory --db $db --project agolive review $r.run_id --action edit --reviewer local-maintainer --finding '필수 입력 누락 안내. 다음 요청의 로그와 계약을 다시 확인한다.'
.\.venv\Scripts\python.exe -m scripts.incident_memory --db $db --project agolive review $r.run_id --action reject --reviewer local-maintainer --note '재사용 반려'
```

정확 검색 옵션은 `--error-code/--path/--exception/--stack-fingerprint`이며 반복할 수 있다. `--query`는 선택 사항이다. `scripts.triage_report --db ...`도 판정 결과를 저장한다. 제공 사건 목록의 프로젝트는 `tracebridge-demo`, 등록 로그 재현은 `agolive`이며 DB 조회 시 같은 프로젝트를 사용한다.

재시작 후 같은 사건의 새 답변:

```powershell
.\.venv\Scripts\python.exe -m scripts.investigate_report --db $db --repo tests/fixtures/agolive_repo --logs-file examples/scoped_agolive.log --resume $r.incident_id --project agolive --answer '같은 회원가입 화면입니다. 현재 자료를 다시 확인해 주세요.'
```

동일 프로젝트·저장소·사건 파일/등록 로그 경로·등록 범위·Docker 선택을 다시 전달해야 한다. 저장된 `source_binding` 해시로 검사한다. 원문 대신 명시 ID/오류/경로 등 구조화 단서, 접수 시각·선택 범위·사진 해시·답변 횟수를 복원한다. 원래 자유문과 사진/OCR 본문은 복원하지 않아 새 답변으로 보완할 수 있다. 후속 답변은 누적 6회 한도를 유지한다. 저장된 사진 단서는 `origin_run_id`와 해시를 보존하며 생략한 OCR 본문을 현재 근거로 재구성하지 않는다.

최초 접수 시각인 `session.received_at`과 각 실행의 `message_received_at`을 구분한다. 후속 답변의 `오늘·어제`는 그 답변의 접수 시각을 KST로 변환한 날짜를 기준으로 한다. `relative_date_basis={date, timezone}`는 상대 날짜의 기준이며, 명시 날짜/ISO 입력은 그대로 우선한다. 아래 날짜 보완 기록에 재현 결과와 제약을 남겼다.

화면의 개발자 기록에서 SQLite 저장 상태·실행 ID·검색/현재 재확인을 본다. 저장 실패 시 이미 확보한 결과를 유지하고 **이 결과 저장만 재시도** 버튼으로 저장만 다시 한다. 실패를 조사 `run_status`와 섞거나 모델·도구를 다시 실행하지 않는다. CLI에서는 원 실행의 명시적 JSON 내보내기가 있으면 아래처럼 저장만 재시도한다.

```powershell
# 최초 조사 시 선택적으로 --output output/validation/latest-run.json 사용
.\.venv\Scripts\python.exe -m scripts.incident_memory --db $db --project agolive save --file output/validation/latest-run.json
```

명시적 `--output` JSON은 DB의 최소 투영과 다르며 당시 비식별화한 발췌·제보 문구를 포함할 수 있다. `save`는 이 원 실행 결과를 입력으로 받는다. DB 조회 JSON을 원 실행과 같은 내용으로 간주해 덮어쓰지 않는다. 저장 실패 뒤 DB에 없는 실행은 조회할 수 없으며, 나중 실행을 저장해도 실패한 이전 실행을 자동 재생하지 않는다.

## 합성 검증과 실제 결과

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_incident_memory.py
.\.venv\Scripts\python.exe -m scripts.replay_incident_memory
git diff --check
```

사건 기억의 초기 기준은 **157개(기존 127 + 신규 30)** 통과였다. 후속 상대 날짜 보완으로 가짜 시계 검사 13개를 추가한 **전체 170개가 정상 권한에서 통과**했다(29.31초). 기존 회귀 기대값을 바꾸지 않았다. 제한 Windows 실행의 임시 폴더 접근 오류는 정상 권한 실행으로 확인했으며 기능 실패로 집계하지 않는다. 정식 회귀 범위는 `tests/`이고 `generated/`의 수정 전 재현 산출물은 수정하거나 정식 검사로 집계하지 않았다.

신규 검사는 재연결/이전 실행, 동일 실행 및 revision 충돌, 승인·수정·반려/FTS 갱신, 프로젝트 격리/2건 상한, 정확/스택/빈 입력/특수문자 검색, 요청값·원문 제외, 저장/검색 실패와 저장만 재시도, 자료 연결·답변 한도, 사진 단서 재연결과 추가 사진의 ID 구분, 과거 카드의 지시문·근거 ID 차단, 반복 제보와 다른 현재 원인을 검사한다. 같은 HTTP 500에서도 과거 `SQLiteException`과 현재 `IllegalStateException`을 구분해 과거 가설을 기각했고, 과거/현재에 모두 `L1`이 있어도 실행이 붙은 과거 참조는 현재 가설 지지에서 제외했다. 이 모델 경계 검사는 가짜 클라이언트이며 외부 모델 호출이 아니다.

최종 코드로 실행한 별도 수직 재현 기록은 `output/validation/stage3-memory-20260928-final/replay.json`과 같은 폴더 DB다. `scripts.replay_incident_memory`는 매번 새 기본 폴더를 만들고 기존 제보 소스 설정을 합성 설정과 분리한다. 아래 숫자는 한 로컬 실행의 측정값이며 성능 비교 결과가 아니다.

| 합성 제보 | 검색 | 현재 재확인·최종 상태 | 검색 시간 |
| --- | --- | --- | --- |
| 반복 회원가입 | 승인 카드 1건, EXACT | 새 request ID의 로그 1개·계약 → HTTP 422, `GUIDANCE/COMPLETED`, `CURRENT_GUIDANCE_OBSERVED` | 4.132ms |
| 같은 경로, 다른 현재 원인 | 같은 카드 1건, EXACT | 새 로그 1개·현재 `IllegalStateException` → HTTP 500, 과거 안내 `REJECTED`, `INVESTIGATE/WAITING_CONTEXT` | 4.291ms |
| 현재 422/500 충돌 | 같은 카드 1건, EXACT | 현재 로그 2개 보존, `NOT_REVALIDATED`, `REQUEST_CONTEXT/WAITING_CONTEXT`, 단일 응답 미확정 | 4.327ms |

DB 재연결 뒤 처음/후속 실행 2개와 revision 2를 확인했고 재저장은 `ALREADY_SAVED`였다. 위 실행은 모두 저장됐고 원인·수정 검증 false, 새 외부 모델 호출 0회였다. 현재 실행 버전은 `NOT_OBSERVED`로 유지했다. 실제 로그/배포/실사건 검증 완료, 조사 시간·도구 호출 절감, 범용 의미 검색의 증거로 사용하지 않는다.

## 재시작/후속 답변의 상대 날짜 보완

원인은 `report_agent.py`의 후속 `with_answer`에도 최초 `session.received_at`을 전달한 것이었다. SQLite는 이 최초 시각을 보존하므로, 다음 날 답변한 `오늘`이 최초 제보 날짜로 해석됐다. 수정 전 가짜 시계 검사에서 `claim-003`과 `contract-001`의 9/29 답변이 9/28 10:00으로 남는 실패 2개를 재현했다.

조사 함수 진입에서 현재 메시지의 UTC 시각을 한 번 캡처해 `with_answer`에 전달한다. 최초 접수는 이 시각을 `session.received_at`으로 설정하고, 후속 답변에서는 기존 최초 시각을 유지한다. 실행별 `message_received_at`과 `relative_date_basis={date, timezone: "+09:00"}`를 결과·이력·SQLite 최소 JSON에 추가했다. 기준 메타데이터는 상대 날짜를 해석할 때 사용하는 날짜이며 실제 조회할 발생 시각은 `session.context.occurred_at/log_scope.requested.occurred_at`이다. 명시 날짜와 ISO 입력에는 기존 우선순위를 유지한다.

자료 준비 중 자정을 넘어도 함수 진입 당시 메시지 날짜를 사용한다. 날짜가 바뀌면 기존 요청/로그 범위 검사로 현재 날짜를 다시 조회하고 이전 날짜 자료를 제외한다. 원 사건의 접수 시각·이전 실행을 다시 쓰지 않으며 자료 연결 해시·6회 답변 한도도 유지한다. 새 테이블/마이그레이션/실행 프레임워크나 원문 답변·사진 보관을 추가하지 않았다. 기존 저장 실행에 새 메타데이터가 없어도 다음 실행에서 현재 메시지 시각을 새로 캡처하며 이전 행은 그대로 둔다.

가짜 UTC 시각을 9/28 01:05 → 9/29 01:05(KST 각각 10:05)로 바꾼 단독 `claim-003` 재현:

| 메시지 | 상대 날짜 기준(KST) | 조회 발생 시각 | 실제 판정·현재 로그 |
| --- | --- | --- | --- |
| 최초 9/28 접수 | 2026-09-28 | `2026-09-28T10:00:00+09:00` | `GUIDANCE/COMPLETED`, HTTP 422, 로그 1개 일치 |
| 9/29 DB 복원 후 오늘 오전 10시 | 2026-09-29 | `2026-09-29T10:00:00+09:00` | `REQUEST_CONTEXT/WAITING_CONTEXT`, 응답 미확정, 기존 날짜 로그 1개 제외 |
| 같은 날 어제 오전 10시 | 2026-09-29 | `2026-09-28T10:00:00+09:00` | 현재 범위로 다시 조회해 `GUIDANCE/COMPLETED`, HTTP 422, 로그 1개 일치 |

세 실행 모두 최초 `session.received_at=2026-09-28T01:05:00+00:00`을 보존했다. 별도 새 실행 3개를 저장했고 최초 실행 조회 내용이 동일함을 확인했다. 원문 답변 대신 위 최소 시각·기준 정보만 추가했다. 기록은 `output/validation/message-date-replay-f1a8896b/replay.json`과 같은 폴더 DB에 있으며 모두 합성 자료다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_message_dates.py
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
git diff --check
```

신규 13개는 같은 날 오늘, 다음 날 복원 후 오늘의 안내/작업 후보 보류, 다음 날 어제, 같은 세션의 자정, 명시 한국어 날짜/ISO/UTC 시각, UTC 14:59/15:01의 KST 날짜 경계, 준비 중 자정, 최초 시각·이전 실행·자료 연결·답변 횟수를 검사했다. 가짜 시계만 사용했으며 실제 대기와 외부 모델 호출은 0회다. 지원 범위는 기존의 간단한 한국어 날짜·시각 패턴과 timezone이 있는 ISO 시각이며 상대 날짜는 KST 고정이다. 시간 없는 상대 표현·다른 시간대/자유로운 자연어 해석은 확장하지 않았다.

이번 국소 보완의 변경은 `tracebridge/report_agent.py`, `tracebridge/report_contract.py`, `tracebridge/incident_memory.py`, `tests/test_message_dates.py`, `docs/current-state.md`, `docs/archive/stages/stage-3-memory.md`에 한정했다. 격리 수정 작업자와 비교 평가 단계는 시작하지 않았다.

## 이번 변경 파일

| 범위 | 파일 |
| --- | --- |
| 저장·조사 계약 | `tracebridge/incident_memory.py`, `tracebridge/report_agent.py`, `tracebridge/report_contract.py` |
| CLI·재현·패키지 제외 | `scripts/incident_memory.py`, `scripts/investigate_report.py`, `scripts/triage_report.py`, `scripts/replay_incident_memory.py`, `scripts/package_submission.py` |
| 화면·검사 | `pages/2_Report_Agent.py`, `tests/test_incident_memory.py` |
| 설정 | `.env.example`, `.gitignore` |
| 사용법·현재 상태·관련 기준 | `README.md`, `docs/README.md`, `docs/current-state.md`, `docs/design/data-model.md`, `docs/archive/plans/competition-mvp.md`, `docs/archive/stages/stage-2-integration.md`, `docs/archive/stages/stage-3-memory.md`, `docs/design/architecture.md`, `docs/archive/stages/multimodal-intake.md`, `docs/design/evaluation-operations.md`, `docs/competition/requirements.md`, `docs/archive/plans/next-session-prompt.md` |

기존 미커밋·미추적 변경을 보존했으며 위 목록은 이번 단계에서 편집한 파일이다. 작업 시작 전부터 변경돼 있던 다른 파일을 이번 변경으로 집계하지 않는다.

현재 기능은 **로컬 내부용**이다. OS/파일 접근 권한을 가진 담당자가 CLI를 사용하며 project 필터는 인증·다중 조직 인가를 대신하지 않는다. 운영 연결·공개 접수·다중 사용자 검토·보관/삭제 정책과 실사건 검증은 남아 있다. 이번 단계의 보고로 종료한다.
