# 검증 절차

명령은 저장소 루트에서 실행합니다. 현재 지원 환경과 설치는 [시작하기](getting-started.md)를 먼저 보세요. 완료 증거는 [최종 로컬 통합](../validation/main-integration.md)에 기록돼 있습니다.

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
