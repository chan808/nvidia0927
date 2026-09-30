# 실제 프로젝트 등록·제보·수정 후보 검증

2026-09-29. 실제 경로 등록, 서비스 선택, 코드 탐색, 등록 검사, 후보 검증과 검토 후 원본 적용을 사용할 수 있다. [최종 GUI 검증 범위](../validation/local-gui-final.md)를 함께 확인한다.

## 이 PC에서 바로 사용

2026-09-30 간편 화면에서 세 프로세스를 연결해 실행한다: [제보 웹](http://127.0.0.1:8502), 로컬 API `127.0.0.1:8765`, `daily-local`에 페어링된 PC 실행기. 기존 8501 포트와 충돌을 피하기 위해 웹은 8502를 사용한다. 웹 기본 화면 **연결된 프로젝트**에서 프로젝트를 고르고 오른쪽 연결 상태를 본 뒤 글이나 사진을 보낸다. 서비스 범위 선택은 고급 설정에 있다. 아래 재실행 명령은 이 PC에 남긴 로컬 설정을 사용한다.

[최종 GUI 검증](../validation/local-gui-final.md)에서 실제 경로·정책 등록, 제보·답변, 실제 모델 후보, 검토 후 원본 적용과 재열기, PC 중단·재연결을 확인했다. 공개 검증용 `gui-local-37a6651e`의 수정된 예시와 저장된 사건도 목록에 남겨 두었다. 최종 세션에서 daily 개발 서버 3100/8081은 응답하지 않았으며 PC 실행기의 온라인 상태는 코드 파일 조회 가능 상태다.

재실행할 때는 아래 환경을 웹/API 터미널에 설정하고 API, PC 실행기, 웹을 각각 시작한다. 이 PC의 인증 파일과 실행기 config는 `output` 아래에 보관하며 저장소에 포함하지 않는다.

```powershell
$env:TRACEBRIDGE_OPERATOR_TOKEN_FILE=(Resolve-Path output/local-service/control-plane/operator-token).Path
$env:TRACEBRIDGE_CONTROL_DB=(Join-Path (Get-Location) 'output/local-service/control-plane/state.sqlite3')
$env:TRACEBRIDGE_CONTROL_URL='http://127.0.0.1:8765'
$env:TRACEBRIDGE_PROJECT_PROFILE=(Resolve-Path output/project-profiles/daily-local.json).Path
$env:TRACEBRIDGE_PROJECT_REGISTRY=(Resolve-Path output/project-profiles).Path
$env:TRACEBRIDGE_ALLOW_PROJECT_REGISTRATION='1'
# 각각 별도 터미널에서 실행
.\.venv\Scripts\python.exe -m scripts.serve_control --host 127.0.0.1 --port 8765
.\.venv\Scripts\python.exe -m scripts.local_runner --config output/local-service/runner/config.json run
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1 --server.port 8502
```

다른 PC에서 새 등록을 시작할 때:

```powershell
$env:TRACEBRIDGE_ALLOW_PROJECT_REGISTRATION="1"
.\.venv\Scripts\python.exe -m streamlit run app.py --server.address 127.0.0.1
```

**연결된 프로젝트**에서 이름·프로젝트 폴더·선택적 실행 주소를 등록하고 증상·사진을 보낸다. 여러 앱의 코드·로그·계약·실행 버전을 연결하거나 직접 점검하려면 고급 설정을 연다. [간편 사용법](simple-project.md)과 [상세 경로·직접 점검](project-connection.md)을 확인한다. 경로는 이 프로그램을 실행하는 PC 기준이다. 서버에 배포한 화면에서 PC 경로를 입력하는 방식은 사용하지 않는다.

현재 PC에는 `daily-local`을 다음 실제 경로로 등록했다.

| 등록 ID | 경로 | 범위 |
| --- | --- | --- |
| frontend | `C:/Users/freetime/Desktop/projects/daily/frontend` | `src` |
| backend | `C:/Users/freetime/Desktop/projects/daily/backend` | `app/src/main`, `domain`, `platform` |

초기 연결 시 프론트 `http://127.0.0.1:3100`, 백엔드 `http://127.0.0.1:8081/actuator/health`, API 프록시의 GET 200 응답을 확인했다. 당시 백엔드는 `SERVER_PORT=8081`로 실행됐으며 Docker에서 daily PostgreSQL·Redis를, 로컬 개발 프로세스에서 백·프론트를 관측했다. 최종 GUI 검증 시 3100/8081의 응답은 없었다. 실행기 온라인 상태와 서비스 가용 상태를 따로 확인한다.

현재 백엔드 로그는 별도 콘솔 출력이며 이 등록에 로그 파일은 연결하지 않았다. 따라서 코드 검색·등록 검사와 실서비스 요청 연결을 구분한다. 기존 프론트의 데모 경계 검사와 Vitest를 독립 사본에서 실행해 24개 파일·229개 테스트 통과와 원본 미변경을 확인했다. 실제 버그 제보를 해결한 기록으로 간주하지 않는다.

`daily-frontend-repair`는 데모의 운영 API 접근 경계 검사를 재현 검사로, 프론트 Vitest 전체를 회귀 검사로 등록했다. 모든 증상을 이 재현 검사가 다루는 것은 아니다. 실제 제보에 대응하는 기존 Vitest 파일/검사 ID·실패/성공 출력 문구로 정책을 바꾼 뒤 조사한다. 원본 적용 권한은 현재 꺼져 있다.

## 판정과 수정의 조건

- 제보와 현재 로그·계약·입력·호출자 근거를 대조한다. HTTP 4xx만으로 사용자 실수라고 판정하지 않는다. 입력 누락과 정상 필드 전달이 확인된 경우에 안내한다.
- 로그 연결·서비스/환경·버전이 충돌하면 원인과 수정 대상을 보류한다. 다른 환경의 로그로 현재 사건을 설명하지 않는다.
- 수정 후보는 소유자가 등록한 저장소·편집 경로·검사에 한정한다. 모델에 임의 셸 명령이나 정책 변경 권한을 주지 않는다.
- 수정 전 등록 검사가 실패 코드 1과 해당 실패 문구를 반환해야 제안을 요청한다. 이미 통과하거나 다른 오류이면 `NOT_REPRODUCED` 또는 보류다.
- 수정 후 같은 검사와 회귀가 코드 0·등록 성공 문구·검사 파일 무변경을 모두 충족해야 `CHANGE_PREPARED`다. 모델의 설명으로 성공 상태를 만들지 않는다.
- 로그 없는 재현을 소유자가 별도로 허용할 수 있다. 이 경우 제보와 실서비스 요청의 연결은 미확인으로 유지하고 등록 검사로 재현한 코드 사본의 후보만 검증한다.

## 경로·서비스 연결 JSON

CLI 등록은 원본 JSON의 상대 경로 기준을 보존하면서 로컬 등록부로 옮긴다.

```powershell
.\.venv\Scripts\python.exe -m scripts.project_tools --project my-app register --file my-project.json
.\.venv\Scripts\python.exe -m scripts.project_tools --project my-app doctor
```

```json
{
  "project_id": "my-app",
  "root": "C:/work/backend",
  "service": "backend",
  "environment": "dev",
  "code_roots": [],
  "repositories": [
    {"id": "frontend", "service": "frontend", "root": "D:/work/frontend", "code_roots": ["src"]},
    {"id": "backend", "service": "backend", "root": "C:/work/backend", "code_roots": ["app"]},
    {"id": "logs", "service": "backend", "root": "D:/logs/my-app", "code_roots": []}
  ],
  "log_sources": [
    {"id": "api-requests", "repository": "logs", "path": "api.jsonl", "format": "jsonl", "service": "backend"}
  ],
  "services": [
    {"id": "backend", "log_source_ids": ["api-requests"]},
    {"id": "frontend", "log_source_ids": []}
  ],
  "policy_refs": ["my-app-repair"]
}
```

서비스별 항목에는 해당 서비스의 계약·DTO·호출자 JSON과 실행 버전 출처를 추가할 수 있다. 환경은 등록 하나의 고정 범위다. dev/prod는 별도 등록 ID로 관리한다. 기존 단일 서비스 프로필도 사용할 수 있다.

로그는 JSON/JSONL/text를 지원한다. 구조화 로그에는 시간대 포함 시각, service, environment, request/trace ID, 응답 상태와 진단 문구를 남기는 편이 좋다. 시간대 없는 text 로그는 등록에 실제 `timezone`을 지정한다. 등록 범위와 명시적인 로그 값이 다르면 제외한다.

## 소유자의 수정·검사 정책

프로필의 `policy_refs`에 있는 ID를 별도 로컬 정책 파일로 등록한다. 경로 등록만으로 실행 권한은 생기지 않는다.

```json
{
  "policy_id": "my-app-repair",
  "project_id": "my-app",
  "repository_id": "backend",
  "environment": "dev",
  "enabled": true,
  "execution_mode": "TRUSTED_LOCAL",
  "trust_project_code": true,
  "allow_apply": false,
  "allow_reproduction_without_logs": false,
  "editable_paths": ["app/*.py", "app/**/*.py"],
  "checks": [
    {"id": "reproduce", "argv": ["python", "-m", "pytest", "-p", "no:cacheprovider", "tests/test_signup.py"], "success_marker": "passed", "timeout_seconds": 60},
    {"id": "regression", "argv": ["python", "-m", "pytest", "-p", "no:cacheprovider", "tests"], "success_marker": "passed", "timeout_seconds": 120}
  ],
  "reproduction_check_id": "reproduce",
  "regression_check_id": "regression",
  "failure_marker": "test_signup_payload",
  "max_seconds": 300
}
```

```powershell
.\.venv\Scripts\python.exe -m scripts.project_tools --project my-app policy --file my-repair-policy.json
.\.venv\Scripts\python.exe -m scripts.project_tools --project my-app check
```

`TRUSTED_LOCAL`은 소유자가 신뢰하는 코드의 실행이며 OS 파일·네트워크 격리가 없다. 수정할 수 있는 코드도 테스트 실행 중 같은 권한을 가진다. `DOCKER`는 준비된 로컬 이미지의 digest를 고정하고 후보만 마운트하며 네트워크·root filesystem·권한·CPU/메모리를 제한한다. 실제 Docker 격리의 악성 코드 검증은 아직 수행하지 않았다. Docker 실행기를 적대적 코드의 보안 경계로 주장하지 않는다.

Node 검사에는 셸/배치 파일 대신 실제 node 실행파일과 `node_modules/vitest/vitest.mjs` 같은 진입점을 등록한다. `dependency_directories: ["node_modules"]`를 해당 검사에 추가하면 독립 사본을 준비한다. 원본의 node_modules를 후보에 링크하지 않는다. 설치·네트워크 다운로드는 자동 수행하지 않는다. 처음 준비에는 파일 복사 시간이 걸리므로 전체 예산을 함께 늘린다.

## 제보 → 후보 → 검토 후 적용

```powershell
.\.venv\Scripts\python.exe -m scripts.investigate_report --profile output/project-profiles/my-app.json --service backend --report '가입하면 실패해요 requestId=req-123' --occurred-at '2026-09-29T12:00:00+09:00' --db output/my-app/incidents.sqlite3 --live
```

저장된 run ID로 후보를 준비한다. `--live`를 생략하면 재현 검사까지만 실행하고 모델을 요청하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m scripts.project_tools --project my-app --db output/my-app/incidents.sqlite3 prepare --source-run RUN_ID --live
.\.venv\Scripts\python.exe -m scripts.project_tools --project my-app --db output/my-app/incidents.sqlite3 show --work-id WORK_ID
```

프로젝트의 등록 설정이 조사 후 바뀌면 다시 조사해야 한다. 원본 파일과 후보 파일이 검증 후 바뀌면 적용을 거절한다. 같은 작업을 반복해서 요청하면 확보한 결과만 반환하고 모델·검사를 다시 실행하지 않는다. 저장 실패는 `save --file <result.json>`으로 저장만 재시도한다.

정책에 `allow_apply: true`를 **후보를 준비하기 전에** 설정한 프로젝트는 화면의 diff 검토 후 적용 버튼 또는 다음 명령을 사용할 수 있다.

```powershell
.\.venv\Scripts\python.exe -m scripts.project_tools --project my-app --db output/my-app/incidents.sqlite3 apply --work-id WORK_ID --diff-sha256 REVIEWED_DIFF_SHA256
```

원본 적용, 재기동, 배포와 서비스 회복은 별개다. 원본 적용 후에도 `fix_verified`와 서비스 회복은 자동 성공 처리하지 않는다.

## 배포 웹과 PC 연결

현재 원격 기능은 **소유자 한 명의 비공개 시범**이다. 웹은 운영자 secret으로 API를 사용하고 실행기는 프로젝트가 제한된 별도 credential을 쓴다. 소유자 전용 원격 사진 처리는 제공하며 공개 사진 접수·공개 다중 사용자 로그인·조직별 membership·자동 배포는 이 시범에 포함되지 않는다.

서버 환경에는 `.[test,service]`를 설치하고 NVIDIA 키를 둔다. PC 실행기에는 NVIDIA 키가 필요 없다.

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test,service]"
.\.venv\Scripts\python.exe -m scripts.serve_control --init-token-file output/control-plane/operator-token
$env:TRACEBRIDGE_OPERATOR_TOKEN_FILE=(Resolve-Path output/control-plane/operator-token).Path
$env:TRACEBRIDGE_CONTROL_URL="http://127.0.0.1:8765"
.\.venv\Scripts\python.exe -m scripts.serve_control --port 8765
```

웹 프로세스에도 같은 API URL과 operator token 파일을 설정한 뒤 **연결된 프로젝트 → 고급 설정 → 다른 컴퓨터 연결**에서 등록 프로젝트 ID의 5분 유효 페어링 코드를 만든다. 실제 배포는 HTTPS URL을 사용한다. PC에서 일회용 코드를 입력하고 실행한다.

```powershell
.\.venv\Scripts\python.exe -m scripts.local_runner --config output/local-runner/config.json pair --url https://YOUR_DOMAIN
.\.venv\Scripts\python.exe -m scripts.local_runner --config output/local-runner/config.json run
```

Windows credential은 해당 OS 사용자 계정의 DPAPI로 보호한다. 다른 OS는 소유자 전용 파일 권한으로 보관한다. credential은 30일 후 만료되며 재페어링한다. PC 경로는 로컬 등록부에 남고 서버 manifest는 저장소/서비스 ID·환경·설정 해시·상태만 받는다. 조사에 필요한 선별된 코드·로그와 결과는 서버로 전송된다.

서버 DB 큐가 접수·임대·원본 접수 시각·결과·모델 단계 응답을 보존한다. PC가 꺼지면 큐에 대기한다. 완료 결과의 ACK가 유실되면 전송만 재시도하고, 실행 중 끊겨 상태가 모호한 작업은 `RECOVERY_REQUIRED`로 남긴다. 확보한 결과를 재전송하는 복구 요청과 새 조사 요청을 구분한다. 취소 요청과 PC 종료 확인도 구분한다.

서버 배포용 합성 구성 검사는 `compose.yaml`에 `control-plane.compose.yaml`을 추가하는 방식이다. Caddy는 `/v1/*`를 bearer 인증 API로, 나머지 웹을 기존 Basic Auth로 연결한다. 준비한 server data 디렉터리에 operator token 파일을 먼저 생성하고 컨테이너 사용자에게 접근을 허용한다. 이 저장소에서는 실제 도메인/HTTPS 배포를 실행하지 않았다.

```powershell
docker compose --env-file deploy/.env -f deploy/compose.yaml -f deploy/control-plane.compose.yaml config
docker compose --env-file deploy/.env -f deploy/compose.yaml -f deploy/control-plane.compose.yaml up -d --build
```

## 공개 합성 자료로 실제 모델 경로 확인

```powershell
.\.venv\Scripts\python.exe -m scripts.validate_project_repair --live
```

공개 작은 프로젝트의 HTTP 제보 → PC snapshot → 서버 NVIDIA 제안 → 수정 전후·회귀를 실행한다. 실제 사용자 프로젝트를 전송하거나 수정하지 않는다. 테스트 더블 검사와 실제 모델 호출 결과를 구분해 저장한다.

## 새 clone에서 GUI 흐름 재현

로컬 설치 후 다음 명령으로 새 공개 예시를 만든다. 매번 별도 프로젝트 ID와 디렉터리를 생성하므로 앞선 적용 기록을 덮어쓰지 않는다. 생성만 수행하며 등록·조사·검토·적용은 화면에서 진행한다.

```powershell
.\.venv\Scripts\python.exe -m scripts.setup_gui_validation
```

출력과 `output/gui-validation/latest-fixture.json`의 프로젝트 ID·frontend/backend 경로·발생 시각·검사 argv를 사용한다.

1. **제보 에이전트 → 프로젝트 경로 등록**에서 출력의 ID와 두 저장소 경로를 등록한다. 각 코드 범위는 `src`, backend 로그는 `events.jsonl`, 형식은 `jsonl`, 서비스는 `backend`, 환경은 `dev`다.
2. **프로젝트 수정·검사 설정**에서 수정 저장소 `frontend`, 편집 경로 `src/app.py`, 신뢰 모드 `TRUSTED_LOCAL`을 등록한다. 재현은 출력의 `reproduction_argv`, 회귀는 `regression_argv`, 성공 문구는 `CHECK_PASSED`, 재현 실패 문구는 `QUOTA_BOUNDARY`다. 이 공개 예시에 한해 후보 준비 전에 원본 적용을 허용한다.
3. `정원 5인데 5개 항목을 등록하면 거절됩니다. 요청 ID gui-quota-001`로 제보하고, 같은 사건에 출력의 `received_at`을 답변한다. 코드 가설과 정확 로그 연결 상태를 확인한다.
4. NVIDIA 사용을 선택하고 후보를 준비한다. 실제 API 비용이 발생한다. provider 오류이면 실패 기록을 확인하고 같은 사건에서 다시 조사·준비한다. 후보가 성공하면 수정 전 실패·수정 후 성공·회귀 성공과 `< 5`에서 `<= 5`로 바뀐 diff를 확인한다.
5. diff 검토 체크 후 공개 예시 원본에 적용한다. 웹을 새로 열고 프로젝트 선택·저장된 사건 불러오기로 `APPLIED`와 검사가 유지되는지 확인한다.
6. 원격 연결은 앞 절차로 PC를 페어링한 뒤 **연결된 프로젝트**에서 확인한다. 실행기 종료 시 오프라인·대기, 재시작 후 완료된 작업의 결과 재전송과 모델 호출 횟수를 확인한다.

이 예시는 일반 프로젝트의 경로·로그·수정·검사·저장을 확인하는 공개 합성 자료다. 실제 서비스 재기동이나 DB 변경을 수행하지 않으며, 실제 daily 장애 해결의 증거로 사용하지 않는다.
