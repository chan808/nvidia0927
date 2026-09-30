# 검증 절차

명령은 저장소 루트에서 실행합니다. 현재 지원 환경과 설치는 [시작하기](getting-started.md)를 먼저 보세요. 최신 전체 회귀와 Linux CI 차이는 [CI 재검증](../validation/ci-portability.md), 경로·연결 진단은 [프로젝트 연결 검증](../validation/project-connection.md), 공개 자동 처리는 [기본 서비스 검증](../validation/basic-service.md), 이전 적용 후 확인은 [서비스 확인 검증](../validation/project-recovery.md), 저장·검색 통합은 [PostgreSQL 재검토](../validation/postgres-rag-review.md)에 기록돼 있습니다. [최종 로컬 통합](../validation/main-integration.md)은 이전 단계의 기록입니다.

## 오프라인 기능 확인

```powershell
# 소스 고정 + 기억 끔/켬 두 CLI + 독립 SQLite
.\.venv\Scripts\python.exe -m scripts.prepare_verification --smoke

# 접수 → 재시작 → 검토 → 반복/다른 원인/충돌 → 매뉴얼
.\.venv\Scripts\python.exe -m scripts.replay_incident_memory
```

첫 명령은 변경 포함 소스를 새 `output/main-foundation/<실행>/snapshot`에 복사하고 복사 전후 파일 해시를 확인합니다. 검사 CLI에는 제공자 키를 전달하지 않고 Python 네트워크 호출을 차단합니다. OS/OpenShell 격리의 증거로 사용하지 않습니다.

재생 명령은 합성 사건과 별도 DB를 사용합니다. 통과했다고 실제 운영 문제나 기억 효과가 검증된 것은 아닙니다.

## 회귀와 평가

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
.\.venv\Scripts\python.exe -m scripts.evaluate_incidents --mode local
.\.venv\Scripts\python.exe -m scripts.evaluate_incidents --mode doubles
git diff --check
```

정식 회귀 범위는 `tests/`입니다. `generated/`의 수정 전 샘플 테스트는 별도입니다. 평가 입력과 재현 방법은 [평가 자료](../../examples/evaluation/README.md)에 있습니다. 미실행 비교와 예상 시간 초과도 상태·종료 코드에 보존하므로 평가의 종료 1을 무조건 러너 결함으로 읽지 않습니다.

기본 검사에서는 PostgreSQL 전용 사례를 건너뛴다. [중앙 DB 가이드](postgres-rag.md)의 선택 의존성을 설치하고, 전용 테스트 DB를 생성·삭제할 수 있는 **격리된 PostgreSQL** URL을 `TRACEBRIDGE_TEST_DATABASE_URL` 환경 변수에 설정한 뒤 같은 pytest 명령을 실행해야 중앙 저장·검색·이전·적용 보고까지 검사한다. URL은 비공개 설정에서 읽고 명령 인수·Git·검사 로그에 넣지 않는다. 실제 조사 대상 서비스나 운영 DB를 테스트 서버로 지정하지 않는다.

GitHub의 `Regression` 작업은 Ubuntu·Python 3.12와 격리된 PostgreSQL/pgvector에서 프로젝트의 버전 고정 직접 의존성을 설치해 검사한다. 저장소의 `requirements-*.lock`은 Windows amd64 / Python 3.12.7 재현용이다. Linux에서 설치된 선택적 간접 의존성 버전을 Windows 잠금 파일과 같다고 가정하지 않는다. 파일 보존 검사는 OS가 기록한 원래 바이트를 기준으로 비교한다.

공개 처리 또는 회복 흐름만 확인하려면:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_public_service.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_project_recovery.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_project_connection.py -q
# 격리된 PostgreSQL 환경 변수를 설정한 환경에서:
.\.venv\Scripts\python.exe -m pytest tests/test_postgres_storage.py -q
```

새 기능의 운영·연결 조건은 [daily 요청 관측](daily-observation.md), [적용 후 서비스 확인](project-recovery.md)을 따른다. 합성 HTTP 검사 통과와 실제 사용자 장애 회복을 구분한다.

## NAT 로컬 계측

```powershell
.\.venv\Scripts\python.exe -m scripts.run_nat contract-001
.\.venv\Scripts\python.exe -m scripts.run_nat --main-report '가입이 안 돼요. 확인해 주세요.' --repo examples/evaluation/project --memory-off
```

첫 명령은 오프라인 fixture, 두 번째는 NAT manager를 사용하는 로컬 주 조사입니다. 실제 모델을 포함한 종단 성공과 구분합니다. 외부 모델은 `--live`를 선택한 경우에만 호출합니다.

## 실제 프로젝트를 검증할 때

한 허가된 사건의 화면/동작, 발생 시각·시간대·서비스·환경, 요청 ID 또는 현재 로그, 실제 실행 SHA, API 계약·입력·호출자 자료를 준비합니다. 등록 프로필과 로그 수집의 범위·완전성을 확인한 뒤 제보부터 후속 답변까지 실행합니다.

원인 후보와 재현 성공, 사본 수정 성공과 원본 적용·서비스 회복을 각각 기록합니다. 자료가 없거나 충돌하면 보류가 정상 결과입니다. 같은 입력·권한·예산에서 비교한 실제 결과가 있어야 우위나 시간 절감을 주장할 수 있습니다.

더 자세한 당시 준비 과정은 [초기 실검증 준비 기록](../archive/local-verification.md)에 보존했습니다.
