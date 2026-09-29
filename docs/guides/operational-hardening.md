# 실사용 고도화: 작업 큐와 사건 RAG

2026-09-29. 소유자가 관리하는 프로젝트의 제보·조사·검토 후 수정 흐름을 먼저 개선했다. 공개 다중 사용자 운영과 실서비스 자동 배포는 별도 범위다.

## 지금 사용할 기능

- 연결된 프로젝트 화면에서 P0~P3 중요도, 작업 난이도와 변경 위험을 입력한다. PC 실행기는 중요도 순서로 대기 작업을 선택하고 동률이면 접수 순서를 따른다. 실행 중 작업을 강제 중단하지 않는다.
- 난이도가 작으면 조사 시간 45초, 보통/미정은 90초, 크면 180초다. 모델·도구 호출의 기존 상한과 소유자의 수정·검사 정책은 유지한다. 난이도와 위험은 소유자의 평가이며 자동 추정값이 아니다.
- 작업 목록에서 저장된 결과를 다시 열고 평가 변경, 접수·실행·취소·만료·복구·완료와 모델 단계 이력을 확인한다. 실행 성공과 원인 확인·수정 후보 검증·서비스 회복은 각각 표시한다.
- 기존 DB는 결과·입력 해시를 보존하면서 새 열·색인·이력 표를 추가한다. 이전 작업에는 마이그레이션 시점의 상태만 기록하고 과거 이력을 만들어내지 않는다.
- 취소 확인을 기다리는 PC에 다른 작업을 동시에 할당하지 않는다. 복구를 승인한 작업에는 새로운 대기 유효 기간을 부여한다.

실행기 연결이 끊겨 취소 확인이나 실행 결과를 받지 못한 경우 `RECOVERY_REQUIRED`로 남긴다. 해당 PC의 다음 작업은 대기한다. 확보된 결과 복구를 요청하거나 PC 작업 종료를 확인한 뒤 대기열에서 제외한다. 임대 만료를 작업 종료로 간주하지 않는다.

코드를 갱신한 뒤 웹·API·PC 실행기를 다시 실행해야 새 API와 조사 예산이 적용된다. 실행 명령은 [실제 프로젝트 운영](real-projects.md)에 있다. 실제 daily의 지속 로그·실행 SHA 연결과 해당 증상의 재현 검사 등록은 여전히 필요하다.

## RAG와 Vector DB 선택

현재도 검토된 사건 검색 결과를 조사 모델에 제공하므로 기본 RAG가 있다. 이번에는 다음 경로를 추가했다.

`현재 프로젝트·서비스·환경 → 오류 지문/경로 검색 → 키워드 검색 + 선택적 벡터 검색 → 순위 결합 → 최대 2개 과거 카드 → 현재 자료로 재확인`

오류 지문과 경로의 정확 일치를 우선한다. 한국어 키워드는 접두어를 허용해 `가입`으로 `가입이`·`가입을`도 찾는다. 벡터 검색은 표현이 다른 과거 제보를 후보로 찾는 데 쓰며, 같은 원인이라는 근거가 되지 않는다.

