# TraceBridge 신청서 초안 — DRAFT

2026-09-28 A~D 통합 후 갱신한 문안입니다. 외부 제출을 하지 않았습니다. 최신 검증 범위는 [메인 통합](docs/main-integration.md), 과거 실제 호출은 [기존 실행 기록](docs/pre-final-readiness.md)에서 확인합니다. 아래 글자 수는 문안 작성 기준이며 최신 폼 제한은 미확인입니다. 팀명·개인 정보·제출 필드 값을 추측하지 않습니다.

## 서비스명

TraceBridge — 거친 오류 제보를 현재 근거와 검토 가능한 수정안으로 연결하는 에이전트

## 해결하고자 한 문제 (300자 내외 초안)

“방금 가입이 안 돼요”라는 제보나 오류 사진에는 발생 시각·환경·요청 정보가 빠져 있습니다. 보고한 500과 실제 응답 422가 다르더라도 가입 실패는 남아 있을 수 있습니다. 개발자는 로그·요청·계약·호출자 코드·실행 버전을 다시 모아 책임과 조치 대상을 확인합니다. TraceBridge는 자연어와 사진을 현재 관측에 연결하고, 확인한 사실과 부족한 자료를 나누며, 허용된 수정안을 같은 검사로 검증해 팀이 검토할 수 있도록 돕습니다.

## 서비스 소개 (500자 내외 초안)

사용자는 자연어와 선택 사진으로 시작합니다. 사건 후보가 여러 개면 필요한 확인을 요청하고, 현재 로그·계약·코드·버전으로 다시 판단합니다. NVIDIA Nemotron 기반 조사 루프가 도구와 검색 대상을 선택하고, 프로그램이 수집 완전성·관측 충돌·근거 출처·정책을 검사합니다. 등록된 개발 씨드의 호출자 키 오류는 수정 전 실패, 후보 사본의 diff, 동일 검사 성공과 회귀 결과를 남기고 검토 대기로 종료합니다. SQLite 사건 기억은 검토된 과거 확인 순서를 현재 조사 단서로 제공하고, 적용 조건·반증 조건·출처를 담은 매뉴얼로 내보냅니다. 사본 성공·원본 적용·서비스 회복은 별도 상태입니다. 현재 실제 실행 범위는 개발 씨드와 합성 자료이며, 일반 조사 모델 안정성·실사건·기억 효과·최종 새 환경 재현은 아직 검증을 마쳐야 합니다.

## 기술과 증거 범위

- NVIDIA 호스팅 Nemotron/NIM 조사 경로: 현재 기본 `nvidia/nemotron-3-super-120b-a12b`. 과거 전체 성공과 이후 서빙 실패·개별 최종 스키마 성공을 각각 보존합니다. 새 전체 실검증은 대기 중입니다.
- NeMo Retriever OCR Microservice: `nvidia/nemotron-ocr-v2` 사진 접수의 **과거 실제 호출**을 사용했습니다. [비식별 메타데이터](examples/evaluation/prior-ocr-evidence.json)는 기존 파일의 해시·이미지 해시·반환 상태를 보존합니다. request ID는 미반환이며 이번 준비에서 새 OCR를 호출하지 않았습니다.
- NeMo Agent Toolkit 1.8: 기존 합성 tool-calling 경로와 주 조사 이벤트를 받을 선택 어댑터를 준비했습니다. 실제 NAT manager의 로컬 주 조사 도구 이벤트 전달과 호출 수/종료 상태 일치를 확인했습니다. 실제 모델을 포함한 NAT 종단 검증은 남아 있습니다.
- 프로젝트 SKILL.md·기존 SkillSpector 정적 검사: 조사 지침과 정적 검사입니다. 예선 Skill API 인정·교육 수료 증거로 자동 계산하지 않습니다.
- Python 3.12.7, Streamlit, OpenAI 호환 SDK, SQLite, 고정 재현·회귀 검사. 현재 Windows 환경의 실제 버전을 lock에 고정했고 `.venv`를 변경하지 않았습니다.
- OpenShell/NemoClaw 런타임과 OS 격리는 미검증입니다. 현재 후보 실행은 등록된 신뢰 가능한 작은 씨드의 사본·고정 명령·timeout 범위입니다.

