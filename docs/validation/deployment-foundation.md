# 배포 기반 확인

2026-09-29. 이번 작업은 배포 설정과 운영 가이드에 한정했다. 조사·판정·수정 코드와 다른 세션의 변경은 보존했다.

이 문서는 초기 배포 기반 확인의 기록이다. 후속 소유자 API·PC 실행기 구현과 실제 모델·GUI 검증은 [최종 GUI 기록](local-gui-final.md)에 있다. 아래 확인 횟수와 미확인 목록은 초기 시점에 해당한다.

## 준비한 구성

- 기존 Python 3.12·Streamlit 앱을 설치하는 Linux Dockerfile.
- Caddy HTTPS·비공개 접근 암호와 웹 서비스의 Compose 설정.
- 사건 DB·관측·수정안·메트릭을 보존할 `/app/output` 영속 디렉터리.
- 실제 서버 비밀 설정의 Git 제외와 빌드 소스 허용 목록.
- 서버 생성·DNS·설치·기동·확인·백업·업데이트·롤백 및 로직 세션 인계 가이드.

## 실행한 확인

로컬 확인 스크립트 `output/deployment-foundation/validate.py`에서 처음 33개 확인이 통과했고, 무료 소형 서버·기존 Nginx 공유 설정의 확인 10개를 추가해 **43개 확인이 통과**했다. 이것은 배포 설정 및 소스 확인이며 서비스 통합 검사 43개나 Linux 실행 성공을 의미하지 않는다.

| 항목 | 결과 |
| --- | --- |
| Docker Compose 5.0.2의 설정 해석 | 예시 설정으로 통과. 실제 키는 사용·출력하지 않음 |
| 공개 포트 | 프록시의 80/443만 공개, 웹 8501은 내부 |
| 저장·접근 | output 영속 bind, 앱 읽기 전용, 프로젝트 경로 등록 비활성화, Docker socket 마운트 없음 |
| 키 전달 경계 | 프록시에 NVIDIA 키 환경 변수 없음 |
| Streamlit 설정 | 설치된 1.64.0의 환경 변수 이름 확인. 실제 CLI 파싱에서 도메인·포트·쿠키 설정 반영 확인. 서버 시작은 가로채어 실행하지 않음 |
| Linux 설치 정의 | 기존 pyproject의 의존성 사용. Windows 잠금 파일 참조 없음 |
| Dockerfile | COPY 입력 존재, 비루트 사용자·상태 확인·FTS5 확인 정의 점검 |
| 빌드 대상 | 비밀·DB·runtime output을 COPY하지 않음. 기본 제외와 소스 재포함, 중첩 비밀 제외 규칙 점검 |
| Git 제외 | `deploy/.env`, `deploy/app.env`, `deploy/local-preview.override.yaml` 제외 확인 |
| 씨드 보존 | 기존 등록 정책과 원본 소스 해시 검사 통과 |
| 새 파일 공백 | 뒤쪽 공백 없음 |
| 무료 소형 서버 | Compose 병합, 1 GB 웹 제한, 웹 포트 비공개 확인 |
| 기존 Nginx 공유 | Compose 병합, 512 MB 웹 제한, 기본 서비스에서 Caddy 제외, 외부 네트워크·upstream 별칭 확인 |
| Nginx 템플릿 | WebSocket·인증·hostname 인증서 정의와 인증서 발급 전 503 정의 점검. 실제 `nginx -t`는 미수행 |
| 외부 모델·OCR 호출 | 0회 |

판독 결과와 파일 SHA256은 로컬의 `output/deployment-foundation/result.json`에 보존한다. 이 output은 Git에 포함되지 않는다.

실행 명령:

```powershell
$taskPreviousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = (Get-Location).Path
try {
    .\.venv\Scripts\python.exe output/deployment-foundation/validate.py
} finally {
    $env:PYTHONPATH = $taskPreviousPythonPath
}
```

검사 스크립트는 이 작업 공간의 확인용 산출물이다. 새 clone에서는 [배포 가이드](../guides/deployment.md)의 실제 Linux 빌드·상태·저장·HTTPS 확인 절차를 수행한다.

## 아직 확인하지 않은 것

이 초기 설정 확인 시에는 `dockerDesktopLinuxEngine` daemon에 접근하지 못했다. 이후 [Linux 실행 기록](linux-deployment.md)에서 실제 빌드·1 GB 실행·SQLite 재조회·초기 화면·Caddy 설정을 확인했다. 아래 항목은 당시의 미확인 범위이며 실제 서버·실모델·PC 연결 확인은 계속 남아 있다.

- 새 Linux 환경의 의존성 설치·이미지 빌드.
- 비루트·읽기 전용·볼륨 권한을 적용한 컨테이너 실제 기동.
- Caddy의 실제 설정 로드, 인증서 발급, 외부 HTTPS, 브라우저 WebSocket·사진 업로드.
- 서버 재시작 후 사건 조회와 데이터 백업/복원.
- 실제 NVIDIA 통신, 로컬 실행기 페어링·작업 전달·프록시 연결.

## 기존 AWS 서버 읽기 전용 확인

사용자가 Agolive 프로젝트 접근을 허가한 뒤 기존 Terraform state와 AWS CLI 계정이 일치함을 확인했다. AWS Free Tier 플랜, EC2 실행 상태, SSM Online을 조회했고, SSM에서 메모리·디스크·컨테이너 상태·Nginx 네트워크·공개 hostname 경로만 읽었다. API 키·컨테이너 환경·개인키는 출력하지 않았다. 서버 설정 변경이나 배포는 수행하지 않았다.

실제 조회 결과는 `output/deployment-reuse/aws-live-inspection.json`과 `server-resources.json`에 보존한다. 계정·잔액 등 운영 정보는 Git에 포함하지 않는다. 공유 배포 판단과 절차는 [기존 서버 재사용](../guides/existing-server.md)에 정리했다.

초기 확인의 빌드 컨텍스트 제외 규칙은 소스 수준에서 검사했으며 실제 이미지 확인은 후속 [Linux 실행 기록](linux-deployment.md)에 있다. 기본 템플릿은 **번들 씨드의 비공개 미리보기**다. 이후 추가한 `control-plane.compose.yaml`과 PC 실행기의 현재 운영 절차는 [실제 프로젝트 사용법](../guides/real-projects.md)을 따른다.