후속 구현으로 [중앙 PostgreSQL + pgvector·비동기 색인·검색 캐시/예산·이전/복구](postgres-rag.md)를 추가했다. 아래 SQLite 설명은 로컬 호환 경로다. 독립 Vector DB 서버는 추가하지 않았다. SQLite는 모델별 벡터를 저장하고 범위 안에서 최대 500개를 정확한 코사인 계산으로 조회한다. 초과 여부와 오래된 벡터·차원 불일치를 검색 결과에 기록한다. 대규모 ANN 검색 구현이 아니며 성능 우위를 주장하지 않는다. 운영 저장소를 PostgreSQL로 옮길 때는 [pgvector](https://github.com/pgvector/pgvector)의 결합 저장·벡터 검색을 우선 검토할 수 있다. 검색을 별도 서비스로 운영할 필요가 생기면 [Qdrant의 하이브리드 검색](https://qdrant.tech/documentation/search/hybrid-queries/)도 선택지다. 검색 지연·색인 범위·운영 부담을 측정한 뒤 정한다.

## 선택적 NIM 임베딩 연결

임베딩은 기본적으로 꺼져 있다. 기존 API 키가 있다는 이유로 자동 전송하지 않는다. 관리자가 URL·모델을 명시하고 활성화한 뒤, 검토된 요약의 색인 전송을 선택한다. 실제 모델의 한국어 성능·서빙 성공·비용은 아직 검증하지 않았다.

[NVIDIA NIM 문서](https://docs.nvidia.com/nim/nemo-retriever/text-embedding/1.12.0/reference.html)에 따라 색인은 `input_type=passage`, 검색은 `input_type=query`를 사용한다. 키워드 검색과 벡터 순위는 reciprocal rank fusion으로 결합한다. 재순위화 모델 호출은 아직 연결하지 않았다.

서버/API 터미널에 설정하고 다시 실행한다. URL은 `/v1`까지이며 모델은 해당 endpoint가 제공하는 이름을 쓴다.

```powershell
$env:TRACEBRIDGE_EMBEDDINGS_ENABLED='1'
$env:TRACEBRIDGE_EMBEDDING_URL='http://127.0.0.1:8000/v1'
$env:TRACEBRIDGE_EMBEDDING_MODEL='<설치한 NIM의 임베딩 모델>'
# 인증이 필요한 서비스는 TRACEBRIDGE_EMBEDDING_API_KEY를 별도로 설정한다.
# 원격 endpoint는 HTTPS를 사용한다.
```

연결된 프로젝트 화면의 **유사 사건 검색 설정**에서 검토된 요약 전송에 체크한 뒤 색인을 갱신한다. 이후 제보의 외부 분석을 선택한 작업만 임베딩 검색을 수행한다. 외부 분석을 선택하지 않으면 로컬 키워드/지문 검색을 유지한다.

로컬 DB의 명시적 색인·검색도 가능하다.

```powershell
.\.venv\Scripts\python.exe -m scripts.incident_memory --db output/tracebridge/incidents.sqlite3 --project my-app index --limit 50 --allow-embedding-transmission
.\.venv\Scripts\python.exe -m scripts.incident_memory --db output/tracebridge/incidents.sqlite3 --project my-app search --query '계정을 만들 수 없어요' --service backend --environment dev --semantic
```

승인/편집된 카드의 정제된 증상·판정·다음 조치·확인 순서와 오류 지문만 색인한다. 원본 로그·전체 코드·사진은 벡터 색인 대상이 아니다. 카드의 검토가 변경되면 기존 벡터를 즉시 삭제하며 색인 중 변경된 카드도 저장하지 않는다. endpoint/모델을 바꾸면 다른 벡터 공간으로 취급한다. 서비스 장애·잘못된 응답·차원 불일치는 기존 키워드 결과를 지우지 않는다.

색인은 한 요청당 최대 200개, 한 배치 16개, 최대 1,000개 후보를 확인한다. 기본값은 50개다. 조회·색인의 임베딩 호출 수는 별도로 기록하며 실제 과금·토큰 수는 현재 측정하지 않는다.

## 실제 사건으로 검색 품질 평가

기록이 쌓인 것과 품질이 개선된 것은 다르다. 담당자가 실제 제보의 정답 카드와 '재사용할 사례 없음'을 표시한 같은 자료로 키워드/하이브리드 경로를 비교한다. 예시 평가 JSON:

```json
[
  {
    "project_id": "my-app",
    "query": "계정을 만들 수 없어요",
    "scope_filters": {"service": "backend", "environment": "dev"},
    "expected_card_ids": ["검토한-사건의-run-id"]
  },
  {
    "project_id": "my-app",
    "query": "관련 사례가 없는 새로운 증상",
    "scope_filters": {"service": "backend", "environment": "dev"},
    "expected_card_ids": []
  }
]
```

```powershell
.\.venv\Scripts\python.exe -m scripts.evaluate_memory_retrieval --suite output/labeled-cases.json --db output/tracebridge/incidents.sqlite3 --output output/retrieval-lexical.json
.\.venv\Scripts\python.exe -m scripts.evaluate_memory_retrieval --suite output/labeled-cases.json --db output/tracebridge/incidents.sqlite3 --output output/retrieval-hybrid.json --semantic
```

Recall@2·MRR@2·관련 사례 없음에 대한 잘못된 검색 비율·p95 지연과 실패를 기록한다. 결과에는 질의 원문을 쓰지 않고 해시를 남긴다. 이 평가는 검색만 다루며 원인 판정·수정 성공·서비스 회복 성능을 뜻하지 않는다. 임베딩 테스트 더블의 통과는 실모델 품질의 증거로 사용하지 않는다.

## 다음 개선 순서와 완료 조건

| 순서 | 개선 | 완료 조건 |
| --- | --- | --- |
| 1 | 실제 관측 연결 | daily의 지속 요청 로그·request ID·실행 SHA·계약과 실제 제보 하나를 연결하고 제보별 재현 검사를 실행 |
| 2 | 자동 평가와 처리 제안 | 관측된 영향·재현 범위·검사 결과로 중요도/난이도/위험 제안을 만들고 소유자 수정과 오판을 측정. 모델 설명만으로 권한 상승하지 않음 |
| 3 | 실제 사건 평가·재발 검사 | 성공·실패·보류·오판 사례를 담당자가 검토. 기억 끔/켬과 수정 회귀를 같은 입력으로 재생해 품질·시간·비용을 비교 |
| 4 | 적용 후 확인 | 원본 적용 후 핵심 사용자 여정·API와 배포 버전을 확인하고 미회복이면 사건을 다시 열기. 배포/롤백은 별도 정책과 검증 |
| 5 | 팀 운영과 확장 | 프로젝트별 인증·인가, 데이터 보관/삭제·백업 복원, 담당자 배정, 중복 제보 묶음, 상시 실행과 비용 상한 |

현재 구현의 검증 범위는 [고도화 검증 기록](../validation/operational-hardening.md)에 있다.
