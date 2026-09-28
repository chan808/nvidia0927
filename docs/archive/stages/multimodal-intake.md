# 자연어·사진 제보와 NVIDIA 조사

> 과거 기록입니다. 당시 계획·검사·제약을 보존하며, 현재 상태는 [구현 상태](../../current-state.md)를 기준으로 확인하세요.

제보자는 자연어 지시, 거친 증상 한 줄, 사진 한 장을 제출할 수 있다. HTTP 메서드·API 경로·trace ID를 필수 입력으로 요구하지 않는다. 사용자의 `고쳐줘`는 요청 의도를 뜻하며 코드 변경 권한을 부여하지 않는다. 현재 경로는 읽기 전용 조사다.

## 처리 흐름

1. 사진을 PNG/JPEG·8 MB·2천만 픽셀로 검증하고, 방향을 반영한 뒤 메타데이터를 제거해 전송 크기를 제한한다. 원본 사진은 자동 저장하지 않는다.
2. 외부 분석을 선택하면 **NeMo Retriever OCR NIM**으로 문구와 신뢰도를 읽는다. 신뢰도 0.75 미만의 문구는 검색·요청 ID 연결에 쓰지 않는다. OCR의 `requestId` 라벨 I/l/1 혼동만 보정하며 ID 값은 바꾸지 않는다.
3. 공통 `ReportContext`로 제공 사건/등록 로그의 시각·서비스·환경·프로젝트 범위를 검사하고 기존 판정기와 라우팅 함수를 사용한다. 명확한 관측은 모델 없이 `GUIDANCE/WORK_CANDIDATE`를 반환한다. 0/복수 후보·범위 부족은 `REQUEST_CONTEXT`, 한 범위 후보나 정확 연결된 서버 오류는 `INVESTIGATE`다.
4. 모호한 사진에 읽을 문구가 거의 없으면 Nemotron Vision으로 보이는 증상을 설명한다. 조사 모델은 `search_code`, `find_logs`, `get_version`을 선택한다. 모델 최대 4회(Vision 포함), 읽기 도구 최대 6회(자동 조회 포함), 코드 6개·로그 20줄로 제한한다. 충분한 근거/질문/한도에 도달하면 종료하며 `finish_investigation`에서 후보 최대 1개를 받는다.
5. 사진/프로젝트 설명만 지지하는 원인 후보는 제외한다. JSON 최상위/trace 식별 필드와 MDC만 상관에 사용하고 본문 ID·무관한 스택·범위 불일치는 제외한다. 시각 모델이나 조사 모델이 만든 ID는 정확 상관에 쓰지 않는다. 배포 미확인/불일치는 가설 지지 수준을 낮추며, 근거 ID 검사도 인과관계·재현 확인을 대신하지 않는다.
6. 화면·동작·대략적인 시각·오류 문구를 쉬운 한국어로 질문한다. **같은 사건에 답변 반영** 또는 CLI `--answer`는 세션 사건 ID를 유지해 현재 자료를 다시 읽는다. 이전 관측은 history에 보존하고 반대 근거에 따른 가설 변경을 기록한다. 라우팅과 `COMPLETED/WAITING_CONTEXT/PARTIAL_FAILURE/TIMED_OUT/BUDGET_EXHAUSTED`를 분리한다. 원인·수정·배포 성공은 표시하지 않는다.
7. [SQLite 최소 기록](stage-3-memory.md)을 저장한다. 검토된 과거 카드를 별도 조사 단서로 검색하고 현재 요청·로그·버전 출처를 재확인한다. 후속 답변은 별도 실행으로 보존하며 CLI `--resume/--answer`는 자료 연결 해시·구조화 단서·답변 횟수로 재시작 후 같은 사건을 다시 조회한다. 원문/OCR 본문·사진은 복원하지 않는다. 저장 실패는 조사 결과와 분리하며 저장만 재시도한다.

