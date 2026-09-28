# TraceBridge

API 오류 제보나 제보 없는 백엔드 사건을 조사하는 도구입니다. 기존 합성 재현 데모 외에 **Agolive 로컬 저장소의 코드와 사용자가 제공한 로그**를 읽기 전용으로 검색해 제보의 원인 후보를 제시합니다. 로컬 Docker 로그는 사용자가 선택한 경우에만 조회합니다. 실제 운영 로그·배포 버전에는 아직 연결되지 않았습니다.

**목표 서비스와 현재 데모를 구분해 읽으려면 [문서 지도](docs/README.md)부터 보세요.** 제품·사건 흐름·권한·자동 해결 정책·NVIDIA 대회 조건·구축 단계는 `docs/`에 나눠 정리했습니다. 이 README는 지금 실행 가능한 범위를 설명합니다.

2026-09-28 [접수·판정 통합](docs/stage-2-integration.md)과 [SQLite 사건 기억](docs/stage-3-memory.md)을 연결했습니다. 새 화면/CLI의 접수·후속 답변 결과를 저장하고 재시작 후 조회·답변하며 검토된 카드만 다음 제보의 과거 조사 단서로 검색합니다. 기존 127개와 신규 30개 검사가 통과했습니다. 실제 Agolive 로그·배포 연결과 실사건 검증은 자료 확보 대기이며 수정 작업자·비교 평가·제출 단계는 시작하지 않았습니다.

## 동작 방식

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

## 실행

Python 3.12가 필요합니다. 아래는 `uv`를 사용하는 Windows PowerShell 설치 예시입니다.

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

`.env`에 NVIDIA Build API 키를 `NVIDIA_API_KEY=...`로 설정한 뒤 실행합니다. 키가 없으면 화면에서 **오프라인 자료 검증**을 선택할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

`http://localhost:8501`에서 사례를 선택하고 **증거 조사 시작 → 재현 테스트 생성·실행** 순서로 확인합니다. CLI에서도 실행할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.demo_cli contract-001 --repro
.\.venv\Scripts\python.exe -m scripts.demo_cli migration-002 --repro
.\.venv\Scripts\python.exe -m scripts.demo_cli migration-002 --offline --without-report
.\.venv\Scripts\python.exe -m scripts.demo_cli --bundle examples/backend_incident.json --without-report
```

`--bundle`은 [예시 JSON](examples/backend_incident.json)과 같은 로컬 사건 묶음만 오프라인으로 읽습니다. 필수 필드는 `trace.trace_id`, `environment`, `service`, `response_status`이며 `logs`는 문자열 배열입니다. `method`, `path`, `operation`, `version`, `request`, `contract`, `dto`, `migration`을 선택적으로 추가할 수 있습니다. 이 경로는 NVIDIA API를 호출하거나 대상 코드를 실행하지 않습니다. 예시 파일도 합성 자료입니다.

## Agolive 제보 조사

Streamlit 사이드바의 **Agolive 제보 조사** 페이지에서 자연어 제보와 선택 로그를 입력합니다. CLI에서는 다음처럼 코드 근거를 찾을 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.analyze_agolive --report '방 입장 시 ROOM_FULL 오류'
.\.venv\Scripts\python.exe -m scripts.analyze_agolive --report '방 입장 실패 requestId=abcd1234' --logs-file .\error.log
```

기본 검색은 로컬에서만 실행됩니다. Agolive의 GPT 키는 `agolive-agent/.env`에서 읽을 수 있지만 자동 호출하지 않습니다. **선별·비식별화한 제보·코드·로그의 OpenAI API 전송을 선택**하려면 화면의 체크박스나 CLI `--gpt`를 사용합니다. 연결 구조와 데이터 범위는 [Agolive 연결 문서](AGOLIVE_INTEGRATION.md)에 적었습니다. 실제 API 호출 검증은 외부 코드 전송 승인 검토에서 거절되어 아직 완료되지 않았습니다.

## 제보와 사건 연결 (로컬 재생)

