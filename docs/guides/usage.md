# 사용법과 CLI

설치와 첫 화면은 [시작하기](getting-started.md)를 보세요. 아래 명령은 저장소 루트에서 실행합니다.

## 프로젝트 자료 연결

등록 JSON 프로필로 코드 루트·로그·OpenAPI·DTO·호출자 자료·버전 파일을 연결합니다. 로컬 버전 snapshot은 실제 실행 SHA와 구분하며 프로필의 정책 참조만으로 수정 권한이 생기지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.investigate_report --profile examples/parallel_b/ledger_demo/profile.json --report '계정 생성이 안돼요 requestId=ledger-001' --db output/manual-check/incidents.sqlite3
```

프로필 예시는 [ledger-demo](../../examples/parallel_b/README.md), 실행 범위는 [현재 상태](../current-state.md)를 보세요.

실제 여러 저장소·서비스를 등록하고, 허용한 검사로 수정 후보를 검증·검토 후 적용하거나 배포 웹과 PC 실행기를 연결하는 절차는 [실제 프로젝트 사용](real-projects.md)에 있습니다. 기존 합성 씨드 수정 경로와 별도로 사용할 수 있습니다.

## 자연어·사진 제보 에이전트

사이드바의 **Report Agent(제보 에이전트)**는 거친 증상, 자연어 지시, 사진만 있는 제보를 받습니다. 외부 분석을 선택하면 실제 **NeMo Retriever OCR 마이크로서비스**로 사진 문구를 읽고, 필요한 경우 Nemotron Vision으로 화면 증상을 설명합니다. 이어 Nemotron이 코드·등록된 로그·버전 조회 도구를 선택해 근거가 붙은 원인 후보를 만듭니다. 사진의 문구와 모델 설명은 제보 단서이며 서버 원인의 증거로 취급하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.investigate_report --report '방에 들어가면 계속 튕겨요. 확인해줘.'
.\.venv\Scripts\python.exe -m scripts.smoke_ocr
```

CLI의 `--live`는 사진과 선별·비식별화한 제보·코드·로그의 NVIDIA 전송을 선택합니다. `--image`만 접수할 수 있고 `--events`는 기존 관측 판정, `--answer`는 같은 사건 답변을 연결합니다. `TRACEBRIDGE_LOG_FILE`/`--logs-file` 또는 선택 Docker는 시각·서비스·환경 범위를 검사하며 누락 값은 미확인으로 표시합니다. 로컬 HEAD·수동 SHA·실행 label 출처와 조사 종료 상태를 분리합니다. 이 조사 명령 자체는 코드를 변경하지 않습니다. 등록한 실제 프로젝트의 수정 후보는 별도 준비·검토 단계를 사용하며 실제 Agolive 로그/배포 연결은 미검증입니다. [재현 명령](../archive/stages/stage-2-integration.md), [입력·도구 계약](../archive/stages/multimodal-intake.md), [기존 실제 API 기록](../validation/nvidia-validation.md)을 참조하세요.


## 제보와 사건 연결 (로컬 재생)

