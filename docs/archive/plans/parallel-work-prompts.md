# 남은 작업 병렬 실행 프롬프트

> 과거 기록입니다. 당시 계획·검사·제약을 보존하며, 현재 상태는 [구현 상태](../../current-state.md)를 기준으로 확인하세요.

시간 제약에 따른 실행 배치다. [최종 설계](final-completion-plan.md)의 완료 기준은 유지하되, 아래 A~D를 동시에 시작한다. **지금 세션은 프롬프트만 제공하며 작업자를 실행하지 않는다.**

동일 workspace에서는 파일 소유권을 지킨다. 일반 Git worktree를 HEAD에서 만들면 현재 미커밋·미추적 구현이 빠지므로, 격리가 필요할 때는 먼저 현재 변경을 포함하는 스냅샷을 마련해야 한다. 기본은 같은 workspace에서 아래 파일을 나눠 수정하는 방식이다.

## 배치와 의존성

| 세션 | 먼저 독립적으로 완성할 것 | 편집 소유권 |
| --- | --- | --- |
| A | R1 로그 완전성, R3 주 조사 안정성, 서비스 연결 준비 | `project_sources.py`, `report_agent.py`, `report_intake.py`, `report_contract.py`, `report_service.py`, `deadline.py`, `seed_project.py`, `change_*.py`, 접수 화면·CLI |
| B | R2 공통 계약 판정, 프로젝트 설정·계약 소스 | `triage.py`, `evidence.py`, 새 `contract_analysis.py`, `project_profile.py`, `project_contracts.py` |
| C | R4 사건 기억·매뉴얼·기억 끔 API | `incident_memory.py`, 사건 기억·재생 CLI, 새 매뉴얼 내보내기 CLI |
| D | R5 평가 세트/러너, R6 제출 준비, NAT 계측 어댑터 | 평가·패키징·NAT 파일, 의존성·제출 문서 |
| 메인 | 완료 산출물 연결, 고정 코드의 전체 회귀, 실제 검증·최종 ZIP | A~D가 편집을 종료한 뒤 필요한 파일 |

A는 B/C/D의 공개 API를 읽어 연결할 수 있다. 다른 세션이 아직 구현 중이면 현재 API와 doubles로 자신의 작업을 진행한다. 외부 모듈을 대신 수정하거나 완료를 가정하지 않는다. 최종 연결이 남으면 인계에 기록한다.

모든 세션은 자신의 전용 검사만 우선 실행한다. 공용 DB·최종 ZIP·서비스 포트를 함께 쓰지 않는다. 검사·산출물은 `output/parallel-a`, `output/parallel-b` 등 고유 경로에 두고 DB 경로를 명시한다. 최종 전체 회귀는 메인에서 코드 편집이 멈춘 시점에 한 번 수행하며, 구체적인 실패가 있을 때만 해당 검사를 재실행한다.

아래 블록은 각각 독립된 새 세션에 그대로 전달한다. 실검증을 나중에 진행한다는 사용자 지시를 유지한다. 공식 문서 조회와 로컬 자료·doubles 검증은 진행할 수 있지만, 실제 NIM/OCR 새 호출·운영 접속·배포·원본 Agolive 수정은 수행하지 않는다.

## 세션 A — 수집·주 조사·서비스 연결

