# TraceBridge

거친 자연어·사진 제보를 현재 관측과 연결하고, 필요한 조사와 허용된 수정안 검증으로 이어주는 로컬 프로젝트 운영 에이전트입니다. 검토된 사건 지식은 다음 조사에서 현재 근거로 다시 확인합니다.

## 무엇을 하나요?

1. 증상이나 사진을 받아 같은 사건의 현재 요청·로그·계약·코드를 찾습니다.
2. 안내, 추가 질문, 조사, 수정 후보를 구분합니다. 4xx만으로 사용자 실수를 확정하지 않습니다.
3. 필요한 경우 Nemotron이 다음 조회와 가설을 선택합니다. 자료가 부족하거나 충돌하면 보류합니다.
4. 소유자가 등록한 실제 프로젝트의 별도 사본에서 수정 전후·회귀 검사를 수행하고, 검토한 후보만 허용된 원본에 적용합니다.
5. 사건·작업·검토 이력을 저장하고, 검토 카드와 매뉴얼을 다음 조사에 재사용합니다. 중앙 서버는 PostgreSQL + pgvector, 로컬 저널은 SQLite를 선택해 사용합니다.
6. PC의 원본 적용 결과를 서버에 보고하고, 등록된 API 동작·회귀·실행 snapshot을 관측하여 회복 확인 또는 사건 재개로 이어집니다.

## 빠르게 시작하기

지원 기준은 **Windows / Python 3.12.7**입니다. 설치와 키 설정은 [시작하기](docs/guides/getting-started.md)에 있습니다. 설치 후 실행하세요.

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

`http://localhost:8501`에서 **개발 가입 동작 실행 → “방금 가입이 안 돼요. 고쳐줘.” 접수 → 이 동작이 맞아요** 순서로 확인합니다. 외부 분석과 수정 제안은 화면에서 선택합니다.

외부 모델 없이 별도 등록 프로젝트를 조사하려면:

```powershell
.\.venv\Scripts\python.exe -m scripts.investigate_report --profile examples/parallel_b/ledger_demo/profile.json --report '계정 생성이 안돼요 requestId=ledger-001' --db output/manual-check/incidents.sqlite3
```

## 현재 범위

자연어·사진 접수, 사건 연결과 후속 답변, 공통 계약 판정, 적응형 조사, SQLite 기억·검토·매뉴얼, 제한된 수정안 검증을 제공합니다. NVIDIA NIM/Nemotron과 NeMo Retriever OCR는 선택한 외부 분석 경로에서 사용합니다. NAT 주 조사 계측도 연결돼 있습니다.

**로컬 운영과 소유자 전용 원격 시범을 제공합니다.** 실제 다중 경로·서비스 등록, 코드/로그 조사, 등록된 검사로 일반 프로젝트 수정 후보 검증과 검토 후 원본 적용을 사용할 수 있습니다. 서버 API·PC 실행기로 작업과 NVIDIA 호출을 분리합니다. 공개 다중 사용자 인증, 원격 사진 처리, 자동 배포·서비스 회복은 후속 범위입니다. 로컬 신뢰 코드 실행과 Docker 설정의 격리 범위를 구분합니다.

현재 사실은 [구현 상태](docs/current-state.md), 최신 적용 보고·회복 검사와 전체 회귀는 [서비스 확인 검증](docs/validation/project-recovery.md), 이전 **650개 회귀**와 실제 개발 HTTP 연결은 [daily 요청 관측 검증](docs/validation/daily-observation.md), PostgreSQL 경계 수정은 [재검토 기록](docs/validation/postgres-rag-review.md)에서 확인하세요. 단순 프롬프트 대비 우위와 기억의 정확도·속도 향상은 아직 입증하지 않았습니다.

## 문서

| 필요한 정보 | 문서 |
| --- | --- |
| 설치와 첫 실행 | [시작하기](docs/guides/getting-started.md) |
| 제보·후속 답변·기억·수정안 CLI | [사용법](docs/guides/usage.md) |
| 실제 프로젝트·PC 실행기·수정 검사 | [실제 운영 사용법](docs/guides/real-projects.md) |
| daily 요청 ID·로그·실행 빌드 연결 | [실제 요청 연결](docs/guides/daily-observation.md) |
| 원본 적용 보고·API 회복 검사·사건 재개 | [적용 후 서비스 확인](docs/guides/project-recovery.md) |
| 우선순위·작업 이력·선택적 벡터 RAG | [실사용 고도화](docs/guides/operational-hardening.md) |
| PostgreSQL 이전·RAG·전체 개선 설계 | [통합 개선안](docs/design/postgres-rag-evolution.md) |
| PostgreSQL 이전·복구·비동기 RAG 실행 | [중앙 DB 운영](docs/guides/postgres-rag.md) |
| 테스트와 실자료 검증 | [검증 절차](docs/guides/verification.md) |
| 제품 목표·구조·정책 | [문서 안내](docs/README.md#설계) |
| NVIDIA 활용과 대회 자료 | [대회 조건](docs/competition/requirements.md) |
| 전체 문서와 과거 기록 | [문서 안내](docs/README.md) |
