# 현재 구현과 목표 서비스의 간격

기준: 2026-09-28 저장소 코드·문서 확인. 이 문서는 **현재 사실**만 기록한다. 최신 상태를 확인할 때는 코드를 실행하고 결과를 날짜와 함께 갱신한다. 목표 설명은 [문서 지도](README.md)의 나머지 문서를 본다.

## 구현된 것

- [루트 Streamlit 화면](../app.py)은 합성 trace ID 3개와 식별 실패 사례를 선택해 제보 텍스트의 일부 명시 항목을 관측과 비교한다. 제보가 없어도 관측된 합성 사건을 조사한다.
- [NVIDIA 직접 호출](../tracebridge/agent.py)은 Nemotron/NIM 한 번의 응답에서 API 계약·백엔드 오류·마이그레이션 상태의 읽기 도구를 선택한다. 최종 판정은 [규칙 코드](../tracebridge/triage.py)가 다시 조회한 자료로 만든다. 별도의 [NeMo Agent Toolkit 워크플로](../nat_workflow.yml)는 합성 자료의 읽기 도구를 최대 3회 반복한다.
- [제보 항목 대조](../tracebridge/claims.py)는 명시된 HTTP 상태·메서드·경로·`○○ API` 작업명과 확인 불가한 원인 추측을 일부 추출한다. 자유로운 자연어 전체를 검증하지 않는다.
- [증거 소스](../tracebridge/evidence.py)는 합성 fixture와 1 MB 이하 로컬 JSON 사건 묶음을 지원한다. 로컬 묶음은 오프라인 CLI에서만 판정한다.
- [샘플 재현](../tracebridge/repro.py)은 두 고정 pytest 템플릿을 샘플 앱·인메모리 SQLite에서 실행한다. 이는 대상 서비스 수정 검증이 아니다.
- [Agolive 페이지](../pages/1_Agolive_Investigation.py)는 로컬 코드와 제보자가 제공한 로그를 읽기 전용으로 검색한다. 로컬 Docker 로그는 사용자가 선택한 경우에만 조회한다. 별도 GPT 호출은 명시적 선택이며 [연결 문서](../AGOLIVE_INTEGRATION.md)에 제약이 기록돼 있다.
- 프로젝트용 [Skill](../skills/tracebridge-triage/SKILL.md)과 [SkillSpector 정적 검사 결과](../SKILL_SCAN_REPORT.md)가 있다.

## 구현되지 않았거나 실자료로 검증되지 않은 것

- 불특정 다수의 제보 접수, 조직/프로젝트별 인증·인가, 사용자별 사건 상태 및 알림, 다중 제보 중복 묶음.
- 제보만으로 실서비스 요청 후보를 자동 검색하는 로그/APM 연결, 실제 배포 SHA와 실행 코드 상관, 운영/스테이징 DB의 읽기 전용 진단 연결.
- 범용 사용자 실수/실제 결함 분류, 중요도·소유자·작업 에이전트·보고 수신자의 정책 기반 라우팅.
- 실프로젝트의 재현, 수정 diff/PR, CI·배포·배포 후 검증, 자동 해결/롤백.
- 사건 영속화·유사 사건 기억 검색, 다중 사용자 검토, 제품 지표 및 실제 NeMo Microservice 통합.
- OpenShell 실행 격리, 전체 API/브라우저 능동 검사와 취약점 검증.

Agolive의 로컬 코드 검색 결과는 실제 배포 버전의 원인 확정이 아니다. README에 따르면 로컬 Docker가 꺼져 있었고 실제 OpenAI API 호출은 외부 코드 전송 승인 검토에서 거절돼 검증하지 못했다. 이 상태는 환경이 바뀌면 다시 확인한다. NAT 경로에는 간헐적 모델 시간 초과가 문서화돼 있다.

## 실행·검증 기준

설치는 [루트 README](../README.md)의 Python 3.12 절차를 따른다. 오프라인 예시는 `python -m scripts.demo_cli --bundle examples/backend_incident.json --without-report`, 테스트는 `python -m pytest -q -p no:cacheprovider tests`다. 2026-09-28 문서 점검에서 **22개 테스트가 통과**했지만, 이는 합성 사례·제한된 로컬 입력·가짜 GPT 응답 검사이며 실제 다중 사용자 서비스나 실사건 자동 해결의 증거가 아니다. 새 구현 작업 전 이 명령을 다시 실행해 기준값을 갱신한다.

현재 작업 트리에는 이 문서화 작업이 만든 변경 외의 수정이 있을 수 있다. 다음 작업자는 `git status`로 확인하고 기존 작업을 보존한다. 이 문서의 구현/미구현 표를 갱신하기 전에는 실제 호출·실행·결과를 확인한다.
