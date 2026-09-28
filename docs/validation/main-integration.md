# A~D 최종 로컬 통합

기준: 2026-09-28. A~D 구현을 함께 검사한 메인 기록이다. 실모델·OCR·실프로젝트 검증은 사용자가 나중에 진행하기로 한 지시를 유지했다. 이번 새 외부 모델 호출은 0회다.

## 지금 사용할 수 있는 범위

| 사용자 목표 | 현재 제공하는 동작 | 범위 |
| --- | --- | --- |
| 대충 증상이나 사진 제보 | 자연어/사진 접수, 추가 질문, 현재 동작 후보 확인, 같은 사건의 후속 답변 | 사진 외부 해석은 선택 실행. 현재 한국어 날짜는 KST 기준의 지원 문법 |
| 단순 입력 누락과 제품 결함 구분 | 계약·실제 요청·입력·호출자·DTO·버전 근거로 안내/조사/작업 후보 판정 | HTTP 4xx만으로 사용자 실수를 확정하지 않음. 부족·충돌·절단이면 보류 |
| 상황에 따른 조사와 수정 작업 | 조회 변경·반증에 따른 가설 변경, 정책을 통과한 수정안 준비·동일 검사·회귀 | 일반 프로젝트는 조사. 실제 편집은 등록된 신뢰 가능한 씨드 사본 한 대상 |
| 프로젝트 매뉴얼의 축적 | SQLite 재시작·조회·검토, 정확/FTS5 검색, 적용 조건·반증·폐기, 검토 매뉴얼 내보내기 | 승인과 원인/수정 검증은 별개. 과거 자료는 현재 근거로 재확인 |
| 제보 없는 지속 관리 | 제공한 관측을 조사할 수 있음 | 상시 감시·전체 API/사용자 여정 검사·성능 탐지·배포/복구는 미구현 |

등록 JSON 프로필로 Agolive 이외 코드 루트·로그·OpenAPI·DTO·호출자 자료·버전 파일을 연결할 수 있다. 로컬 버전 snapshot은 실제 배포 SHA 관측과 구분하며, `policy_refs`는 수정 권한을 주지 않는다. 공개 다중 사용자 서비스의 인증·인가와 OS/OpenShell 격리는 구현 범위 밖이다.

## 메인 통합 보완

1. CLI 표시 정보 때문에 저장만 재시도가 거절되던 문제를 수정했다. 같은 실행의 원 결론/개인 입력이 달라지면 계속 거절하며, 저장된 정확한 기록의 재저장과 예전 digest의 동일 입력을 허용한다.
2. 예전 긍정 예제에 합성임을 명시하고 현재 입력·타입·호출자·계약/DTO/버전 출처를 붙였다. 호출자 파일 해시를 다시 확인한다. 알려진 요청 ID만으로 이 근거를 일반 자료에 삽입하지 않는다.
3. 같은 소스의 동일 로그 발췌가 한도를 채워 다른 소스의 반증이 밀리던 문제를 중복 제거로 수정했다. 원자료의 관측 개수·불완전성은 그대로 유지한다. 기존 100건+뒤쪽 500 반증 검사에서 500/422와 보류를 확인한다.
4. 현재 캡처 기록이 고정 씨드 snapshot 사건을 가리던 수정 준비 결함을 수정했다. 고정 사건은 정책 파일/해시가 맞는 snapshot에서, 캡처 사건은 확인된 현재 캡처에서 재확인한다.
5. ZIP 압축 해제 검사에서 등록 프로필의 JSONL 입력 누락을 발견해 포함 규칙을 고쳤다. 전체 회귀 후 이 패키징 보완의 기존 DB/시크릿 제외 검사와 D 평가·패키징 검사 **27개를 별도로 통과**했다.
6. 기존 검사의 도구 허용 목록·불완전 자료 보류·명시 UNVERIFIABLE 항목·Vision 시간 초과·새 호출자 근거로 조기 거절되는 경로를 최신 계약에 맞췄다. 실행 권한을 넓히지 않았다.

## 확인 결과

**최종 전체 회귀 499개 통과, 174.74초.** Streamlit AppTest·CLI·수집/충돌·정책·사본 diff/동일 검사·SQLite·매뉴얼·계측/평가 검사를 포함한다. 결과는 아래 메인 파일에 기록했다. 첫 통합 실행은 484 통과/11 실패, 수정 경로 재검사는 7 통과 후 나머지·신규 9 통과였다. 최초 실행의 Windows 임시 상위 폴더 부재는 별도 환경 오류였으며, 최종 검사는 상위 폴더를 만든 정상 사용자 권한에서 실행한다.