NVIDIA는 [NeMo Retriever를 마이크로서비스 집합](https://docs.nvidia.com/nemo/retriever/)으로 설명하고, [Image OCR을 NeMo Retriever OCR 마이크로서비스](https://docs.nvidia.com/nim/ingestion/image-ocr/latest/overview.html)로 분류한다. 사진 접수의 실제 기능에 이 서비스를 사용한다. NAT 또는 일반 LLM NIM만으로 이 사용을 주장하지 않는다.

## 실행

Streamlit 사이드바의 **Report Agent(제보 에이전트)**에서 글 또는 사진을 제출한다. NVIDIA 전송 선택을 켜면 크기 조정·메타데이터 제거된 사진과 선별·비식별화한 제보·코드·로그가 외부 서비스로 전송된다. 사진의 시각적 개인정보를 자동으로 완전히 가린다는 보장은 없다.

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
.\.venv\Scripts\python.exe -m scripts.investigate_report --report '방에 들어가면 계속 튕겨요. 확인해줘.'
```

관리자는 `TRACEBRIDGE_LOG_FILE`에 로컬 `.log/.txt/.jsonl` 파일을 연결한다. 모델은 경로를 정하지 못하고 마지막 300 KB만 읽는다. 누락 메타데이터는 미확인이며, 관리자 `TRACEBRIDGE_LOG_SERVICE/ENVIRONMENT/TIMEZONE`은 등록 정보로 구분한다. Docker 조회는 별도 선택한다. `TRACEBRIDGE_DEPLOYED_SHA`는 수동 설정이다. 실행 컨테이너의 revision label을 읽는 별도 코드는 구현했으나 실제 Agolive 배포 관측은 확보하지 못했다. 설정은 환경 변수 또는 로컬 `.env`로 CLI/화면에 전달한다.

`--events`는 기존 사건 목록을 같은 접수에 연결하고 `--answer`는 쉬운 답변을 같은 사건에 반영한다. 선택적 `--environment/--service/--occurred-at`은 기술 캡처/관리자 재현용이며 제보자 필수 입력이 아니다. 로그 범위와 네 라우팅의 합성 재현 명령은 [통합 결과](stage-2-integration.md)에 있다.

새 조사 경로의 모델은 `TRACEBRIDGE_INVESTIGATION_MODEL`로 설정하며 기본값은 실제 한국어 조사 검증에 성공한 `nvidia/nemotron-3-super-120b-a12b`다. 기존 합성 화면의 `NVIDIA_MODEL` 설정은 별개다. 시각 해석은 `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`을 사용한다. 이용 가능 모델은 변경될 수 있으므로 실제 호출로 확인한다.

## 합성 자료로 실제 API 검증

```powershell
.\.venv\Scripts\python.exe -m scripts.smoke_ocr
.\.venv\Scripts\python.exe -m scripts.investigate_report --image output/validation/room_error.png --repo tests/fixtures/agolive_repo --logs-file examples/agolive_error.log --live --output output/validation/report_agent.json
.\.venv\Scripts\python.exe -m scripts.investigate_report --image output/validation/loading_screen.png --repo tests/fixtures/agolive_repo --live --output output/validation/visual_report_agent.json
```

이 명령은 직접 만든 오류 화면과 테스트 저장소·합성 로그를 사용한다. 실제 Agolive 운영 자료는 포함하지 않는다. 기존 성공 결과는 `output/validation/`에 있으며 [검증 기록](../../validation/nvidia-validation.md)에 요약한다. 이번 통합에서는 기존 성공을 재사용하고 외부 호출을 반복하지 않았다. 범위/배포 검사가 강화됐으므로 이전 조건의 로그는 현재 경로에서 후보·미확인으로 남을 수 있다. 예산 소진·모델 오류에도 관측을 보존한다.

공통 접수의 127개 회귀에 사건 기억 신규 30개를 더한 총 157개가 통과했다. 실제 Agolive 실행 로그/배포 연결과 실사건 검증은 미완료다. 새 화면의 실제 사진 업로드+외부 API 종단 검증도 재실행하지 않았다. [통합 결과와 남은 자료](stage-2-integration.md), [사건 기록·검토·검색과 합성 재현](stage-3-memory.md)을 현재 기준으로 삼는다. 이 사건 기억 작업의 새 외부 모델 호출은 0회다.
