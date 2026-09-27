# 대회 조건과 NVIDIA 통합 상태

이 문서는 사용자가 제공한 `Creative Use-Case` 트랙 이미지와 공개 [패스트캠퍼스 대회 소개](https://fastcampus.co.kr/NVIDIA_hackathon)를 분리해 기록한다. **이미지의 세부 조건이 온라인 사전 챌린지에 그대로 적용되는지 아직 확인되지 않았다.** 주최 측의 서면 답변이나 최신 신청 양식이 있으면 출처·날짜와 함께 이 문서를 갱신한다. 제품 전체의 설계를 대회용 기술 나열에 종속시키지 않는다.

## 이미지 문구의 해석과 현재 증거

| 이미지 항목 | 현재 저장소에서 확인된 것 | 판정·필요한 증거 |
| --- | --- | --- |
| 목표: 스스로 판단·실행해 실제 문제 해결, 재현 가능한 코드·작동하는 서비스 | 합성 사례 3개와 샘플 앱 재현, Agolive 읽기 전용 코드 검색 | **부분.** 실제 사건의 자동 상관·수정·사후 검증은 미구현. 작동 범위를 합성 데모로 명시 |
| 활용 도구: Nemotron·NIM | `tracebridge/agent.py`에서 NVIDIA Build의 호스팅 Nemotron 호출 | 구현 경로 있음. 라이브 호출 성공 여부는 실행 기록으로 확인 |
| 활용 도구: NVIDIA Agent Toolkit | `nat_workflow.yml`, `tracebridge/nat_plugin.py`의 합성 읽기 도구 | 보조 경로 구현. 간헐적 호스팅 모델 시간 초과가 문서화됨. 실프로젝트 주 흐름 아님 |
| 활용 도구: Skills·Skill Spector | `skills/tracebridge-triage/SKILL.md`, [정적 검사 보고](../SKILL_SCAN_REPORT.md) | 스킬 문서 검사. 런타임 정확성·권한 안전성의 증거 아님 |
| 활용 도구: OpenShell(NemoClaw) | 현재 재현 실행에 연결되지 않음 | 미구현. 이미지의 활용 도구 전부가 필수라는 근거는 별도로 확인 필요 |
| 방향: GPU 자체보다 모델 서빙 기반 에이전트 | 호스팅 NIM 호출 | 방향에는 부합하지만 실사건 에이전트성은 별도 검증 필요 |
| **전체 조건: NeMo Framework 또는 NeMo Microservices 활용** | 현재 의존성은 `nvidia-nat`, 호스팅 NIM. Framework/Microservice 실제 호출 근거 없음 | **이미지의 문구를 엄격히 적용하면 미충족.** 제품/대회 둘 다 실제 통합·실행 기록 필요 |

[NVIDIA NeMo 문서](https://docs.nvidia.com/nemo/)는 Framework, Microservices, Agent Toolkit을 별도 항목으로 나눈다. 이름만 비슷하다는 이유로 현재의 Agent Toolkit 또는 호스팅 NIM을 `NeMo Framework/NeMo Microservices` 사용으로 적지 않는다. 이미지의 `활용 도구` 목록을 모두 필수라고 단정하지 않되 **`전체 조건`은 확인될 때까지 충족으로 표시하지 않는다.**

## 목표 기술 배치

- **Nemotron/NIM:** 모호한 사건에서 가설, 구별할 다음 조회, 필요한 질문을 제안한다. 정확 ID 조회와 계약 비교 같은 단순 단계에는 호출하지 않는다.
- **NeMo Agent Toolkit:** 실사건 읽기 도구를 묶고 조사 실행 경로·토큰·시간을 기록한다. 현재 합성 fixture 전용 설정은 전환 또는 새 워크플로가 필요하다.
- **NeMo Evaluator Microservice:** 승인된 재생 사건에서 도구 선택 정확도·원인 판정·올바른 보류를 평가하는 실제 job을 실행한다. [Agentic 평가 흐름](https://docs.nvidia.com/nemo/microservices/25.10.0/evaluate/flows/agentic.html)은 Data Store·Entity Store·평가 대상/설정 등 준비가 필요하다. `nat eval`만 실행해서 Microservice를 썼다고 주장하지 않는다. Microservice 실행 환경이 없다면 대안인 NeMo Retriever 등도 **실제 배포·호출 결과를 확인한 경우에만** 적는다.
- **Skill/Skill Spector:** 프로젝트별 조사·수정 규칙을 담고 변경 시 스캔한다. 이 규칙은 모델 권한을 부여하는 정책 엔진이 아니다.
- **OpenShell:** 대상 코드 실행의 격리 후보. [파일·네트워크 정책](https://docs.nvidia.com/openshell/reference/policy-schema)을 실제 적용·검증하면 사용 기술로 보고한다. 제품의 논리적 안전 요구는 특정 런타임 없이도 유지한다.

## 제출·시연 증거 규칙

실행 명령, 기준 코드 커밋, 사건 입력과 예상/실제 관측, 모델·도구 호출 기록, Microservice job ID/결과, 재현·회귀 결과, 제한 사항을 재현 가능하게 남긴다. 허가받지 않은 실제 코드·로그·개인정보는 공개 저장소나 모델 요청에 넣지 않는다. 합성 자료·시드한 결함·실제 사건을 명확히 구분한다. 모델이 만든 해결책을 실제 서비스에 적용하지 않았다면 `제안`으로만 표시한다. [현재 상태](current-state.md)의 미구현 항목을 발표 자료에서 완료로 바꾸지 않는다.

대회 결과와 무관하게 제품 가치의 판단 기준은 [제품 계약](product.md)과 [평가](evaluation-operations.md)를 따른다. 다른 트랙·기술 조건이 확인되면 이 문서만 바꾸고 제품의 사용자 문제 정의를 자동으로 바꾸지 않는다.