- 전체 회귀: `output/main-integration/regression-final.xml`, `regression-final.log`.
- 12건 평가: `output/main-integration/evaluation-local`, `evaluation-doubles` 아래 실행별 manifest/results/summary.
- NAT 로컬 주 조사: `output/main-integration/nat` 아래 같은 실행 ID의 관측 수/종료 상태.
- 변경 포함 고정 사본 189파일에서 기억 끔/켬 두 CLI·독립 SQLite 모두 통과. 기억/매뉴얼 재생 CLI도 종료 0. 등록 씨드와 정책의 원래 해시 7/7 보존. `git diff --check` 통과.
- 묶음: `output/main-integration/packages`의 DRAFT ZIP. 파일별 해시·CRC·압축 해제 후 재생 결과는 ZIP 옆 `.verification.json`을 확인한다. 기존 Python 환경의 재생이며 새 의존성 설치 성공을 뜻하지 않는다.
- 기존 가상 환경: `pip check` 통과. Windows CPython 3.12.7 lock 제공. 새 환경의 의존성 설치와 wheel 해시는 미검증.

| 고정 12건 평가 | 기억 끔 | 기억 켬 | 해석 |
| --- | --- | --- | --- |
| 로컬 오프라인 | 기대 동작 10/12 | 10/12 | 적응형 모델/서빙 시간 초과를 오프라인 경로가 실행하지 못함 |
| scripted doubles | 기대 동작 12/12 | 12/12 | 실행 상태는 11 COMPLETED + 예상 시간 초과 1 FAILED를 보존 |
| 단일 프롬프트/일반 코딩 에이전트 | 각 12 BLOCKED | 해당 없음 | 비교 실행 기록 미제공. 우위/절감/자가학습 효과를 주장하지 않음 |

규칙 조건도 10/12다. 평가는 진단 범위이며 12건의 수정 성공률이 아니다. NAT 1.8의 실제 manager에 로컬 주 조사 도구 완료 2건을 전달했고 모델 0회/WAITING_CONTEXT였다. 실제 모델을 포함한 NAT 종단 성공은 미검증이다.

## 바로 실행하는 로컬 검증

```powershell
# 변경 포함 고정 사본 + 두 기억 모드 CLI + 독립 SQLite. 외부 통신 차단.
.\.venv\Scripts\python.exe -m scripts.prepare_verification --smoke

# 접수 → 재시작 → 검토 → 반복/다른 원인/충돌 → 매뉴얼
.\.venv\Scripts\python.exe -m scripts.replay_incident_memory

# 별도 등록 프로젝트의 공통 계약 판정
.\.venv\Scripts\python.exe -m scripts.investigate_report --profile examples/parallel_b/ledger_demo/profile.json --report '계정 생성이 안돼요 requestId=ledger-001' --db output/manual-check/incidents.sqlite3

# 화면: 외부 분석/수정 제안은 실제 검증을 재개할 때 선택
.\.venv\Scripts\python.exe -m streamlit run app.py

# 최종 회귀와 평가 (평가 실패/비교 미실행은 종료 코드에도 보존)
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
.\.venv\Scripts\python.exe -m scripts.evaluate_incidents --mode doubles
```

## 대회 설명과 남은 실검증

소개: **모호하거나 잘못 표현된 제보를 현재 관측과 연결하고, 검토된 사건 기억을 재확인해 허용된 조사·수정안 검증으로 이어주는 프로젝트 운영 에이전트.**

Creative Use-case 방향에 부합하며, NeMo Microservices 설명에는 기존 실제 NeMo Retriever OCR 실행 기록을 사용한다. 일반 NIM/NAT 설치를 그 필수 조건의 단독 근거로 쓰지 않는다. Skill API 미션 인정 범위·개인 교육 증빙·최신 신청 조건은 [제출 확인표](../competition/submission-readiness.md)의 미확인 항목이다. 모든 대회 요건을 충족했다고 확정하지 않는다.

실검증 재개 후 한 허가된 개발 사건의 화면/동작/시각/환경, 실제 로그·실행 SHA·계약·호출자 근거를 연결하고 현재 최종 스키마의 모델 반복·OCR·서빙 실패·NAT 종단을 확인한다. 같은 입력/권한/예산에서 단일 프롬프트·코딩 에이전트·기억 끔/켬을 비교한다. 후보 검증은 원본 적용·배포·회복과 구분한다. 묶음은 내부 검토용 DRAFT이며 외부 제출은 하지 않았다.
