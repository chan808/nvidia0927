# daily 실제 요청 관측 검증

2026-09-29. TraceBridge의 첫 실제 자료 연결을 구현하고 검증했다. daily 개발 서버의 필수 입력 누락 HTTP 400을 사용했다. 사용자 운영 장애나 코드 결함을 만들어낸 사례로 취급하지 않는다.

게시 전 점검: daily 계측은 별도 로컬 작업 트리에 있으며 소스 13개가 검증 당시 해시와 일치한다. 해당 저장소에는 이 작업 이전의 미푸시 커밋 39개가 있어 이번 TraceBridge 게시에 함께 푸시하지 않았다. daily의 원격 main에 이 계측이 포함됐다고 간주하지 않는다. TraceBridge 연결 도구는 daily 소스를 수정하거나 계측을 자동 설치하지 않는다.

## 확인한 흐름

`프론트 API의 요청 ID/시각 → Next 프록시 → daily 응답/JSONL/빌드 → TraceBridge 웹 API 접수 → 기존 페어링 PC 조사 → 저장된 작업 재조회`

- 실제 HTTP 400·`VALIDATION_FAILED`와 같은 요청 ID의 지속 JSONL, 입력 필드 관측, 빌드 SHA를 확인했다.
- 프론트 프록시 요청과 PC의 조사 모두 `EXACT_ID`이며 API 작업은 `SUCCEEDED`였다. 원인/사용자 책임은 미확정인 `INVESTIGATE`를 유지했고 외부 모델은 0회였다.
- 발생 시각 없는 제보는 추가 질문으로 보류한다. 실제 요청 시각을 포함한 동일 ID 제보에서만 대조한다. 로그의 날짜로 제보의 시각을 대신 만들지 않는다.
- backend `UP`, frontend `REACHABLE`을 PC의 온라인·자료 연결 상태와 별도로 중앙 manifest에서 조회했다.
- 기존 control DB의 완료 작업 2개는 입력·본문·결과·해시·상태가 바이트/값 수준에서 일치하고 integrity/FK 검사도 통과했다. 등록 정책의 원본 적용은 계속 꺼져 있다.

기계 판독 결과와 소스 해시는 [기록 JSON](daily-observation.json)에 있다. HTTP/작업 원 근거는 비공개 `output/daily-observation/verification`, `proxy-verification`, `remote-verification.json`에 보관했다.

## 검사 결과

| 검사 | 결과 |
| --- | --- |
| TraceBridge 전체 회귀 | **650개 통과, 175.86초** |
| 실제 PostgreSQL | 위 전체 검사에 포함된 30개, 각 고유 DB로 실행 |
| daily 프론트 | **233개 통과**, 타입 검사·production build 통과 |
| daily backend support | **7개 통과**, private 값 미기록·미처리 오류의 성공 오표시 방지·mixed local/Google 비활성 포함 |
| daily 레이어·Modulith | **6개 통과** |
| 실행 JAR·문서 | JAR 빌드, 46개 문서/273개 링크/28개 ADR 검사 통과 |

윈도우 sandbox의 pytest 임시 폴더 권한 제한 때문에 검사는 승인된 로컬 실행에서 작업 전용 임시 폴더를 사용했다. Docker 조회가 응답하지 않아 기존 별도 PostgreSQL 18.6 검증 클러스터와 새로운 빈 daily DB를 사용했다. 기존 daily 개발 DB·Redis·Docker 설정을 바꾸지 않았다. 검증용 daily 프로세스는 Redis 세션 자동 설정을 제외했다.

## 구현 범위와 한계

- 요청 메타데이터와 오류 코드의 개발 전용 기록이다. 본문 값·쿼리·쿠키·인증 헤더·응답 본문을 파일이나 실패 메타데이터에 저장하지 않는다.
- `local & !google` 조건과 명시적 설정을 함께 확인한다. local+Google의 혼합 프로필에서도 파일 관측·빌드 응답 헤더를 차단한다.
- 실행 SHA는 빌드 정보다. 미커밋 코드임을 기록했고 현재 HEAD와 동일하다는 이유로 배포/원인/수정을 확정하지 않는다.
- 개발 로그인 한 endpoint의 소스 해시 대응 OpenAPI/DTO와 caller 파일 해시를 등록했다. 다른 endpoint, 실제 브라우저 실행 버전·입력의 책임 대응은 추가 작업이다.
- 요청 기록은 활성 1 MB 파일에 한정해 등록했다. 회전된 과거 파일을 포함하려면 별도 경로를 등록한다.
- full daily Docker/Testcontainers 회귀와 실제 브라우저 조작은 이번 검증에서 수행하지 않았다. 새 화면의 Streamlit AppTest는 전체 회귀에 포함된다.
- 중앙 PostgreSQL 운영 전환, 실제 사용자 장애 수정·적용 후 사용자 여정 확인, 공개 인증과 자동 배포는 수행하지 않았다.

사용과 재현 명령은 [연결 가이드](../guides/daily-observation.md)에 있다.

## 검증 후 상태

이번에 생성한 daily DB의 계정·Task가 모두 0개임을 확인한 뒤 해당 DB와 임시 backend/frontend를 정리하고 검증 PostgreSQL을 정상 종료했다. 기존 수정 중이던 `frontend/next-env.d.ts`는 보관한 원문과 바이트 단위로 일치하게 복원했다. TraceBridge 웹·API·기존 페어링 PC는 최신 코드로 실행 중이며 기존 SQLite 원본을 유지한다.

daily 개발 서비스는 검증 전처럼 종료된 상태다. PC는 온라인이지만 마지막 서비스 응답은 backend/frontend `UNREACHABLE`로 표시된다. 기존 개발 기동 절차로 daily를 시작하면 등록된 기본 로그 경로와 현재 서비스 응답을 다시 확인한다. 실제 사용자 사건과 증상별 수정 재현 검사는 그다음에 연결한다.
