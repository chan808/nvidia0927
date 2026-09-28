# 대회 조건과 NVIDIA 통합 상태

2026-09-28 사용자가 다시 제공한 `Creative Use-Case` 이미지의 조건을 이번 구현의 필수 기준으로 삼는다. 전체 조건은 **NeMo Framework 또는 NeMo Microservices 활용, 재현 가능한 코드, 작동하는 데모**다. 공개 [대회 소개](https://fastcampus.co.kr/NVIDIA_hackathon)와 이미지 문구는 구분해 보존한다.

## 이미지 문구의 해석과 현재 증거

| 이미지 항목 | 현재 저장소에서 확인된 것 | 판정·필요한 증거 |
| --- | --- | --- |
| 목표: 스스로 판단·실행해 실제 문제 해결, 재현 가능한 코드·작동하는 서비스 | 합성 재현, Agolive 코드 검색, 자연어/사진 제보 UI와 도구 선택 조사 | **부분.** 합성 자료의 실제 모델 연동은 검증. 실사건 자동 상관·수정·사후 검증은 남아 있음 |
| 활용 도구: Nemotron·NIM | 기존 합성 호출 외에 `report_agent.py`에서 읽기 도구를 선택하고 근거를 받아 최종 결과 생성 | [사진 제보의 실제 실행 기록](nvidia-validation.md) 있음. 합성 코드·로그 사용 |
| 활용 도구: NVIDIA Agent Toolkit | `nat_workflow.yml`, `tracebridge/nat_plugin.py`의 합성 읽기 도구 | 보조 경로 구현. 간헐적 호스팅 모델 시간 초과가 문서화됨. 실프로젝트 주 흐름 아님 |
| 활용 도구: Skills·Skill Spector | `skills/tracebridge-triage/SKILL.md`, [정적 검사 보고](../SKILL_SCAN_REPORT.md) | 스킬 문서 검사. 런타임 정확성·권한 안전성의 증거 아님 |
| 활용 도구: OpenShell(NemoClaw) | 현재 재현 실행에 연결되지 않음 | 미구현. 이미지의 활용 도구 전부가 필수라는 근거는 별도로 확인 필요 |
| 방향: GPU 자체보다 모델 서빙 기반 에이전트 | 호스팅 NIM 호출 | 방향에는 부합하지만 실사건 에이전트성은 별도 검증 필요 |
| **전체 조건: NeMo Framework 또는 NeMo Microservices 활용** | `nemo_ocr.py`에서 NeMo Retriever OCR NIM 마이크로서비스를 사진 접수에 실제 연결·호출 | 공식 제품 분류와 실제 호스팅 호출 증거 확보. Framework 또는 전체 NeMo 플랫폼을 설치했다는 뜻은 아님 |

[NVIDIA NeMo 문서](https://docs.nvidia.com/nemo/)는 Framework, Microservices, Agent Toolkit을 별도 항목으로 나눈다. 이번 구현은 [NeMo Retriever의 공식 마이크로서비스 분류](https://docs.nvidia.com/nemo/retriever/)와 [OCR 제품 설명](https://docs.nvidia.com/nim/ingestion/image-ocr/latest/overview.html)을 근거로 OCR 마이크로서비스 활용 경로를 선택했다. 일반 LLM NIM이나 NAT 의존성만으로 이 조건을 충족했다고 적지 않는다. 이미지의 활용 도구 목록 전부를 필수라고 단정하지 않는다.

## 목표 기술 배치

2026-09-28 [접수·판정 통합](stage-2-integration.md)에 이어 [로컬 SQLite 사건 기억](stage-3-memory.md)의 저장·조회·카드 검토·정확/FTS5 검색·현재 재확인을 연결했다. 157개 합성/회귀 검사는 실프로젝트 문제 해결의 증거가 아니다. 실제 Agolive 로그·배포 관측·사건 검증은 미완료이며 사건 기억 작업의 새 외부 모델 호출은 0회다. 수정·비교 평가·제출 단계는 시작하지 않았다. 기억 검색의 실측을 조사 시간·호출 절감으로 확대하지 않는다.

- **Nemotron/NIM:** 모호한 사건에서 가설, 구별할 다음 조회, 필요한 질문을 제안한다. 정확 ID 조회와 계약 비교 같은 단순 단계에는 호출하지 않는다.
- **NeMo Agent Toolkit:** 실사건 읽기 도구를 묶고 조사 실행 경로·토큰·시간을 기록한다. 현재 합성 fixture 전용 설정은 전환 또는 새 워크플로가 필요하다.
- **NeMo Retriever OCR Microservice:** 사진 접수에서 실제 문자 추출을 수행한다. 호스팅 API 경로와 자기 호스팅 `/v1/ocr` endpoint 설정을 지원한다. 현재 실제 검증은 호스팅 경로다.
- **NeMo Evaluator Microservice:** 반복 평가의 확장 후보다. 이번 최소 구현의 필수 의존성으로 추가하지 않는다. `nat eval`만 실행해서 Evaluator Microservice를 썼다고 주장하지 않는다.
- **Skill/Skill Spector:** 프로젝트별 조사·수정 규칙을 담고 변경 시 스캔한다. 이 규칙은 모델 권한을 부여하는 정책 엔진이 아니다.
- **OpenShell:** 대상 코드 실행의 격리 후보. [파일·네트워크 정책](https://docs.nvidia.com/openshell/reference/policy-schema)을 실제 적용·검증하면 사용 기술로 보고한다. 제품의 논리적 안전 요구는 특정 런타임 없이도 유지한다.

## 제출·시연 증거 규칙

실행 명령, 기준 코드 커밋, 사건 입력과 예상/실제 관측, 모델·도구 호출 기록, Microservice job ID/결과, 재현·회귀 결과, 제한 사항을 재현 가능하게 남긴다. 허가받지 않은 실제 코드·로그·개인정보는 공개 저장소나 모델 요청에 넣지 않는다. 합성 자료·시드한 결함·실제 사건을 명확히 구분한다. 모델이 만든 해결책을 실제 서비스에 적용하지 않았다면 `제안`으로만 표시한다. [현재 상태](current-state.md)의 미구현 항목을 발표 자료에서 완료로 바꾸지 않는다.

대회 결과와 무관하게 제품 가치의 판단 기준은 [제품 계약](product.md)과 [평가](evaluation-operations.md)를 따른다. 다른 트랙·기술 조건이 확인되면 이 문서만 바꾸고 제품의 사용자 문제 정의를 자동으로 바꾸지 않는다.
