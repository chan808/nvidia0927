# 시작하기

## 환경과 설치

지원 기준은 Windows amd64 / CPython 3.12.7입니다. 잠금 의존성은 이 환경에서 설치된 버전으로 고정했습니다. 새 환경 설치와 다른 OS에서의 실행 성공은 아직 검증하지 않았습니다.

저장소 루트에서 실행하세요.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pip install --no-deps --no-build-isolation -e .
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

이미 가상 환경이 있다면 그대로 사용하면 됩니다. 마지막 editable 설치는 NAT 플러그인 등록에도 사용합니다.

## 외부 분석 설정

NVIDIA 외부 분석을 사용하려면 로컬 `.env`에 `NVIDIA_API_KEY`를 설정합니다. 키가 없어도 오프라인 조사와 기존 자료 재생을 사용할 수 있습니다. `.env`는 저장소와 배포 묶음에 포함하지 않습니다.

외부 분석을 선택하면 사진과 선별·비식별화한 제보·코드·로그가 NVIDIA 서비스로 전송됩니다. 사진 해석은 NeMo Retriever OCR와 선택적 Vision, 조사는 Nemotron을 사용합니다. 실제 호출의 과거 검증 범위는 [NVIDIA 기록](../validation/nvidia-validation.md)에 있습니다.

## 첫 화면

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

`http://localhost:8501`의 **제보 · 조사 · 수정안**에서 시작하세요.

1. **개발 가입 동작 실행**으로 현재 개발 관측을 만듭니다.
2. “방금 가입이 안 돼요. 고쳐줘.”처럼 제보합니다.
3. 제시된 동작이 맞으면 **이 동작이 맞아요**로 사건을 확인합니다.
4. 자료가 충분하면 안내·조사·작업 후보를 확인합니다. 부족하거나 충돌하면 추가 질문에 답합니다.
5. 외부 분석·수정안 준비를 선택한 경우 등록된 씨드 사본의 diff와 동일 검사를 확인합니다.

수정안 준비 성공은 원본 적용이나 배포 완료가 아닙니다. 기본 예시는 등록한 개발 씨드에서 실행합니다. 실제 프로젝트는 [프로젝트 연결과 검사 정책](real-projects.md)으로 별도 등록하며, 소유자가 허용한 파일·명령·검토한 diff만 원본에 적용할 수 있습니다. [적용 후 서비스 확인](project-recovery.md)은 등록된 API 기대 결과·회귀·실행 snapshot·관측 기간으로 별도 검증합니다.

## 모델 없이 확인하기

별도 등록 프로젝트의 합성 사건을 조사하고 전용 SQLite에 저장합니다.

```powershell
.\.venv\Scripts\python.exe -m scripts.investigate_report --profile examples/parallel_b/ledger_demo/profile.json --report '계정 생성이 안돼요 requestId=ledger-001' --db output/manual-check/incidents.sqlite3
```

후속 답변·사진·기억·수정안 명령은 [사용법](usage.md), 실행 검사는 [검증 절차](verification.md)를 보세요.
