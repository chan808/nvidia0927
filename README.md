# TraceBridge

프론트엔드의 모호한 API 오류 제보를 **관측된 요청의 증거와 실행 가능한 재현 테스트**로 바꾸는 데모입니다. 제보를 사실로 가정하지 않고 한 `trace_id`의 요청·응답을 먼저 확인합니다.

## 동작 방식

1. 제보한 HTTP 상태와 실제 응답을 비교합니다.
2. NVIDIA Nemotron 모델이 API 계약, 백엔드 오류, 마이그레이션 상태 중 필요한 읽기 전용 도구를 선택합니다. 필수 근거가 빠지면 Python 검증기가 보완합니다.
3. 검증기가 근거가 확인된 불일치만 판정합니다. 지원하는 오류 유형이라면 고정된 pytest 템플릿을 생성해 샘플 앱·인메모리 SQLite에서 수정 전후를 비교합니다.

| 사례 | 확인할 내용 |
| --- | --- |
| `contract-001` | “500” 제보와 실제 422를 구분하고 `user_id` / `userId` 계약 불일치를 재현 |
| `migration-002` | 실제 500의 로그와 V11 / V12 이력을 대조하고 누락된 `phone` 컬럼을 재현 |
| `claim-003` | “500” 제보와 실제 400을 구분하고 근거 없는 500 원인을 만들지 않음 |
| `unknown` | 요청을 식별할 수 없으면 모델 호출 없이 추가 정보를 요청 |

화면에는 판정 근거, 도구 호출 기록, 모델 사용량, 테스트 결과가 표시됩니다. 최종 판정과 요약은 검증된 증거에서 생성합니다.

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
```

## 테스트와 범위

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
```

현재 입력 자료는 저장소에 포함된 **합성 사례 3개**입니다. 임의의 서비스 로그나 저장소를 분석하지 않습니다. 수정 전 실패와 후보 수정 후 통과는 샘플 앱·격리 DB에만 해당하며 실제 서비스가 수정됐다는 뜻은 아닙니다. 모델이 작성한 임의 코드는 실행하지 않습니다.

핵심 화면은 NVIDIA NIM을 직접 호출합니다. 별도의 [NeMo Agent Toolkit 워크플로](nat_workflow.yml)도 포함돼 있지만 호스팅 모델 응답 지연으로 간헐적으로 시간 초과가 납니다. 해당 경로는 `python -m scripts.run_nat contract-001`로 실행할 수 있으며 재현 테스트는 수행하지 않습니다.
