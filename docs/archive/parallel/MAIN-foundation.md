# 메인 세션 기반 작업 인계

> 과거 기록입니다. 당시 계획·검사·제약을 보존하며, 현재 상태는 [구현 상태](../../current-state.md)를 기준으로 확인하세요.

후속 사전 통합 작업은 [사전 통합 점검](../preintegration-checks.md)에 있다. A·D 진행 중 고정 사본 회귀·기억/매뉴얼/재시작을 확인했고, 완료된 C의 저장 재시도 결함을 `_insert_run`에서 국소 보완했다. 기존 C 포함 63개 전용 검사를 통과했다. 아래 처음 기반 작업의 “다른 세션 파일 미수정” 기록과 구분한다.

2026-09-28. 사용자 요청: A~D가 병렬 작업 중인 상태에서 문서화와 실제 테스트를 위한 최소 기반을 먼저 진행한다. 다른 세션의 소유 파일은 수정하지 않았다. 실모델/실프로젝트 검증 대기는 유지했다.

## 메인 소유 산출물

- `scripts/prepare_verification.py`: stdlib 기반 환경 기록·변경 포함 소스 고정·오프라인 CLI smoke. 새 의존성 없음.
- `tests/test_main_verification_foundation.py`: 스냅샷 일관성, 비밀/DB 제외, 환경 변수 제외, Python 연결 차단, 저장 판정·DB 행 대조, 출력 경계의 10개 검사.
- `docs/archive/local-verification.md`: 재실행·화면 준비·실자료 체크·검증 범위.
- 이 인계와 공용 문서의 안내 링크/진행 중 표기.

공개 실행 명령:

```powershell
.\.venv\Scripts\python.exe -B -m scripts.prepare_verification --smoke
```

`--smoke` 없이 실행하면 소스/환경 준비만 한다. 매 실행은 `output/main-foundation`의 고유 하위 경로를 사용한다. 현재 미커밋·미추적 소스를 복사 전후 검증하고, `.env`/기존 DB는 복사하지 않는다. 사본의 결과 DB·로그를 포함한 전체 출력은 자동 제출 자료가 아니다.

## 실제로 확인한 범위

- 소스 사본: `output/main-foundation/20260928T141654Z-7596e40b/snapshot`, 소스 116개, 생성 시 변경 감지 없음.
- 같은 사본의 공개 합성 `ledger-demo` CLI에서 기억 끔/켬 모두 `WORK_CANDIDATE / COMPLETED`. 제보 500/실제 422·호출자 결함 구분, 사건 저장·SQLite 행·모드 저장 일치, 모델 0회 확인.
- 같은 사본에서 씨드의 고정 개발 가입 동작만 실행하고 ID 없는 “방금 가입이 안 돼요. 조사만 해줘.”를 접수. 후보 확인 대기 후 같은 사건을 복원·답변·명시 확인해 `EXACT_ID / WORK_CANDIDATE`와 저장 확인. 수정 작업·원본 적용 없음.
- 외부 모델/OCR 새 호출 0회. 실제 Agolive/운영 접속·전체 회귀·UI 화면·배포는 수행하지 않음.
- 전용 검사 10개 통과(0.26초), JUnit: `output/main-foundation/pytest-results-final.xml`.

근거: 위 실행 폴더의 `foundation.json`, `snapshot/output/main-smoke/{memory_off,memory_on}/incident.json`, `snapshot/output/main-smoke/rough-seed/{rough,confirmed,checks}.json`.

첫 도구 실행의 저장 필드 확인 오류와 pytest 환경 실패는 원자료를 보존했다. 제품 저장 실패와 구분한다. 정확한 결과·한계는 로컬 검증 문서에 있다.

## A~D 및 최종 통합 참고

현재 작업 트리를 수정하며 전체 회귀를 돌리지 않았다. 사본 smoke 성공은 해당 소스 해시·합성 경로에만 적용된다. 각 세션이 편집을 끝내면 새 사본/전체 회귀/실자료 게이트를 진행한다.

B 인계의 책임 근거가 없는 기존 합성 `GUIDANCE` 기대값 충돌은 메인 통합에서 명시된 합성 입력·호출자·버전 어댑터로 해결해야 한다. 일반 422의 책임 판정을 느슨하게 만드는 해결은 사용하지 않는다.

D의 패키징 대상 `scripts/tests/docs`에 메인 파일이 포함되지만 `output/main-foundation`은 제외해야 한다. 사본·키·DB·실행 stderr를 통째로 제출하지 않는다. 최종 의존성/패키지·12건 평가·실모델/실프로젝트·NAT 계측 결과는 이번 기반 성공으로 대신하지 않는다.
