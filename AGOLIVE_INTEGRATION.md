# Agolive 제보 조사 연결

작성: 2026-09-27. 이 연결은 Agolive 저장소를 **읽기 전용**으로 조사한다. Agolive 파일을 바꾸거나 API 테스트·배포·수정을 실행하지 않는다.

2026-09-28 추가: 아래는 기존 **Agolive 제보 조사** 페이지의 수동/GPT 경로다. 새 **Report Agent** 페이지와 `scripts.investigate_report`의 공통 판정·로그 범위·후속 답변·실행 revision label 조회는 [통합 결과](docs/stage-2-integration.md)에 있다. 새 경로의 합성 검사는 완료됐으나 실제 실행 로그·배포 연결과 실사건 검증은 여전히 미완료다. 기존 페이지를 새 범위 검사 경로로 간주하지 않는다.

## 입력과 자료 흐름

1. 사용자가 자연어 오류 제보를 입력한다. `requestId=...`가 있으면 같은 ID의 로그를 우선 선택한다.
2. TraceBridge가 Agolive의 네 서비스 manifest와 현재 로컬 Git 커밋을 확인하고, 제보와 관련된 코드 줄을 제한적으로 찾는다. 검색 대상은 `backend/src/main`, `realtime`, `agolive-agent`, `frontend/src`의 소스 파일뿐이다. `.env`, Terraform 상태, 의존성 폴더와 테스트 파일은 검색하지 않는다.
3. 사용자가 로그를 붙여넣거나 파일로 제공할 수 있다. 선택한 경우에만 로컬 Docker Compose `api`·`realtime`의 최근 로그도 읽는다. 각 자료는 양과 길이를 제한하고, 흔한 키·토큰·이메일·IP·사용자 ID를 표시 및 GPT 전송 전에 가린다.
4. **명시적으로 GPT 전송을 선택한 경우에만**, Agolive `agolive-agent/.env`의 `OPENAI_API_KEY`와 `LLM_MODEL`을 메모리에서 사용한다. 키는 TraceBridge 설정 파일로 복사하지 않는다. 첫 모델 호출은 코드 검색어를 제안하고, 두 번째 호출은 선별된 근거 ID가 붙은 원인 후보와 검증 절차를 제안한다. [OpenAI의 Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)를 사용하며, 존재하지 않는 근거 ID는 결과에서 제거한다.
5. 결과에는 로컬 코드 버전, 로그의 요청 ID 연결 여부, 코드·로그 근거 위치, 원인 후보, 검증 방법, 후보 수정과 부족한 정보를 분리해 표시한다. 재현과 수정은 아직 수행하지 않으므로 원인을 `CONFIRMED`로 표시하지 않는다.

## 실행

기본 저장소 위치는 TraceBridge 옆 `../projects/agolive`다. 위치가 다르면 `TRACEBRIDGE_AGOLIVE_REPO`를 설정한다.

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

사이드바의 **Agolive 제보 조사** 페이지에서 제보와 선택 로그를 입력한다. GPT 전송 체크박스는 기본 꺼짐이다.

```powershell
# 코드만 조사 — 외부 모델 호출 없음
.\.venv\Scripts\python.exe -m scripts.analyze_agolive --report '방 입장 시 ROOM_FULL 오류'

# 명시적으로 제공한 로그 파일을 함께 조사
.\.venv\Scripts\python.exe -m scripts.analyze_agolive --report '방 입장 시 ROOM_FULL requestId=abcd1234' --logs-file .\error.log

# 로컬 Docker가 실행 중일 때만 최근 로그 조회
.\.venv\Scripts\python.exe -m scripts.analyze_agolive --report '방 입장 실패' --docker --since-minutes 30
```

GPT 분석이 필요하면 CLI에 `--gpt`를 명시한다. 이 옵션은 **제보와 선별·비식별화한 Agolive 코드·로그 줄을 OpenAI API로 전송한다**. 비식별화는 완전한 비밀 탐지 수단이 아니므로 전송 전 로그 내용을 검토해야 한다.

## 현재 검증과 한계

- 실제 Agolive 로컬 저장소에서 `ROOM_FULL` 제보가 Go WebSocket 처리와 Spring 오류 코드로 연결되는 것을 확인했다. 로컬 코드 버전은 실제 배포 버전과 다를 수 있다.
- 테스트는 코드 검색, `requestId` 로그 선택·비식별화, 가짜 GPT 응답의 근거 ID 검증을 확인한다. 실제 OpenAI API 호출은 외부로 Agolive 코드를 전송하는 승인 검토에서 거절되어 실행하지 못했다.
- 현재 로컬 Docker 엔진이 실행 중이지 않아 컨테이너 로그 조회의 실데이터 검증은 못 했다. 로그를 제공하지 않으면 실행 시점의 오류를 확인할 수 없고 코드 기반 가설만 가능하다.
- Spring의 로그 `requestId`는 `SecurityLoggingFilter`에서 생성되어 로그에 기록된다. 현재 코드상 응답 헤더로 전달되는 흐름은 확인하지 못했다. 제보에 ID가 없으면 문구 기반 후보이며 동일 사건으로 확정하지 않는다. 발생 시각을 제보에서 해석해 로그를 좁히는 기능은 아직 없다.
- 서비스 수정, 재현 테스트, 취약점 검사, 운영 로그·배포 버전 자동 연결은 아직 구현되지 않았다.

## 다음 연결

Agolive의 실제 오류 제보 한 건에 대해 발생 시각·환경·요청 ID 또는 해당 로그를 확보하고, 실행된 배포 버전을 조회하는 읽기 전용 어댑터를 추가한다. 그 자료와 로컬 코드 커밋을 연결한 뒤 재현·후보 수정 검증을 별도 단계로 만든다.
