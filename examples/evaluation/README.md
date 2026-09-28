# 12건 고정 평가 자료 — 초안

6유형×2변형이며 모든 사건은 **합성/통제 주입**이다. 실제 프로젝트·실제 장애·새 OCR/NIM 성공으로 집계하지 않는다. `suite.json`에 입력·기대값·공통 자료·초기 기억·파일 해시를 고정한다. 파일이 바뀌면 러너가 거부한다. 결과에 맞춰 기대값을 낮추지 않는다.

| 유형 | 변형 1 | 변형 2 |
| --- | --- | --- |
| 정상적인 입력 검증 | 입력 자체 누락 | 정상 입력을 호출자가 누락 |
| 계약/호출자 결함 | user_id/userId | account_id/accountId |
| 모호한 자연어·사진 | ID 없이 같은 시각 복수 후보 | 합성 사진의 500 주장과 현재 422 |
| 불완전·충돌 자료 | 상위 조회 중단/일부 관측 | 같은 ID·범위의 500/422 충돌 |
| 비슷한 과거, 다른 현재 | 같은 경로/상태, 다른 예외 | 현재 버전과 호출자 근거 버전 불일치 |
| 적응형 조사·실행 실패 | 첫 검색 무관, 다른 검색어로 조회 | 서빙 timeout 및 미검증/실패 조치 보류 |

사진의 문자 내용은 고정된 합성 PNG와 `ocr_transcript`다. 규칙/단일 프롬프트/코딩 에이전트는 같은 전사 자료를 받고, 로컬 TraceBridge에도 같은 문자를 제보 단서로 전달한다. doubles는 OCR transport double로 전사를 반환한다. 실제 OCR를 호출하지 않는다.

`project/client.py`는 작은 합성 호출자이며 버전 snapshot도 합성이다. 현재 주 소스 어댑터 호환을 위한 네 manifest가 있고 Agolive 원본은 없다. 실행 권한은 별도로 등록돼야 한다. 코드 검색을 위해 `frontend/src/client.py`에 같은 자료를 둔다. 수정 비교용 고정 검사·입력·예산은 `resources.json`에서 모든 조건에 동일하게 노출한다. 이 러너는 후보 변경을 자동 실행하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m scripts.evaluate_incidents --mode local
.\.venv\Scripts\python.exe -m scripts.evaluate_incidents --mode doubles --conditions rules tracebridge_memory_off tracebridge_memory_on
```

결과는 `output/parallel-d/evaluations/<새 실행 ID>`의 **DRAFT**다. `single_prompt`와 `coding_agent`는 실제 관측 가져오기 계약인 `observation-template.json`을 제공한다. 새 모델 호출/사람의 시간/답변을 만들지 않으며 기록이 없으면 `BLOCKED`다. 평가상 실패와 기능 미실행을 결과에 보존한다. Exit 0=선택 조건 전부 실행·기대 통과, 1=실행 실패/기대 불일치, 2=실행 불가 조건 존재(실패가 있으면 1 우선).

외부에서 기록한 기준선은 원자료 파일의 상대 `source_ref`·SHA-256, 현재 `suite.json`의 SHA-256, 공통 자원 계약 해시, 기억 조건, 수행 범위를 붙여 가져온다. `execution_origin`은 `RECORDED_LIVE`, `RECORDED_REPLAY`, `RECORDED_DOUBLE`, `RECORDED_HUMAN`으로 구분하고 수행 범위·원점별로 집계한다. 사람 시간/수작업이 관측됐으면 별도 `human_measurement_ref`·`human_measurement_sha256`을 제공한다. 없으면 `null`이다. `--import-observations`는 기록이 있는 조건만 가져오고 나머지 로컬 조건은 그대로 실행한다.

검증된 조치는 before/after의 같은 command ID·입력/검사/설정 해시, 수정 전 실패·후 성공·회귀 성공, 원자료 파일/해시, 실제 diff/원본 보존 자료를 확인해야 집계한다. 이는 기록 증거의 일치 검사이며 등록 작업자의 정책 검사·실제 검사 재실행을 대체하지 않는다. 현재 내장 어댑터는 `diagnosis_only`이고 후보 수정은 실행하지 않으므로 조치 검증을 완료했다고 발표하지 않는다.

기억 초기 DB는 `initial_memory.json`의 검토된 합성 단서로 새로 만든다. 매 사건·조건마다 SQLite backup을 복제하므로 현재 평가 결과가 다음 실행에 유입되지 않는다. `--initial-db`도 원본은 읽기 전용이며 각 실행은 복제본을 쓴다. 기억 끔은 검색만 끄고 저장은 유지한다. 어떤 시간·정확도·기억 효과 우위도 이 합성/double 결과로 주장하지 않는다.

R1의 짧은 로그/약 481 KB/6,000줄 절단 재현은 A 전용 검사에서 각각 확인한다. 이 12건은 그것을 대신하는 전체 회귀가 아니다. 실제 수정 전후 동일 검사·회귀 실패/미재현·실사건·NAT 주 흐름·새 환경 최종 ZIP은 통합/실검증 게이트로 남는다.