```text
TraceBridge의 병렬 작업 세션 A다. 계획만 설명하지 말고 소유 범위의 최소 구현·검사를 완료해라.

워크스페이스: C:\Users\freetime\Desktop\nvidiatest
docs/archive/plans/parallel-work-prompts.md, docs/archive/plans/final-completion-plan.md, docs/archive/pre-final-readiness.md, docs/design/product.md, docs/policy-security.md를 읽어라. 이번 병렬 배치는 과거의 순차 진행 지시보다 우선하며 완료 기준은 유지한다.

소유 파일:
- tracebridge/project_sources.py, report_agent.py, report_intake.py, report_contract.py, report_service.py, deadline.py, seed_project.py, change_policy.py, change_proposal.py, change_worker.py
- pages/2_Report_Agent.py, scripts/investigate_report.py
- 새 tests/test_parallel_a_*.py, docs/archive/parallel/A-handoff.md
다른 기존 테스트·B/C/D 소유 파일·공용 README/현재 상태 문서는 편집하지 말고 필요한 변경을 인계해라.

우선순위:
1. 로그 절단 결함부터 수정해라. 파일/제공 로그/Docker의 바이트·줄·관측 수 제한과 timeout이 최종 완전성 판단에서 사라지지 않게 한다. 요청 범위의 관측 수집을 표시/모델 발췌와 분리한다. bounded streaming을 사용하고 끝까지 검사하지 못하면 불완전하게 남겨라. 한도 증가만으로 해결하지 마라.
2. 같은 ID·서비스·환경·시각의 500/422 충돌을 짧은 로그, 약 481KB 중간 채움, 짧은 줄 6,000개로 재현해라. 둘 다 수집했으면 충돌 보류, 범위를 다 못 읽었으면 불완전성 보류다. GUIDANCE/WORK_CANDIDATE 및 수정 실행을 확정하지 말고 확보한 관측을 보존해라. 정상 완전 자료의 경로는 유지한다.
3. 주 조사 루프는 모델이 조회 실패/반증 후 도구·검색어·가설을 바꿀 수 있게 유지한다. 최종 반환 스키마·예산·5xx/timeout 경로를 doubles로 검증한다. 중간 조회 성공을 전체 조사 성공으로 기록하지 마라.
4. B의 공통 계약/프로젝트 모듈, C의 search_memory(enabled=False) 및 매뉴얼 API, D의 계측 API를 연결할 자리를 마련해라. 준비된 API만 소비하고, 미완성 모듈을 대신 만들지 마라. 공개 API는 각 docs/parallel/*-handoff.md에 있다.
5. 실제 지원하는 수정 작업자는 현재 등록된 호출자 수정 작업자다. 실행 직전 현재 자료·정책·기준 해시를 재확인하고, 불완전/충돌/사용자 조사만 요청/지원하지 않는 역할에서 실행을 막아라. 저장 재시도와 후속 답변이 작업을 중복시키면 안 된다.
6. 화면·CLI에서 보고 상태 오류와 실제 증상/제품 결함을 구분하고, 조사 종료·후보 검증·원본 적용·회복 상태를 구분해라. 다른 모듈이 완료되면 소비 연결을 마무리하고, 남은 연결은 구체적으로 인계해라.

기존 미커밋·미추적 변경을 보존한다. git reset/clean/전체 staging, 원본 Agolive 수정, 기준 examples/seed_signup 및 examples/change_policy 변경을 하지 마라. 사용자는 실검증을 나중에 한다. 새 외부 모델/OCR 호출·운영 접속·배포 없이 로컬 자료와 doubles로 진행해라. 고유 임시 DB/output 경로를 써라.

먼저 시작 상태·소유 파일·외부 모듈의 필요한 연결점을 A-handoff.md에 적고 작업해라. 검사는 새 A 전용 검사와 git diff --check를 실행한다. 공유 파일 편집 중 전체 회귀 결과를 최종 통과로 주장하지 마라.
최종 보고: 변경 파일, 세 절단 재현 결과, 공개 API/연결 상태, 검사 명령·결과, 새 외부 호출 0회, 남은 실제 검증. 단순히 테스트 수로 완료를 주장하지 마라.
```

## 세션 B — 공통 판정·프로젝트 근거