제보에 요청 ID가 있으면 제공된 사건 목록에서 정확 조회합니다. ID가 없으면 발생 시각·환경·메서드·경로로 단일 후보를 찾고 추가 확인 대상으로 표시합니다. 후보가 없거나 여럿이면 판정을 보류합니다. 예시 목록은 합성 자료이며 실제 서비스 로그에 연결되지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.triage_report --events examples/report_events.json --report '회원가입 API에서 500 requestId=claim-003'
```

출력의 `GUIDANCE`는 관측된 입력 검증을 설명하며 사용자의 책임을 확정하지 않습니다. `WORK_CANDIDATE`는 작업 후보를 뜻하며 실제 수정 에이전트 실행이 아닙니다. 단계별 구현 계획은 [대회용 최소 구현 순서](../archive/plans/competition-mvp.md)에 있습니다.


## 로컬 사건 기록·검토·검색

기본 DB는 workspace의 `output/tracebridge/incidents.sqlite3`입니다. `TRACEBRIDGE_DB_PATH` 또는 `--db`로 `.db/.sqlite/.sqlite3` 파일 위치를 설정합니다. 사건·각 실행·미검토 초안을 저장하며 같은 실행은 중복 저장하지 않고 다른 내용의 같은 실행 ID는 거절합니다. DB에는 원본 제보/요청값·사진·로그/코드 대신 구분된 최소 기록·출처·해시를 남깁니다.

```powershell
# 별도의 빈 재현 폴더에서 접수→재연결→검토→반복/다른 원인/충돌 확인 (외부 모델 0회)
$demo = .\.venv\Scripts\python.exe -m scripts.replay_incident_memory | ConvertFrom-Json
$db = $demo.database
.\.venv\Scripts\python.exe -m scripts.incident_memory --db $db --project agolive list
.\.venv\Scripts\python.exe -m scripts.incident_memory --db $db --project agolive incident $demo.repeated.incident_id
.\.venv\Scripts\python.exe -m scripts.incident_memory --db $db --project agolive run $demo.repeated.run_id
.\.venv\Scripts\python.exe -m scripts.incident_memory --db $db --project agolive review $demo.repeated.run_id --action approve --reviewer local-maintainer
.\.venv\Scripts\python.exe -m scripts.incident_memory --db $db --project agolive search --path /api/users
```

`approve/edit/reject`로 카드를 검토합니다. 승인/수정 검토된 카드만 같은 프로젝트의 정확 지문·FTS5로 최대 2개 반환합니다. 검토 상태와 원인·수정 검증 상태는 별개이며 `COMPLETED`도 해결 성공이 아닙니다. 과거 참조에는 실행 ID를 붙이고 현재 관측·근거로 추가하지 않습니다. 현재 요청·로그·버전 출처를 다시 확인하며 다른 원인·자료 부족·충돌이면 기존 조사/보류를 유지합니다.

`scripts.investigate_report --resume <incident_id> --answer '새 답변'`은 저장 당시와 같은 현재 자료 연결 옵션을 사용해 같은 사건의 새 실행을 만듭니다. 원문은 복원하지 않고 명시 ID/오류/경로·선택 범위·답변 횟수를 복원합니다. 화면에서 저장 실패를 표시하며 저장만 재시도할 수 있습니다. CLI의 원 실행 JSON은 `scripts.incident_memory save --file ...`로 저장만 재시도합니다. [실행 가능한 전체 명령과 측정 결과](../archive/stages/stage-3-memory.md)를 참조하세요. 현재 기능은 OS/파일 접근 권한을 가진 담당자의 로컬 내부용이며 project 필터는 공개 서비스 인증·인가가 아닙니다.


## 격리 수정 준비 (등록 씨드 A2)

통제된 `SEEDED_DEVELOPMENT` 프로젝트의 요청 필드 오류 한 가지를 대상으로 합니다. 등록 정책을 통과한 저장 실행에서 원본의 별도 사본을 만들고, 수정 전 실패를 확인한 뒤 기존 NVIDIA 연결의 구조화 제안을 검증해 사전 키 문자열만 바꿉니다. 검사 파일·실행 명령·환경 설정은 모델이 바꿀 수 없습니다. OS/OpenShell 격리를 확보하지 않았으므로 등록된 신뢰 가능한 씨드 코드만 실행합니다.

```powershell
$stage4Db = 'output/validation/stage4-local/state.sqlite3'
$source = .\.venv\Scripts\python.exe -m scripts.prepare_change --db $stage4Db seed | ConvertFrom-Json
$prepared = .\.venv\Scripts\python.exe -m scripts.prepare_change --db $stage4Db prepare --source-run $source.run_id --live | ConvertFrom-Json
.\.venv\Scripts\python.exe -m scripts.prepare_change --db $stage4Db show $prepared.job.work_id
```

`--live`는 등록 합성 코드·입력·실패의 Nemotron 전송을 선택합니다. 생략하면 수정 전 재현과 모델 미요청 상태만 남깁니다. 원본 적용·배포·서비스 회복은 수행하지 않으며 새 카드는 PENDING입니다. 실패 기록·실제 모델 호출 2회의 구분, 원본/후보 해시, 검토·저장만 재시도와 전체 명령은 [4단계 기록](../archive/stages/stage-4-change.md)에 있습니다. 내부 CLI이며 제보자에게 실행 경로/명령 ID를 요구하지 않습니다.


## Agolive 기존 수동 조사

기존 수동/GPT 페이지의 연결·로그·전송 범위는 [Agolive 가이드](agolive.md)에 모았습니다.

## 기존 합성 데모


1. 제보가 있으면 명시된 HTTP 상태·메서드·경로·`○○ API` 작업명을 항목별로 실제 요청과 비교합니다. 제보가 없어도 관측된 오류를 조사합니다.
2. 합성 사례의 라이브 경로에서 NVIDIA Nemotron 모델이 API 계약, 백엔드 오류, 마이그레이션 상태 중 필요한 읽기 전용 도구를 선택합니다. 필수 근거가 빠지면 Python 검증기가 보완합니다.
3. 검증기가 근거가 확인된 불일치만 판정합니다. 지원하는 두 오류 유형이라면 고정된 pytest 템플릿으로 샘플 앱·인메모리 SQLite에서 수정 전후를 비교합니다. 그 외 5xx 로그는 예외 종류 단서만 제시하고 원인을 보류합니다.

| 사례 | 확인할 내용 |
| --- | --- |
| `contract-001` | “회원가입 API 500” 제보의 작업명 일치·상태 불일치를 구분하고 `user_id` / `userId` 계약 불일치를 재현 |
| `migration-002` | 실제 500의 로그와 V11 / V12 이력을 대조하고 누락된 `phone` 컬럼을 재현 |
| `claim-003` | “500” 제보와 실제 422를 구분하고 근거 없는 500 원인을 만들지 않음 |
| `unknown` | 요청을 식별할 수 없으면 모델 호출 없이 추가 정보를 요청 |

화면에는 제보 항목별 판정, 근거, 도구 호출 기록, 모델 사용량, 테스트 결과가 표시됩니다. `MATCHED` 등 제보 집계는 **추출된 항목에 한정**하며 자연어 전체의 진위를 보장하지 않습니다. 최종 판정과 요약은 확인된 증거에서 생성합니다.


```powershell
.\.venv\Scripts\python.exe -m scripts.demo_cli contract-001 --repro
.\.venv\Scripts\python.exe -m scripts.demo_cli migration-002 --repro
.\.venv\Scripts\python.exe -m scripts.demo_cli migration-002 --offline --without-report
.\.venv\Scripts\python.exe -m scripts.demo_cli --bundle examples/backend_incident.json --without-report
```

`--bundle`은 [예시 JSON](../../examples/backend_incident.json)과 같은 로컬 사건 묶음만 오프라인으로 읽습니다. 필수 필드는 `trace.trace_id`, `environment`, `service`, `response_status`이며 `logs`는 문자열 배열입니다. `method`, `path`, `operation`, `version`, `request`, `contract`, `dto`, `migration`을 선택적으로 추가할 수 있습니다. 이 경로는 NVIDIA API를 호출하거나 대상 코드를 실행하지 않습니다. 예시 파일도 합성 자료입니다.


검사와 NAT 계측 명령은 [검증 절차](verification.md)에 있습니다. 과거 단계 문서는 작성 당시의 실행 기록이며 현재 판정·수집 범위는 [최종 통합](../validation/main-integration.md)을 기준으로 확인하세요.
