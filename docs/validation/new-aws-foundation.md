# 신규 AWS 계정 배포 기반 확인

2026-09-29. 신규 계정의 독립 EC2를 대상으로 기반을 준비했다. 조사·수정 로직, 기존 Agolive 저장소와 서버는 변경하지 않았다.

초기 신규 계정 배포 기반의 확인 기록이다. 이후 소유자 API·PC 실행기와 localhost 실제 GUI·NVIDIA 연결을 확인했으며 [최신 검증](local-gui-final.md)에 별도로 기록했다. 실제 새 AWS 서버 배포는 수행하지 않았다.

## 확인 결과

- **신규 기반 확인 27개 통과:** 명시 AWS 프로필·계정 일치, 잘못된 계정/플랜/잔액/만료 거절 경로를 가짜 API 응답으로 확인했다. 실제 AWS 호출은 0회다.
- CloudFormation YAML과 EC2·보안 그룹·t3.small·Standard CPU 크레딧·IMDSv2·암호화 디스크·공개 포트 정의를 확인했다. AWS의 실제 `validate-template`나 리소스 생성 검증은 수행하지 않았다.
- 수동 GitHub workflow의 트리거·최소 권한·외부 네트워크 없는 검사·선택적 이미지 export·1일 artifact 보관을 확인했다. GitHub workflow를 실제 실행한 것은 아니다.
- **기존 배포 기반 확인 44개 통과:** 새 이미지 smoke helper COPY 입력을 포함한 Compose·쓰기 경로·소스·비밀 제외 정의를 확인했다. 이전 43개 기록에 COPY 입력 확인이 하나 추가됐다.
- 로컬 Python 3.12에서 `deploy/offline_smoke.py`를 실행해 **WORK_CANDIDATE / SQLite 재조회 / 등록 씨드 해시 통과**를 확인했다. 실제 외부 모델 요청은 하지 않았다.

## 실행 명령

```powershell
.\.venv\Scripts\python.exe output/new-aws-foundation/validate.py

$taskPreviousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = (Get-Location).Path
try {
    .\.venv\Scripts\python.exe deploy/offline_smoke.py
    .\.venv\Scripts\python.exe output/deployment-foundation/validate.py
} finally {
    $env:PYTHONPATH = $taskPreviousPythonPath
}
```

로컬 확인 스크립트와 JSON 결과는 `output/new-aws-foundation/` 및 `output/deployment-foundation/`에 보존하며 Git에 포함하지 않는다. 씨드 확인으로 만든 DB는 기존 DB와 다른 `output/deployment-smoke/<실행 ID>/`에 저장한다.

## 남은 확인

1. 새 계정의 실제 Free plan 자격·명시 프로필·잔액·만료일.
2. 대상 리전의 AMI·기본 VPC·public subnet·키·인스턴스 할당량과 CloudFormation 서비스 검증.
3. GitHub에서 Linux 이미지 빌드 및 네트워크 차단·읽기 전용·1 GB 제한의 smoke 실행.
4. 새 서버 기동·데이터 권한·HTTPS·브라우저·저장·재시작 복원.
5. 최종 릴리스의 control API와 PC 실행기를 새 서버의 공인 HTTPS로 연결한 뒤 실제 사건 검증.

처음 기반 확인 시 Docker daemon에 접근하지 못했으나 이후 실제 로컬 Linux 빌드·컨테이너·저장·초기 화면·Caddy 설정을 확인했다. 이 후속 확인은 [Linux 실행 기록](linux-deployment.md)에 따로 기록한다. AWS 리소스 생성·실제 배포·기존 URL 장애 수정은 수행하지 않았다.
