# daily 실제 요청 연결

2026-09-29. daily의 로컬 개발 요청을 요청 ID·발생 시각·서비스/환경·응답 상태·실행 빌드와 연결한다. 이 단계는 실제 제보의 근거 수집이며 자동 수정·배포·회복은 별도다.

## 개발 서버와 연결

daily 소스에 요청 관측 계측이 있어야 한다. 이 저장소의 연결 도구는 등록 프로필·계약 사본을 만들며 daily 소스를 수정하거나 배포하지 않는다. 현재 검증은 별도 daily 작업 트리의 계측 변경으로 수행했다. 해당 저장소의 게시 상태와 검증 범위는 [검증 기록](../validation/daily-observation.md)을 확인한다.

daily의 `local` 프로필에서 요청 관측이 기본 활성화된다. 기존 개발 DB/Redis와 개발 기동 절차를 그대로 사용한다. 기본 로그 디렉터리는 Gradle `bootRun`의 작업 디렉터리인 `backend/app` 아래 `logs/tracebridge`다. 다른 작업 디렉터리에서 JAR을 실행할 때는 경로를 명시한다.

```powershell
# daily를 시작할 터미널에 설정. 기존 dev.ps1/개발 서버 실행 전에 적용한다.
$env:DAILY_OBSERVATION_DIR='C:/Users/freetime/Desktop/projects/daily/backend/app/logs/tracebridge'
$env:DAILY_TRACEBRIDGE_PROJECT='daily-local'
# 끄려면 DAILY_OBSERVATION_ENABLED=false
```

TraceBridge 저장소에서 연결을 등록한다. 기존 프로젝트 ID·root·dev 범위를 확인하며 소유자의 수정 정책과 원본 적용 권한을 보존한다.

```powershell
.\.venv\Scripts\python.exe -m scripts.connect_daily --root C:/Users/freetime/Desktop/projects/daily
.\.venv\Scripts\python.exe -m scripts.project_tools --project daily-local doctor
```

기본 주소는 backend `http://127.0.0.1:8081`, frontend `http://127.0.0.1:3100`이다. 실제 개발 포트가 다르면 `--backend-url`과 `--frontend-url`을 지정한다. HTTP 연결 검사는 명시한 loopback·포트만 허용하고 redirect를 따라가지 않는다. 설정 변경 뒤 TraceBridge 웹·API·PC 실행기도 다시 실행한다.

소스·빌드를 변경했으면 daily를 재시작한 뒤 연결 명령을 다시 실행해 계약/DTO와 caller 해시를 갱신한다. 이전 파일을 현재 빌드의 근거로 자동 승격하지 않는다.

## 사용 흐름

1. daily의 API 클라이언트가 요청마다 `X-Request-Id`를 만든다. 백엔드는 같은 ID를 응답과 로컬 JSONL에 남긴다.
2. 실패 화면의 오류에는 요청 ID와 발생 시각이 포함된다. 브라우저의 최근 실패 20개에는 경로·메서드·응답 상태·관측 여부·빌드 정보를 보관한다. 새로고침하면 사라지며 외부 전송이나 영속 저장은 하지 않는다.
3. **연결된 프로젝트 제보** 또는 로컬 **제보 에이전트**에서 daily의 backend를 선택하고 오류 문구·요청 ID·시각을 제보한다. API 실패 로그의 서비스 범위는 backend다. 프론트 코드 조사 자체는 frontend 선택을 사용할 수 있다.
4. TraceBridge가 같은 환경·서비스와 발생 시각 ±5분에서 요청 ID를 정확 대조한다. 시각이 없거나 범위가 충돌하면 추가 질문·보류를 유지한다.
5. 실제 응답·코드·계약과 실행 빌드를 확인한다. 400/409와 연결 성공만으로 사용자 책임·코드 결함·수정 성공을 확정하지 않는다.

PC 온라인, 코드/로그 연결, 마지막 서비스 응답을 화면에서 구분한다. 서비스 응답은 담당 PC의 최근 확인이며 핵심 사용자 여정이나 제보 해결을 대신하지 않는다. startup `runtime.json`도 현재 프로세스가 살아 있다는 증거가 아니다.

## 근거와 보관 범위

- JSONL은 요청 메타데이터와 안정적인 오류 코드만 담는다. 쿼리·본문 값·쿠키·인증 헤더·응답 본문을 저장하지 않는다. 소비된 작은 JSON object의 필드명/타입만 수집하며 본문이 불완전하거나 크면 입력을 미관측으로 둔다.
- 로컬 요청 파일은 1 MB씩 회전하고 7일·10 MB 상한을 설정했다. 기본 등록은 활성 `requests.jsonl`만 읽는다. 회전된 과거 파일이 필요한 사건은 해당 파일을 별도로 명시 등록한다.
- 실행 SHA와 dirty 여부는 Spring Boot가 빌드에 포함한 정보에서 읽는다. Git 메타데이터가 없는 빌드는 버전을 미확인으로 둔다. 수동 Docker 빌드 SHA가 필요하면 빌드 시 `DAILY_BUILD_SHA`를 명시한다.
- 개발 로그인 한 endpoint의 계약/DTO는 현재 Kotlin 소스에서 제한적으로 추출한다. 빌드에 포함된 해당 소스 해시와 현재 파일이 같을 때만 실행 버전 대응을 기록한다. 다른 endpoint의 계약은 추가로 등록해야 한다.
- caller 파일은 해시를 대조하지만 브라우저 실행 버전과 사건별 입력 관측은 별도다. 현재 연결 도구는 이를 확정하지 않아 caller 책임 판단을 보류한다.
- 공개·Google 프로필에서는 이 로컬 파일 관측을 활성화하지 않는다. 중앙 DB 전환과 원본 적용 허용 설정은 변경하지 않는다.

## 실제 HTTP 재현 검사

로컬 개발 로그인의 필수 입력을 비운 요청으로 HTTP 400을 재현한다. 계정·Task를 생성하지 않으며 NVIDIA를 호출하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m scripts.verify_daily_observation
# 프론트 프록시를 포함한 검증:
.\.venv\Scripts\python.exe -m scripts.verify_daily_observation --backend-url http://127.0.0.1:3100 --output output/daily-observation/proxy-verification
```

같은 요청 ID·시각·400·입력 필드 관측·빌드 SHA와 `EXACT_ID`를 확인하고 전용 사건 DB 및 JSON 근거를 저장한다. 이 검사는 개발 환경의 통제된 입력 검증 실패이며 실제 사용자 장애나 코드 결함 수정의 증거로 사용하지 않는다. 실제 증상을 선택하면 그 요청의 재현 검사를 별도로 등록한다.
