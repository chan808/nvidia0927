# 프로젝트 등록과 글·사진 제보

기본 웹 화면은 **연결된 프로젝트** 하나다. 왼쪽에서 프로젝트를 등록·선택하고 증상이나 사진을 보내며, 오른쪽에서 연결 상태를 확인한다.

## 사용 순서

1. **프로젝트 추가**에서 이름·이 컴퓨터의 프로젝트 폴더 전체 경로·실행 주소(선택)를 입력한다.
2. 로컬 소유자 웹은 해당 프로젝트만 사용하는 PC 실행기를 자동으로 연결한다. 연결 정보가 게시되면 제보 버튼이 활성화된다.
3. 오른쪽의 **프로젝트 연결**과 **웹 화면/API 응답**을 확인한다. PC 연결과 대상 서비스 실행 상태는 각각 표시한다.
4. 증상 글이나 PNG/JPEG 사진을 보내고, 원하는 경우 **AI로 사진·원인 분석**을 선택한다. 제보·사진과 필요한 코드·로그가 NVIDIA 분석 서비스로 전송된다.
5. 제보 결과와 추가 질문을 같은 화면에서 보고 **추가 설명 보내기**로 이어간다.

사진은 8 MB/2천만 픽셀 이하의 PNG/JPEG 한 장을 검증하고 metadata를 제거한 제한된 JPEG로 전송한다. AI 분석을 선택하지 않으면 외부 OCR/vision을 호출하지 않고 사진 문구·동작을 추가로 묻는다. 사진 문구는 제보 단서이며 서버 관측이나 원인 확인의 증거를 대체하지 않는다.

## 고급 설정

서비스별 분석 범위, 코드·로그·계약·실행 버전 자료, 수정 검사 권한, 작업 이력·공개 접수·지식 검색은 **고급 설정**에 있다. 제보마다 서비스나 검사 정책을 선택할 필요는 없다. 프로젝트의 기본 분석 범위를 사용하고 후속 답변은 기존 사건 범위를 유지한다. 여러 앱이 있는 프로젝트는 관리자가 이 설정에서 로그와 분석 범위를 등록한다.

저장된 사건 카드는 **고급 설정 → 사건 지식 검토**에서 담당자가 근거·적용 범위를 확인한 뒤 검색 재사용을 승인하거나 제외한다. 승인 전 기록은 검색에 쓰이지 않는다. [검토와 품질 비교 사용법](knowledge-review.md)을 참고한다.

수정 후보는 해당 앱에 등록된 검사만 선택한다. 폴더 등록만으로 코드 실행·원본 수정 권한을 부여하지 않는다. 새 프로젝트의 자료가 부족하면 추가 연결이나 질문이 필요하다. [상세 연결 점검](project-connection.md)과 [서비스 회복](project-recovery.md)을 함께 확인한다.

## 로컬 웹/API 최초 실행

현재 이 PC는 기존 `output/local-service` 설정으로 실행 중이다. 기존 인증·DB는 [재실행 명령](real-projects.md#이-pc에서-바로-사용)을 사용한다. 새 환경에서 소유자 API를 처음 준비할 때는 서비스 의존성과 한 번의 소유자 설정이 필요하다.

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[service]'
# 처음 한 번만: 기존 인증 파일은 보존
if (-not (Test-Path output/control-plane/operator-token)) {
    .\.venv\Scripts\python.exe -m scripts.serve_control --init-token-file output/control-plane/operator-token
}
$env:TRACEBRIDGE_OPERATOR_TOKEN_FILE=(Resolve-Path output/control-plane/operator-token).Path
$env:TRACEBRIDGE_CONTROL_DB=(Join-Path (Get-Location) 'output/control-plane/state.sqlite3')
$env:TRACEBRIDGE_CONTROL_URL='http://127.0.0.1:8765'
$env:TRACEBRIDGE_PROJECT_REGISTRY=(Join-Path (Get-Location) 'output/project-profiles')
$env:TRACEBRIDGE_ALLOW_PROJECT_REGISTRATION='1'
# 같은 환경 설정을 사용한 두 터미널에서 각각 실행
.\.venv\Scripts\python.exe -m scripts.serve_control --host 127.0.0.1 --port 8765
.\.venv\Scripts\python.exe -m streamlit run app.py
```

웹에서 등록한 프로젝트의 실행기는 `output/project-runners/<ID>`에 연결을 보존하고 백그라운드로 실행한다. 다른 PC에 이미 연결된 프로젝트를 바꾸지 않는다. 원격 PC의 기존 페어링은 고급 설정의 **다른 컴퓨터 연결**과 [운영 가이드](real-projects.md)를 사용한다. 중앙 배포 웹에서는 로컬 경로 등록을 켜지 않는다.
