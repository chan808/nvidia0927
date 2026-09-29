# TraceBridge 문서 안내

현재 기능은 [구현 상태](current-state.md), 최신 공개 접수·자동 처리·회귀는 [기본 서비스 검증](validation/basic-service.md), 이전 적용 보고·회복 검사는 [서비스 확인 검증](validation/project-recovery.md), 개발 HTTP 연결은 [daily 요청 관측 검증](validation/daily-observation.md)에서 확인하세요.

## 사용법

| 문서 | 내용 |
| --- | --- |
| [시작하기](guides/getting-started.md) | 지원 환경, 설치, 설정, 첫 화면 |
| [공개 제보와 자동 처리](guides/basic-service.md) | 공개 화면·접수 토큰·후속 답변, 자동 평가·후보 준비·비운영 적용·회복·지식 승인 |
| [공개 운영 점검표](guides/basic-service-rollout.md) | 대회 시연, 인터넷 공개 전 검증 조건, 권한 철회·실패·중단 대응, 후속 기능 순서 |
| [사용법](guides/usage.md) | 제보·사진·후속 답변·프로젝트 연결·기억·수정안·기존 데모 |
| [실제 프로젝트 운영](guides/real-projects.md) | 실제 다중 경로·서비스 등록, 일반 수정·검사·검토 적용, PC 페어링·배포 웹 |
| [daily 실제 요청 연결](guides/daily-observation.md) | 요청 ID·시각·JSONL·빌드·서비스 응답 연결과 실제 HTTP 재현 |
| [적용 후 서비스 확인](guides/project-recovery.md) | PC 적용 결과 보고·신고 API/회귀·버전/관측 기간·회복 확인/사건 재개 |
| [실사용 고도화·RAG](guides/operational-hardening.md) | 우선순위·조사 예산·작업 이력, 선택적 NIM 임베딩, 검색 품질 평가와 후속 운영 조건 |
| [중앙 PostgreSQL·RAG](guides/postgres-rag.md) | 설치·이전·역방향 복구·백그라운드 색인·캐시·예산·중앙 평가 |
| [검증 절차](guides/verification.md) | 오프라인 재생, 회귀, 실자료 검증 준비 |
| [Agolive 수동 조사](guides/agolive.md) | 기존 수동/GPT 페이지의 연결과 제한 |
| [클라우드 웹과 로컬 실행기](guides/cloud-and-local.md) | 다중 경로 등록, 소유자 전용 원격 연결의 구현 범위와 후속 운영 조건 |
| [배포 가이드](guides/deployment.md) | CPU 서버·Compose·HTTPS·영속 저장·백업, 비공개 웹과 로컬 연결의 완료 기준 |
| [신규 AWS 계정](guides/new-aws.md) | 현재 배포 방향: 무료 자격·계정 분리, EC2 템플릿, 수동 Linux 이미지 검증 |
| [무료 배포](guides/free-deployment.md) | AWS Free plan EC2, Lightsail 90일 체험, Oracle Always Free와 2 GB 설정 |
| [기존 서버 재사용](guides/existing-server.md) | Agolive EC2·Nginx·SSM 확인 결과와 별도 Compose 공유 배포 절차 |

## 설계

이 문서들은 목표와 계약을 설명합니다. 구현 완료 여부는 [현재 상태](current-state.md)와 대조하세요.

| 문서 | 내용 |
| --- | --- |
| [제품](design/product.md) | 사용자, 가치, 제품 범위 |
| [기본 서비스 구현](design/basic-service.md) | 공개 접수·현재 관측·작업 판단·자동 처리·검증된 지식의 계약과 후속 확장 |
| [사건 흐름](design/workflows.md) | 상태, 분류, 담당 역할, 보고 |
| [시스템 구조](design/architecture.md) | 구성 요소와 도구·실행 계약 |
| [PostgreSQL·RAG 통합 개선안](design/postgres-rag-evolution.md) | 중앙 DB/PC 저널, 지식·검색·버전·예산, 단계별 이전/복구, 실제 사건 평가와 지속 개선 |
| [클라우드·로컬 운영 개선안](design/cloud-local-operations.md) | 현재 코드의 간격, 로컬 조사 배치, API·작업 복구·다중 서비스·일반 수정·출시 조건 |
| [자료와 기억](design/data-model.md) | 사건·근거·실행·검토·검색 |
| [권한과 정책](design/policy-security.md) | 자료 접근, 작업 허용, 자동화 수준 |
| [평가와 운영](design/evaluation-operations.md) | 정확성·비용·운영 기준 |
| [로드맵](design/roadmap.md) | 기능 의존성과 후속 확장 |