## 2분 시연 구성 — 녹화/재생 여부를 화면과 내레이션에 표시

1. 0~25초: 개발 가입 동작 후 “방금 가입이 안 돼요” 접수. 후보 확인과 현재 관측 재조회. 사진은 합성 이미지 또는 **과거 OCR 실행 기록 재생**이라고 표시합니다.
2. 25~55초: 보고한 500/현재 422, 정상 입력 누락/호출자 결함을 구분합니다. `account_id/accountId` 변형에서도 같은 근거 구조를 보여줍니다. 로그가 불완전하거나 충돌하면 보류합니다.
3. 55~90초: 기존 씨드에서 확보한 수정 전 실패→후보 diff→같은 검사→회귀→`CHANGE_PREPARED / WAITING_REVIEW` 기록을 보여줍니다. 원본 적용·회복 성공으로 읽지 않습니다.
4. 90~115초: 검토 카드의 확인 순서·출처·버전 조건과 매뉴얼. 다른 현재 예외/버전에는 처방을 그대로 확정하지 않는 모습을 보여줍니다.
5. 115~120초: 일반 조사 서빙 실패·실사건·기억 효과·최종 묶음 재현의 남은 검증을 표시합니다. 시간·정확도 우위 수치는 넣지 않습니다.

## 제출 준비 파일과 실행

```powershell
.\.venv\Scripts\python.exe -m scripts.evaluate_incidents --mode local
.\.venv\Scripts\python.exe -m scripts.evaluate_incidents --mode doubles --conditions rules tracebridge_memory_off tracebridge_memory_on
.\.venv\Scripts\python.exe -m scripts.run_nat contract-001
.\.venv\Scripts\python.exe -m scripts.package_submission
```

12건 평가의 입력·기대값·출처를 고정했습니다. 규칙·단일 프롬프트·일반 코딩 에이전트·TraceBridge 기억 끔/켬을 구분합니다. 미수행 기준선은 `BLOCKED`, 호출 실패는 실패, 미반환 사용량·사람 시간은 미관측으로 남깁니다. doubles는 실제 모델 선택 품질이나 작업 시간의 증거가 아닙니다.

ZIP은 `output/parallel-d/packages/TraceBridge_DRAFT_*.zip`에 생성되며 내부 manifest와 안내에도 DRAFT를 표시합니다. 새 매뉴얼·평가 입력·잠금 의존성·필요 소스를 포함하고 DB·시크릿·임시 결과를 제외합니다. 메인 통합 묶음과 압축 해제 후 기존 Python 환경에서의 재생 결과는 메인 기록에 있습니다. 새 환경의 의존성 설치와 실제 모델/실사건 검증은 후속입니다. 기존 기록의 GitHub 주소 `https://github.com/chan808/nvidia0927`는 이번 준비에서 최신 코드 게시 상태를 확인하지 않았습니다.

## 대회 확인과 남은 입력

[공식 예선 이미지](https://cdn.day1company.io/prod/uploads/202609/153942-1931/%E1%84%80%E1%85%A2%E1%84%8B%E1%85%AD-01.webp)는 9월 11~28일과 Build NVIDIA Skill API 활용 데모 제출을 안내합니다. [공식 신청 링크](https://docs.google.com/forms/d/e/1FAIpQLScyZ5GYYaCOycNUzXVUTenliEUmSEIdXelVdYphvMvLeLuiHA/viewform)는 로그인 경계 때문에 폼 내부를 조회하지 못했습니다. 기존 초안의 **23:59 KST, 파일 1개, 파일명·개인별 업로드 안내는 이번 폼에서 재확인되지 않았습니다.**

Skill API로 인정되는 endpoint/스킬 형식·필수 실행 증빙, 교육 미션의 개인별 강의/실습/수료 증빙, 트랙 조건의 예선 적용, 필수 제출 필드·파일 제한을 최신 참가자 공지에서 확인해야 합니다. 일반 NIM 호출이나 로컬 스킬 파일이 자동 인정된다고 적지 않습니다. [상세 확인표](docs/submission-readiness.md)에 확인/미확인 상태를 보존합니다. 외부 문의·배포·신청서 입력·제출은 수행하지 않았습니다.
