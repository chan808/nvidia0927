# TraceBridge

API 오류 제보나 제보 없는 백엔드 사건을 조사하는 도구입니다. 기존 합성 재현 데모 외에 **Agolive 로컬 저장소의 코드와 사용자가 제공한 로그**를 읽기 전용으로 검색해 제보의 원인 후보를 제시합니다. 로컬 Docker 로그는 사용자가 선택한 경우에만 조회합니다. 실제 운영 로그·배포 버전에는 아직 연결되지 않았습니다.

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

## 테스트와 범위

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
```

기존 NVIDIA 화면의 입력 자료는 **합성 사례 3개**입니다. 로컬 JSON 경로는 단일 요청의 일부 관측 자료만 받습니다. Agolive 연결은 로컬 코드와 선택 로그를 검색하지만 운영 로그·배포 버전을 자동 수집하거나 프로젝트를 수정하지 않습니다. 코드만 찾은 결과는 원인 확정 근거가 아닙니다. 수정 전 실패와 후보 수정 후 통과는 샘플 앱·격리 DB에만 해당하며 실제 서비스가 수정됐다는 뜻은 아닙니다. 모델이 작성한 임의 코드는 실행하지 않습니다.

핵심 화면은 NVIDIA NIM을 직접 호출합니다. 별도의 [NeMo Agent Toolkit 워크플로](nat_workflow.yml)도 포함돼 있지만 호스팅 모델 응답 지연으로 간헐적으로 시간 초과가 납니다. 해당 경로는 `python -m scripts.run_nat contract-001`로 실행할 수 있으며 재현 테스트는 수행하지 않습니다.
