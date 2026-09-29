# 중앙 PostgreSQL과 검토 기반 RAG 운영

2026-09-29. 중앙 서버에 **PostgreSQL + pgvector**를 선택할 수 있다. PC 실행기의 SQLite 저널은 오프라인 결과 보관·재전송에 계속 사용한다. 기존 SQLite 서버도 호환한다. [최신 재검토](../validation/postgres-rag-review.md)와 [초기 구현 검증](../validation/postgres-rag.md)을 함께 확인한다.

```mermaid
flowchart LR
  Report[제보와 소유자 평가] --> Queue[중앙 작업 큐]
  Queue --> Runner[PC 조사와 사본 검사]
  Runner --> Commit[사건 · 결과 · 이력의 단일 커밋]
  Commit --> PG[(PostgreSQL)]
  PG --> Review[소유자 검토]
  Review --> Worker[백그라운드 색인]
  Worker --> Vector[(pgvector)]
  PG --> Search[지문 · 키워드 · 벡터 · RRF]
  Vector --> Search
  Search --> Recheck[현재 요청 · 로그 · 버전으로 재확인]
  Recheck --> Runner
  Runner --> Journal[(PC SQLite 저널)]
```

## 설치와 연결

저장소 루트의 기존 Python 3.12 환경에서 선택 의존성을 설치한다.

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[service,postgres,test]'
```

PostgreSQL 18과 pgvector 0.8.6에서 검증했다. 연결 역할은 TraceBridge 전용 DB의 `tracebridge` 스키마·마이그레이션을 관리할 수 있어야 한다. 최초에는 관리자가 `vector`, `pg_trgm` 확장을 설치할 권한도 필요하다. [pgvector 설치](https://github.com/pgvector/pgvector#installation)를 참고한다.

비공개 파일에 `postgresql://USER:URL_ENCODED_PASSWORD@HOST:PORT/tracebridge`를 저장하고 **파일 경로만** 설정한다. 실제 URL은 명령 인수·Git·공개 로그에 넣지 않는다.

```powershell
$env:TRACEBRIDGE_DATABASE_URL_FILE = 'C:/private/tracebridge-database-url'
.\.venv\Scripts\python.exe -m scripts.storage_admin init
.\.venv\Scripts\python.exe -m scripts.serve_control --init-token-file output/control-plane/operator-token
$env:TRACEBRIDGE_OPERATOR_TOKEN_FILE = 'output/control-plane/operator-token'
.\.venv\Scripts\python.exe -m scripts.serve_control --host 127.0.0.1 --port 8765
```

토큰 생성은 파일이 없을 때 한 번 실행한다. 기존 파일은 덮어쓰지 않는다. 연결 우선순위는 `TRACEBRIDGE_DATABASE_URL` → `TRACEBRIDGE_DATABASE_URL_FILE` → `TRACEBRIDGE_CONTROL_DB` → 기존 SQLite 기본값이다. `/health`는 실제 DB 조회와 선택된 저장소를 반환한다.

지원하지 않는 DB URL·빈 연결 파일·디렉터리·메모리 DB는 시작 시 거부한다. PostgreSQL의 `20260929_03` 마이그레이션은 카드·벡터·수정 작업·적용 기록·최신 실행의 프로젝트/사건 참조를 검증한다. 기존 연결이 잘못돼 있으면 업그레이드 전체가 롤백되고 원 데이터와 기존 마이그레이션 버전이 남는다. 백업에서 잘못된 참조의 원인을 확인하고 원본 기록에 근거해 해결한 뒤 다시 실행한다.

웹의 `TRACEBRIDGE_CONTROL_URL`과 소유자 토큰을 같은 서버에 연결한 뒤 [PC 페어링·프로젝트 등록](real-projects.md)을 진행한다. 조사 대상 서비스의 DB를 TraceBridge 저장소로 지정하지 않는다.

## 데이터 이전과 되돌리기

이전 대상은 중앙 서버의 `state.sqlite3`, `server-incidents.sqlite3`다. PC 저널과 로컬 사건 DB는 그대로 둔다. 두 소스는 읽기 전용으로 열고 SQLite backup API로 WAL을 포함한 사본을 읽는다.

```powershell
.\.venv\Scripts\python.exe -m scripts.storage_admin inventory --control output/control-plane/state.sqlite3 --incidents output/control-plane/server-incidents.sqlite3
```

작업을 완료하거나 안전하게 종료하고 서버·실행기·색인 작업의 쓰기를 중지한다. `RUNNING`, `CANCEL_REQUESTED`, `RECOVERY_REQUIRED` 작업이나 실행 중인 색인이 남으면 도구가 거부한다. 임의로 상태를 바꿔 통과시키지 않는다. 두 SQLite 파일에 공동 스냅샷이 없으므로 쓰기 중지가 필요하다.