```text
TraceBridge의 병렬 작업 세션 B다. 소유 범위의 공통 판정과 작은 프로젝트 연결 모듈을 구현·검사해라.

워크스페이스: C:\Users\freetime\Desktop\nvidiatest
docs/archive/plans/parallel-work-prompts.md, docs/final-completion-plan.md의 R2, docs/archive/pre-final-readiness.md, docs/design/product.md, docs/policy-security.md를 읽어라.

소유 파일:
- tracebridge/triage.py, tracebridge/evidence.py
- 새 tracebridge/contract_analysis.py, project_profile.py, project_contracts.py
- 새 tests/test_parallel_b_*.py, tests/fixtures/parallel_b/**, examples/parallel_b/**
- docs/archive/parallel/B-handoff.md
report_agent/project_sources/report_intake/report_contract, 화면·공용 문서·다른 세션 테스트는 편집하지 마라. 주 조사 연결은 A/메인이 담당한다.

구현:
1. user_id/userId와 phone 문자열에 묶인 일반 판정을 제거해라. 필수 필드·실제 필드·검증 가능한 타입 차이는 공통 검사로 계산한다. 표기 유사성은 후보이고 자동 rename 근거가 아니다. 비식별 값·미관측 타입은 확정하지 않는다.
2. 계약 차이·현재 요청·호출자 생성 코드·DTO·버전 출처를 구분한다. 정상 입력 누락과 클라이언트 직렬화 결함을 구분하고, 책임 근거가 없으면 추가 조사한다. 실제 응답 422만으로 제품 정상이나 사용자 책임을 확정하지 않는다.
3. 기존 EvidenceSource 호출과 analyze/route_verdict 반환의 호환성을 유지한다. 필요한 필드/인자는 기본값 있는 선택 항목으로 추가해라. 고정 시연 자료는 회귀 예시로 유지하며 기대값을 약하게 만들어 통과시키지 마라.
4. 프로젝트 설정 파일 하나로 ID·서비스/환경·코드 루트·로그 소스·OpenAPI 경로·버전 관측 방법·등록된 정책 참조를 표현해라. 로컬 JSON/구조화 로그와 OpenAPI 파일부터 지원한다. 플러그인 플랫폼·DB 연결·임의 명령 실행은 만들지 마라. 설정/소스가 실행 권한을 부여하면 안 된다.
5. 계약/DTO가 로그 snapshot에 항상 있다는 전제를 제거해라. 등록된 계약을 별도로 읽어 출처·버전·미관측 상태와 함께 제공한다. 실제 마이그레이션 상태가 없으면 DB 원인을 가설로만 남긴다.

공개 API 초안을 B-handoff.md에 먼저 적어 A가 연결 준비를 할 수 있게 해라. 기존 EvidenceSource와 호환되는 어댑터를 우선하고 API 변경은 최소화한다.
검사에는 user_id/userId와 account_id/accountId, 입력 누락/호출자 결함, 5xx/성공 응답, 계약 부재, 실행 버전 불일치, Agolive 폴더명이 없는 프로젝트를 넣어라. 이미 틀렸다고 보고된 HTTP 상태와 실제 가입 실패가 각각 보존돼야 한다.

기존 미커밋·미추적 변경, 등록 씨드/정책 기준 파일을 보존해라. 새 외부 모델/OCR 호출·실서비스 접속·원본 수정·배포는 하지 않는다. 테스트와 DB/output은 B 고유 경로를 사용한다.
새 B 전용 검사와 git diff --check를 실행한다. 기존 검사와 충돌하면 실패와 필요한 수정 위치를 인계하고 다른 세션 파일을 바꾸지 마라.
최종 보고: 공개 함수·입출력·기본값, 수정 파일, 판정 변형 결과, 검사 명령·결과, A가 연결할 위치, 실제 자료가 없어 남은 한계.
```

## 세션 C — 사건 기억·매뉴얼

