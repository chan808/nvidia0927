# 세션 A 인계 — 수집·주 조사·서비스 연결

기준일: 2026-09-28. 작업 시작 HEAD: `34ce50b0cf6f214726e64a3fbee1f4195c397d4e`, Windows / Python 3.12.7 (`.venv`). 기존 staged/unstaged/미추적 변경이 있는 동일 workspace에서 A 소유 파일만 편집한다. 기준 상태·파일 해시는 `output/parallel-a/baseline.json`에 기록한다.

## 소유 범위와 실행 제한

- `tracebridge/project_sources.py`, `report_agent.py`, `report_intake.py`, `report_contract.py`, `report_service.py`, `deadline.py`, `seed_project.py`, `change_policy.py`, `change_proposal.py`, `change_worker.py`
- `pages/2_Report_Agent.py`, `scripts/investigate_report.py`
- 새 `tests/test_parallel_a_*.py`, 이 인계 문서. 전용 산출물·DB: `output/parallel-a`.
- 기존 변경·`examples/seed_signup`·`examples/change_policy`는 보존한다. 다른 세션 파일·기존 테스트·공용 문서는 편집하지 않는다. 새 외부 모델/OCR 호출·실프로젝트 실행·원본 적용·배포는 수행하지 않는다.

## 시작 시 확인한 결함과 우선 작업

`project_sources.py`가 로그 입력을 300 KB / 5,000줄로 먼저 자르고, Docker stdout도 절단한다. 일부 상위 절단 상태가 최종 완전성 판단에 전달되지 않을 수 있다. 관측 범위 수집을 화면·모델 발췌 제한과 분리하고, 바이트·줄·관측 수·timeout/소스 실패를 종단으로 전달한다. 짧은 로그·약 481 KB·짧은 줄 6,000개의 같은 사건 500/422 충돌을 전용 doubles 검사로 확인한다.

## 외부 공개 API 연결점 (시작 상태)

- B: `triage` / `evidence`의 호환 API를 사용 중. 공통 계약 분석, 프로젝트 profile/계약 도구의 공개 API는 `B-handoff.md`와 실제 구현이 준비되면 읽어 소비한다.
- C: 현재 사건 기억 검색·저장 API를 사용 중. keyword-only `search_memory(enabled=False)`와 `export_manual`의 공개 API가 준비되면 화면·CLI·주 조사에 연결한다.
- D: 주 조사 도구·모델·시간·종료 이벤트를 받을 `nat_observability` API가 준비되면 연결한다. 실제 NAT 종단 실검증은 미완료로 남긴다.
- 시작 시 `docs/parallel` 인계와 B/D 새 모듈은 없었다. 기다리는 동안 로그 수정과 A 전용 doubles 검사를 먼저 수행한다.

## 검사·결과·미완료

진행 중. 완료 후 수정 파일, 세 절단 재현의 관측·완전성·라우팅 결과, 공개 API 연결, 전용 검사 명령·결과, 기준 파일 보존 여부를 갱신한다. 전체 회귀·실제 모델 조사·실프로젝트 검증·원본 적용/회복은 메인 통합 또는 사용자 실검증 재개 전까지 미완료다.