연결을 **별도 빈 PostgreSQL DB**로 설정한 뒤 실행한다.

```powershell
.\.venv\Scripts\python.exe -m scripts.storage_admin import-sqlite --control output/control-plane/state.sqlite3 --incidents output/control-plane/server-incidents.sqlite3 --source-frozen
.\.venv\Scripts\python.exe -m scripts.storage_admin verify-sqlite --control output/control-plane/state.sqlite3 --incidents output/control-plane/server-incidents.sqlite3
```

ID·revision·원 JSON 문자열·원 입력 해시·검토 이력·모델 상태·이벤트 ID·credential digest를 보존한다. 저장에서 빠진 입력으로 원 해시를 재계산하지 않는다. 불명확한 모델 단계도 그대로 유지하고 재호출하지 않는다. 알 수 없는 테이블/컬럼과 비어 있지 않은 대상은 거부하며 삽입·비교 실패는 전체 이전을 롤백한다.

이력이 없는 구버전 작업마다 `MIGRATED_SNAPSHOT`에 `history_before_migration=NOT_RECORDED`를 남긴다. 일부 작업만 이력이 없는 DB도 지원한다. 별도 `verify-sqlite`는 원 이벤트의 모든 필드를 비교하며, 해당 작업의 상태·epoch와 일치하는 명시적인 이전 시점 기록 하나만 추가로 허용한다. 그 수는 `legacy_history_snapshots`로 반환한다. 과거 이벤트를 복원한 것처럼 기록하지 않는다. 네이티브 벡터는 원 `vector_json`에서 만드는 파생 자료다.

비교가 일치하면 운영 서버의 연결을 PostgreSQL로 바꿔 **한 저장소만 쓰게** 시작한다. 새 쓰기 이후 되돌리기는 옛 SQLite 백업만으로 하지 않는다. 쓰기를 다시 중지하고 현재 PostgreSQL을 새 경로로 내보낸다.

```powershell
.\.venv\Scripts\python.exe -m scripts.storage_admin export-sqlite --directory output/private-rollback/2026-09-29 --source-frozen
```

두 SQLite 파일과 비교 결과를 생성한다. 원 JSON·해시·모델 상태·쿼리 캐시·색인 작업·검토를 포함한 논리 행을 비교한다. 기존 경로는 덮어쓰지 않는다. 사본으로 서버를 재시작하고 PC 재연결·결과 재전송을 확인한다. PostgreSQL 물리 백업/WAL/PITR 정책은 별도로 운영한다. 백업에는 비공개 결과와 credential digest가 포함되므로 공개하거나 Git에 추가하지 않는다.

## RAG 실행

흐름은 **결과 저장 → 소유자 승인/정정 → 색인 요청 → 범위 검색 → 현재 근거 재확인**이다. 미검토·반려 카드는 검색 대상에서 제외한다. 검토 승인과 원인 확인·후보 검사·원본 적용·서비스 회복은 별도 상태다.

실제 제공하는 NIM `/v1` 주소·모델로 임베딩을 별도 활성화한다.

```dotenv
TRACEBRIDGE_EMBEDDINGS_ENABLED=1
TRACEBRIDGE_EMBEDDING_URL=https://your-nim-service.example/v1
TRACEBRIDGE_EMBEDDING_MODEL=your-served-embedding-model
TRACEBRIDGE_EMBEDDING_API_KEY=your-private-key
```

같은 설정을 가진 환경에서 색인 실행기를 HTTP 서버와 별도 프로세스로 시작한다.

```powershell
.\.venv\Scripts\python.exe -m scripts.memory_worker
# 한 번만 처리하려면:
.\.venv\Scripts\python.exe -m scripts.memory_worker --once
```

웹의 **유사 사건 검색 설정 → 승인된 사건의 검색 인덱스 갱신**은 PostgreSQL에서 `202`와 작업 ID를 반환한다. 화면에서 진행 상태를 조회한다. `QUEUED → RUNNING → SUCCEEDED/FAILED`를 저장한다. 워커가 꺼져 있어도 상태 조회·다음 요청에서 만료된 임대를 `RECOVERY_REQUIRED`로 표시한다. 소유자가 마지막 요청 결과를 확인한 뒤 재시도하거나 **인덱스 갱신 중단**으로 `CANCELLED` 처리할 수 있다. 실패·중단 복구 API는 `/v1/memory/{project_id}/index-jobs/{index_job_id}/retry`, 취소는 같은 주소의 `/cancel`이다. 불명확한 외부 호출은 자동 반복하지 않는다. 취소·만료된 작업 토큰은 네이티브 벡터나 완료 상태를 저장하지 못한다. 이미 저장한 벡터와 완료한 외부 호출은 취소로 되돌리지 않는다.

