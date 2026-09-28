<!-- DRAFT: synthetic evaluation history; exported through C public API; no real resolution -->

# tracebridge\-evaluation 사건 매뉴얼

검토된 과거 기록의 확인 순서와 조치 단서다. 현재 자료로 재검증해야 하며 편집·실행·배포 권한을 부여하지 않는다.
검토 승인, 원인 확인, 후보 사본 검증, 원본 적용, 서비스 회복은 각각 별도 상태다.

## room\_enter · HTTP 500 · RoomFullException

- 카드: fixture\-history\-run\-rooms; 사건: fixture\-history\-rooms; 출처 실행: fixture\-history\-run\-rooms; revision: 1
- 출처 시각: 2026\-09\-19T12:00:00\+00:00; 실행 상태: COMPLETED; 자료 유형: NOT\_RECORDED
- 검토: APPROVED; 검토자: SYNTHETIC\_EVALUATION\_CURATOR; 시각: 2026\-09\-28T13:48:40\.388414\+00:00; 검토 revision: 1
- 기록된 판정·가설: 합성 과거 RoomFullException 단서; 원인과 수정 미검증 (출처 실행 fixture\-history\-run\-rooms)

### 적용 조건과 버전

- project_id: tracebridge\-evaluation (출처 실행 fixture\-history\-run\-rooms)
- service: evaluation\-api (출처 실행 fixture\-history\-run\-rooms)
- environment: dev (출처 실행 fixture\-history\-run\-rooms)
- operation: room\_enter (출처 실행 fixture\-history\-run\-rooms)
- method: POST (출처 실행 fixture\-history\-run\-rooms)
- path: /rooms/enter (출처 실행 fixture\-history\-run\-rooms)
- runtime_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa (출처 실행 fixture\-history\-run\-rooms)
- local_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa (출처 실행 fixture\-history\-run\-rooms)
- 버전 출처 local_head: synthetic\_snapshot; SHA: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; 상태: NOT\_RECORDED; 출처 실행: fixture\-history\-run\-rooms
- 버전 출처 configured: 미관측; SHA: 미관측; 상태: NOT\_RECORDED; 출처 실행: fixture\-history\-run\-rooms
- 버전 출처 runtime: synthetic\_snapshot; SHA: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; 상태: OBSERVED; 출처 실행: fixture\-history\-run\-rooms
- 관측 특징: HTTP 500; \{"error\_codes":\[\],"exceptions":\["RoomFullException"\],"paths":\["/rooms/enter"\],"stack\_fingerprints":\[\]\}; 출처 실행: fixture\-history\-run\-rooms

### 확인 순서

1. 현재 로그 예외와 실행 버전을 먼저 대조한다 (출처 실행 fixture\-history\-run\-rooms; RECORDED\_PLAN; historical:fixture\-history\-run\-rooms:L1)
2. 화면·동작·대략적인 시각을 보완해 주세요\. (출처 실행 fixture\-history\-run\-rooms; RECORDED\_PLAN; historical:fixture\-history\-run\-rooms:L1)

### 반증·보류 및 부적합 조건

- 현재 응답 상태·오류 코드·예외·스택 지문이 과거 관측과 다르다\. (출처 실행 fixture\-history\-run\-rooms; MEMORY\_RECHECK\_POLICY)
- 현재 프로젝트·서비스·환경·동작·메서드·경로가 적용 조건과 다르거나 미관측이다\. (출처 실행 fixture\-history\-run\-rooms; MEMORY\_RECHECK\_POLICY)
- 과거 또는 현재 실행 버전이 미관측이거나 서로 다르다\. (출처 실행 fixture\-history\-run\-rooms; MEMORY\_RECHECK\_POLICY)
- 현재 관측이 충돌하거나 조회 범위를 끝까지 확인하지 못했다\. (출처 실행 fixture\-history\-run\-rooms; MEMORY\_RECHECK\_POLICY)

### 조치와 검증 결과

- 기록된 다음 조치: 화면·동작·대략적인 시각을 보완해 주세요\. (출처 실행 fixture\-history\-run\-rooms); 담당자 검토와 현재 근거 확인이 필요하다.
- 승인·정정: APPROVED (검토 revision 1; 출처 실행: fixture\-history\-run\-rooms)
- 원인 확인: NOT\_CONFIRMED (출처 실행: fixture\-history\-run\-rooms)
- 후보 사본 검증: NOT\_VERIFIED (출처 실행: fixture\-history\-run\-rooms)
- 원본 적용: NOT\_APPLIED (출처 실행: fixture\-history\-run\-rooms)
- 회복 확인: NOT\_VERIFIED (출처 실행: fixture\-history\-run\-rooms)

### 근거 출처·마지막 확인·한계

- historical:fixture\-history\-run\-rooms:L1: log; 출처: synthetic\_history; 해시: f2695ebe550a772f0e5c174e01c4dbe8fdaf3d0c6641acfa25b3520fa021981e
- 마지막 자료 확인: 2026\-09\-19T12:00:00\+00:00; 출처 실행: fixture\-history\-run\-rooms; 실행 상태: COMPLETED; 조회 완전성: True; 충돌: False; 실행 버전: OBSERVED
- 한계: 과거 카드는 현재 근거와 실행 권한을 대신하지 않는다\. (출처 실행 fixture\-history\-run\-rooms)
- 한계: 검토 승인은 원인·수정·회복 확인이 아니다\. (출처 실행 fixture\-history\-run\-rooms)