제보에 요청 ID가 있으면 제공된 사건 목록에서 정확 조회합니다. ID가 없으면 발생 시각·환경·메서드·경로로 단일 후보를 찾고 추가 확인 대상으로 표시합니다. 후보가 없거나 여럿이면 판정을 보류합니다. 예시 목록은 합성 자료이며 실제 서비스 로그에 연결되지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.triage_report --events examples/report_events.json --report '회원가입 API에서 500 requestId=claim-003'
```

출력의 `GUIDANCE`는 관측된 입력 검증을 설명하며 사용자의 책임을 확정하지 않습니다. `WORK_CANDIDATE`는 작업 후보를 뜻하며 실제 수정 에이전트 실행이 아닙니다. 단계별 구현 계획은 [대회용 최소 구현 순서](docs/competition-mvp.md)에 있습니다.

## 자연어·사진 제보 에이전트

사이드바의 **Report Agent(제보 에이전트)**는 거친 증상, 자연어 지시, 사진만 있는 제보를 받습니다. 외부 분석을 선택하면 실제 **NeMo Retriever OCR 마이크로서비스**로 사진 문구를 읽고, 필요한 경우 Nemotron Vision으로 화면 증상을 설명합니다. 이어 Nemotron이 코드·등록된 로그·버전 조회 도구를 선택해 근거가 붙은 원인 후보를 만듭니다. 사진의 문구와 모델 설명은 제보 단서이며 서버 원인의 증거로 취급하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.investigate_report --report '방에 들어가면 계속 튕겨요. 확인해줘.'
.\.venv\Scripts\python.exe -m scripts.smoke_ocr
```

CLI의 `--live`는 사진과 선별·비식별화한 제보·코드·로그의 NVIDIA 전송을 선택합니다. `--image`만 접수할 수 있고 `--events`는 기존 관측 판정, `--answer`는 같은 사건 답변을 연결합니다. `TRACEBRIDGE_LOG_FILE`/`--logs-file` 또는 선택 Docker는 시각·서비스·환경 범위를 검사하며 누락 값은 미확인으로 표시합니다. 로컬 HEAD·수동 SHA·실행 label 출처와 조사 종료 상태를 분리합니다. 실제 Agolive 로그/배포 연결은 미검증이며 코드 변경은 수행하지 않습니다. [재현 명령](docs/stage-2-integration.md), [입력·도구 계약](docs/multimodal-intake.md), [기존 실제 API 기록](docs/nvidia-validation.md)을 참조하세요.

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

`scripts.investigate_report --resume <incident_id> --answer '새 답변'`은 저장 당시와 같은 현재 자료 연결 옵션을 사용해 같은 사건의 새 실행을 만듭니다. 원문은 복원하지 않고 명시 ID/오류/경로·선택 범위·답변 횟수를 복원합니다. 화면에서 저장 실패를 표시하며 저장만 재시도할 수 있습니다. CLI의 원 실행 JSON은 `scripts.incident_memory save --file ...`로 저장만 재시도합니다. [실행 가능한 전체 명령과 측정 결과](docs/stage-3-memory.md)를 참조하세요. 현재 기능은 OS/파일 접근 권한을 가진 담당자의 로컬 내부용이며 project 필터는 공개 서비스 인증·인가가 아닙니다.

## 테스트와 범위

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_incident_memory.py
git diff --check
```

정식 회귀 범위는 `tests/`이며 **157개(기존 127+신규 30)**가 통과했습니다. `generated/`의 수정 전 재현 산출물은 별도입니다. 검색 적중·시간·현재 재확인을 기록하지만 응답 시간·도구 호출 절감이나 실사건 검증 완료는 주장하지 않습니다.

기존 NVIDIA 화면의 입력 자료는 **합성 사례 3개**입니다. 로컬 JSON 경로는 단일 요청의 일부 관측 자료만 받습니다. Agolive 연결은 로컬 코드와 선택 로그를 검색하지만 운영 로그·배포 버전을 자동 수집하거나 프로젝트를 수정하지 않습니다. 코드만 찾은 결과는 원인 확정 근거가 아닙니다. 수정 전 실패와 후보 수정 후 통과는 샘플 앱·격리 DB에만 해당하며 실제 서비스가 수정됐다는 뜻은 아닙니다. 모델이 작성한 임의 코드는 실행하지 않습니다.

핵심 화면은 NVIDIA NIM을 직접 호출합니다. 별도의 [NeMo Agent Toolkit 워크플로](nat_workflow.yml)도 포함돼 있지만 호스팅 모델 응답 지연으로 간헐적으로 시간 초과가 납니다. 해당 경로는 `python -m scripts.run_nat contract-001`로 실행할 수 있으며 재현 테스트는 수행하지 않습니다.