별도 색인 워커는 PostgreSQL 전용이다. SQLite는 기존 `scripts.incident_memory index`의 직접 색인을 사용한다.

색인 중 정정·반려된 결과는 버린다. 검색 후보를 뽑을 때 검토 revision을 함께 읽고, 반환 직전 승인 상태·서비스/환경·같은 revision을 확인한다. 외부 임베딩 호출 중 정정·반려된 키워드 후보도 제외한다. 검토 변경 시 기존 벡터·키워드 색인을 제거하고 현재 상태로 다시 검색한다. 모델 identity·차원을 구분하고 같은 identity의 차원 변경을 거부한다. 벡터를 임의로 자르지 않는다.

정확한 오류 지문·경로를 우선하고 한국어 접두어 FTS, pgvector 코사인, RRF를 결합한다. SQL 범위를 먼저 적용한 네이티브 정확 검색이며 SQLite의 최대 500개 스캔 제한을 사용하지 않는다. HNSW/IVFFlat·리랭커·형태소 분석은 후속이다. 모델 차원·대표 필터·지연·Recall을 측정한 뒤 확장한다.

PostgreSQL 검색의 `candidate_count`는 반환된 상위 벡터 후보 수다. 정확 검색이 실제로 평가한 전체 행 수나 작업량을 뜻하지 않는다. 규모에 따른 지연은 실제 데이터와 실행 계획으로 측정한다.

조사당 검색은 최대 20회, **새 쿼리 임베딩은 최대 2회**다. 동일 쿼리/모델의 벡터는 재사용하지만 검색 카드는 캐시하지 않아 승인 상태·범위를 다시 확인한다. 정확 일치 두 건이면 벡터 호출을 생략한다. 실패·예산 소진 시 키워드 결과를 유지하고 실패 이유를 기록한다. 불명확한 같은 쿼리는 재전송하지 않는다.

전략·적중 수·카드와 검토 revision·범위·지연·캐시/호출 수는 작업 이력에 남긴다. 쿼리 본문 대신 해시를 저장한다. 채팅 모델 예산은 조사 최대 4회·후보 최대 1회다. 과거 카드가 적중해도 현재 원인·사용자 책임을 확정하지 않는다.

채팅 요청의 도구·메시지·호출 제한·tool choice는 외부 호출 예약 전에 검증한다. 잘못된 요청은 `422`이며 호출 예산을 소비하지 않는다. 결과의 사건 revision도 할당된 이전 실행의 다음 revision과 같아야 한다.

## 중앙 조회와 평가

서버 관리 권한이 있는 환경에서 직접 조회한다.

```powershell
.\.venv\Scripts\python.exe -m scripts.incident_memory --central --project my-app list
.\.venv\Scripts\python.exe -m scripts.incident_memory --central --project my-app search --query '계정을 만들 수 없어요' --service api --environment dev --semantic
.\.venv\Scripts\python.exe -m scripts.evaluate_memory_retrieval --central --suite output/labeled-cases.json --output output/retrieval-postgres.json
```

하이브리드 평가에는 마지막 명령에 `--semantic`을 추가한다. 같은 사건·라벨·범위로 Recall@2, MRR@2, 관련 사례가 없는 제보의 오적중·지연·실패를 비교한다. [라벨 형식과 평가](operational-hardening.md)를 참고한다. 검색 지표는 원인/수정 성공률이 아니다. 실제 한국어 NIM 품질과 비용은 허가된 사례로 측정한다.

## 배포와 다음 단계

`deploy/postgres.compose.yaml`을 기존 `compose.yaml`, `control-plane.compose.yaml`에 합친다. 비공개 URL 파일과 비밀번호 파일 경로를 지정한다. PostgreSQL 포트는 호스트에 공개하지 않는다. `--profile rag`는 색인 실행기를 추가한다. 오프라인 Compose 검사는 통과했으며 이번 이미지의 Linux 실행·백업 복원·HTTPS 공개는 별도 출시 검사다. [기존 배포 절차](deployment.md)를 따른다.

현재는 소유자 전용이다. 지속 개선은 **기록 → 검토 → 재색인 → 재확인 → 실제 라벨 비교 → 회귀 검사 추가** 순서다. 적용 후 API/사용자 여정 검증, 관측 기반 자동 중요도·난이도 제안, 팀 인증/RLS, 런북·문서 RAG와 자동 배포는 [후속 설계](../design/postgres-rag-evolution.md#14-실행할-작업-목록과-우선순위)에 남긴다. 모델 가중치를 자동 학습하는 기능은 포함하지 않는다.