## 검증 기록

- [기본 서비스 검증](validation/basic-service.md): 공개 브라우저 접수·질문·후속 답변·후보 준비, 실제 파일 적용/HTTP 회복·지식 승인·PostgreSQL·내부 정보 경계

- [게시 전 문서·파일 점검](validation/publication-review.md): 미사용 설정·추적된 로컬 산출물 정리, 문서 링크·credential·검증 소스 해시·wheel 포함 파일 확인
- [서비스 확인 검증](validation/project-recovery.md): 공개 프로젝트의 실제 원본 적용·API 관측·중앙 SQLite/PostgreSQL 보고·회복 실패 재개·AppTest
- [PostgreSQL·RAG 재검토](validation/postgres-rag-review.md): 전체 632개 회귀, 실제 PostgreSQL 30개, 검색 정정 경합·워커 복구/취소·프로젝트 참조·입력·이전·업그레이드·이력 정렬 수정
- [PostgreSQL·RAG 기반](validation/postgres-rag.md): 전체 595개 회귀, 실제 PostgreSQL/pgvector 15개, 이전·원자성·색인·예산/캐시
- [작업 운영·RAG 고도화](validation/operational-hardening.md): 최종 579개 회귀, 선택적 벡터 검색·범위/무효화·작업 이력·AppTest와 실모델 미검증 범위
- [로컬 GUI 최종 검증](validation/local-gui-final.md): 실제 브라우저 등록·제보·실모델 후보·원본 적용·재열기·실행기 재연결과 551개 회귀
- [최종 로컬 통합](validation/main-integration.md): A~D 통합, 전체 회귀와 재현 범위
- [실제 프로젝트 운영 연결](validation/real-project-operations.md): daily 사본의 229개 프론트 검사, 실제 NVIDIA 게이트웨이와 원격 작업 계약
- [검증 메타데이터](validation/release-verification.json): 당시 실행·해시·미완료 항목
- [NVIDIA 실제 호출 기록](validation/nvidia-validation.md): 과거 OCR·모델 호출과 한계
- [SkillSpector 검사](validation/skill-scan-report.md): 정적 검사 결과
- [배포 기반 확인](validation/deployment-foundation.md): 설정 확인 43개와 기존 AWS 서버 조회, TraceBridge 기동·HTTPS·로컬 연결은 미확인
- [신규 AWS 기반 확인](validation/new-aws-foundation.md): 계정 확인 경로 27개·배포 설정 44개·로컬 씨드 저장, AWS 생성과 Linux CI는 미수행
- [실제 Linux 이미지 확인](validation/linux-deployment.md): 이미지 빌드·1 GB 실행·초기 화면·데이터 유지·Caddy 설정 및 전송용 파일

검증 기록의 날짜와 수행 범위를 함께 보세요. 파일 이동 후에도 당시 해시·원점·성공/실패 값은 보존합니다. 로컬 `output/` 산출물은 Git 저장소에 포함되지 않습니다.

## 대회 자료

- [조건과 NVIDIA 역할](competition/requirements.md)
- [제출 확인 사항](competition/submission-readiness.md)
- [신청 문안 초안](competition/submission-draft.md)

사용자가 별도로 제출을 완료했습니다. 문안 초안과 과거 준비 기록을 실제 업로드본과 동일한 파일로 간주하지 않습니다.

## 과거 기록

[보관 문서 목록](archive/README.md)에 초기 설계, 단계별 구현 결과, 사전 점검, A~D 세션 인계를 모았습니다. 과거 프롬프트의 남은 작업이나 검사 개수를 현재 상태로 사용하지 마세요.

문서를 갱신할 때는 현재 기능·제약은 `current-state.md`, 실행 결과는 `validation/`, 명령과 설정은 `guides/`, 목표와 계약은 `design/`에 기록합니다. 코드·실행 증거와 문서가 다르면 현재 사실부터 바로잡습니다.