```text
TraceBridge의 병렬 작업 세션 C다. 기존 SQLite 사건 기억을 검토 가능한 프로젝트 매뉴얼로 연결해라.

워크스페이스: C:\Users\freetime\Desktop\nvidiatest
docs/archive/plans/parallel-work-prompts.md, docs/final-completion-plan.md의 R4, docs/archive/stages/stage-3-memory.md, docs/design/product.md, docs/policy-security.md를 읽어라.

소유 파일:
- tracebridge/incident_memory.py
- scripts/incident_memory.py, scripts/replay_incident_memory.py, 새 scripts/export_incident_manual.py
- 새 tests/test_parallel_c_*.py, docs/archive/parallel/C-handoff.md
report_agent/report_contract/report_service/화면/공용 문서/다른 세션 테스트는 편집하지 마라. A/메인이 연결한다.

구현:
1. 기존 카드에 증상·관측 특징, 적용 프로젝트/환경/동작/버전, 확인 순서, 반증 조건, 조치·검증 상태, 출처 실행·마지막 확인을 최소 보강해라. 기존 자료를 보존하는 호환 변경을 우선해라.
2. 승인, 원인 확인, 후보 검증, 원본 적용, 회복 확인은 별개다. 사본 성공은 수정안 검증으로 표시하고, 승인만으로 해결 매뉴얼을 만들지 마라.
3. 버전/조건이 다르거나 미관측이면 조사 단서로 낮춘다. 정정·폐기·부적합 조건을 보존하고 과거 자료를 현재 근거로 승격하지 마라. 과거/현재 근거 ID와 실행 출처를 분리한다.
4. 검토된 카드에서 출처와 적용 조건이 붙은 Markdown 매뉴얼을 내보내라. 검증되지 않은 새 결론을 생성하거나 매뉴얼에 실행 권한을 부여하지 않는다.
5. search_memory에 keyword-only enabled: bool=True를 호환 추가해라. False면 검색·카드 전달 없이 동일 구조의 DISABLED 결과를 반환한다. 사건 저장은 계속 가능해야 한다. recheck_memory도 이 상태를 처리해라. 기존 db_path·프로젝트 범위·후속 답변·run_id 중복/충돌·change_jobs를 유지한다.
6. 공개 export_manual API와 입력·출력·enabled 결과 구조를 C-handoff.md에 먼저 적어 A/D가 연결할 수 있게 해라. 벡터 DB·임베딩·모델 학습·새 외부 호출을 추가하지 않는다.

반복 사건, 같은 상태/경로의 다른 원인, 버전 변경, 충돌, 폐기·검토 변경, DB 재연결·이력, 후보 성공/원본 미적용, 검색 끔/저장 유지, 매뉴얼 출처를 검사해라. 공용 실제 DB를 열어 변경하지 말고 C 고유 임시 DB를 명시해라.
기존 미커밋·미추적 변경과 등록 씨드/정책 기준을 보존한다. 새 C 전용 검사와 git diff --check를 실행한다.
최종 보고: 변경 파일·호환/마이그레이션 방법, API 시그니처·사용 예, 매뉴얼 샘플, 검사 결과, A 연결점, 남은 실제 기억 효과 평가. 자가학습이나 시간 절감을 주장하지 마라.
```

## 세션 D — 평가·제출 준비·계측

```text
TraceBridge의 병렬 작업 세션 D다. 평가와 제출 준비를 먼저 완성하고, 최소 NAT 계측 연결을 준비해라.

워크스페이스: C:\Users\freetime\Desktop\nvidiatest
docs/archive/plans/parallel-work-prompts.md, docs/final-completion-plan.md의 R5/R6·대회 확인표, docs/archive/pre-final-readiness.md, docs/design/evaluation-operations.md, docs/competition.md를 읽어라.

소유 파일:
- 새 scripts/evaluate_incidents.py, tracebridge/evaluation.py, tracebridge/nat_observability.py
- tracebridge/nat_plugin.py, nat_workflow.yml, scripts/run_nat.py
- scripts/package_submission.py, pyproject.toml, requirements.txt, 새 requirements-*.lock
- examples/evaluation/**, 새 tests/test_parallel_d_*.py
- docs/competition/submission-draft.md, 새 docs/competition/submission-readiness.md, docs/archive/parallel/D-handoff.md
서비스/판정/기억/화면과 기존 테스트, 공용 README/current-state/문서 지도는 편집하지 마라. 필요한 최종 변경은 메인에 인계한다.

우선순위:
1. 공식 최신 미션·제출 폼에서 기한·Skill API 인정 범위·교육 증빙을 확인해라. 공개 안내와 참가자 전용 미확인 조건을 구분하고, 일반 NIM 호출이 자동 인정된다고 적지 마라. 제품 전면 변경이나 OpenShell 설치부터 시작하지 않는다.
2. 최종 설계의 6유형×2변형=12건 평가 자료·기대값·출처를 고정해라. 판정/보류/잘못된 확정/검증된 조치/질문/수작업/호출·토큰·시간을 결과에서 집계하는 러너를 만들어라. 세트에 맞춘 제품 분기는 넣지 마라.
3. 규칙·단일 프롬프트·개발자+코딩 에이전트·TraceBridge 기억 끔/켬 조건을 나눈다. 현재 자료 접근·허용 명령·재현/회귀는 동일하고, 답변만 하는 기준선과 실제 수정 시간은 섞지 않는다. 사람의 시간이나 미반환 토큰을 만들어내지 않는다. 평가 결과가 다음 실행의 기억에 유입되지 않게 고유/복제 DB를 써라.
4. 현재 공개 API를 사용하는 어댑터와 --live 없이 동작하는 로컬/doubles 경로를 먼저 완성해라. A/B/C API가 아직 없으면 연결 필요 항목을 기록한다. 현재 결함으로 실패한 사례는 실패로 남기고 최종 성능으로 발표하지 않는다.
5. 패키징은 새 매뉴얼/평가 자료·필요 파일을 포함하고 DB·시크릿·임시 산출물을 제외하게 준비한다. 지원 환경의 실제 의존성을 고정하되 현재 .venv를 변경하지 않는다. 다른 세션 편집 중 만든 ZIP은 초안이다. 최종 ZIP·새 환경 종단 재현은 통합 후 메인이 한다. 팀명·제출 필드는 추측하지 않는다.
6. 신청 초안을 자연어/사진→현재 관측→적응형 조사→후보 diff·같은 검사→사건 기억 중심으로 갱신한다. 검증된 것만 완료로 표시하고 미검증 항목은 남긴다. NeMo Retriever OCR 실제 기록을 활용하되 새 호출 성공처럼 재포장하지 않는다.
7. NAT는 주 조사 도구·모델 이벤트를 받을 작은 계측 어댑터를 준비하고 공개 API를 D-handoff.md에 먼저 적어라. 실제 도구·모델 하위 단계가 추적되지 않으면 그 한계를 적는다. 설치나 fixture 실행을 주 흐름 연결 성공으로 집계하지 말고, 프레임워크 전면 교체는 하지 않는다.

기존 미커밋·미추적 변경·등록 씨드/정책 기준을 보존한다. 실제 NIM/OCR 새 호출·운영 접속·실사건 검증·원본 수정·배포·외부 제출은 지금 하지 않는다. 공개 공식 문서는 필요하면 검색한다. 산출물/DB/패키지는 D 고유 경로로 분리한다.
새 D 전용 검사와 git diff --check를 실행한다. 최종 보고: 평가 실행법·API/자료·초안 상태, 의존성/패키징 준비, 대회 확인/미확인 항목, NAT 연결점, 검사 결과, 통합 후 남은 검증. 시간/정확도 우위를 미리 주장하지 마라.
```

## 메인 통합 프롬프트 — A~D 편집 종료 후

```text
A~D의 편집이 끝났다. docs/parallel/*-handoff.md와 실제 diff를 대조해 TraceBridge를 통합해라. 기존 변경을 보존하고 미완료 작업을 완료로 간주하지 마라.

먼저 구현 파일의 상태·해시를 고정한다. A의 수집 완전성 게이트, B의 공통 계약/프로젝트 소스, C의 기억 켬/끔·매뉴얼, D의 평가·NAT 계측을 주 서비스/CLI/화면에 연결해라. 이미 A가 연결한 부분은 다시 구현하지 마라.

검증은 로그 절단 세 재현, 이름을 바꾼 계약 오류, 500 제보/실제 422/호출자 결함의 분리, 적응형 조사 doubles, 같은 사건 후속 답변·재시작·저장만 재시도, 기억 끔/켬 및 다른 원인 기각, 수정 전후 같은 검사·회귀·중복 실행 방지를 우선한다. 기존 기대값을 낮추지 말고 의도적으로 바뀐 동작에는 근거를 남겨라.

코드가 고정된 뒤 전체 tests 회귀와 git diff --check를 실행한다. 구체적 실패만 수정·재검증해라. 12건 평가는 합성/재생/실제 사건을 구분해 수행하고 기억 효과를 그대로 보고한다. 편집 중 세션별 검사 결과를 합산해 전체 통과로 대신하지 마라.

사용자의 실검증 대기는 계속 적용된다. 실제 모델/OCR·프로젝트 실행은 명시적인 재개 전에는 수행하지 않는다. 지금 가능한 새 환경 설치/오프라인 묶음 재현과 문서 정합성 검사를 완료하고 실제 게이트는 미완료로 남겨라.

최종 산출물: 작동하는 통합 서비스, 고정 코드·의존성의 재현 묶음, 평가 결과, 사건 매뉴얼, 최신 README/current-state/제출 문안, 실제 검증 체크리스트. 원본 적용·배포·외부 제출을 하지 않는다. 완성 범위와 남은 게이트를 보고해라.
```
