# 심사 체험 검증

2026-10-01 KST. 기존 AWS 배포 구성을 main의 최신 간편 화면·회귀 보완과 통합하고 심사용 공개 진입점을 추가했다.

## 실제 실행

- 공개 API의 브라우저 예제: 실제 HTTP 500·`EXACT_ID` 요청 로그 연결, 수정 전 검사 종료 1, 수정 후 같은 검사 종료 0, 독립 회귀 검사 종료 0, 원본 파일 적용, snapshot 대응 실제 API 회복 `PASSED`·사건 `RESOLVED`.
- 다운로드 ZIP: 약 281 KB의 소스 묶음. 명시적 소스 목록만 수집하고 `.env`, 운영자 token, 사건 DB, output, 기존 프로젝트를 제외한다. 각 다운로드에 독립 프로젝트 ID와 24시간 서명 권한을 부여한다.
- Windows / Python 3.12의 새 venv에 실행기 의존성을 설치하고 `pip check` 통과. 다운로드한 소스만 사용하는 Streamlit AppTest에서 예제 생성·실행, 실제 실패 조사, 수정안 검사, diff 검토 후 적용, 실제 API 회복을 끝까지 통과했다. 설치한 의존성 전체를 `requirements-review.txt`에 고정했다.
- 두 체험의 프로젝트·작업 조회 분리, 소유자 API 401, 변조·만료·잘못된 서명 거절, 기존 연결의 재페어링 거절, 공개 AI 수정의 Docker 정책 요구를 검사했다.
- 관련 프로젝트 등록·진단·실행기·AWS 검사 67개 통과. Windows 전체 초기 회귀는 763 통과·42 건너뛰기·1 실패였으며, 새 Linux 잠금 파일을 Windows 설치와 대조하던 검사였다. 해당 검사를 OS별 잠금 파일에 맞춰 수정했다. PostgreSQL과 실 Docker 실행은 로컬 초기 전체 실행에서 생략됐으며 배포 CI에서 확인한다.
- 실제 브라우저에서 새 첫 화면과 실행 버튼을 확인하고 HTTP 500 → HTTP 200·검사 결과·적용 diff·회복 확인이 표시되는 것을 확인했다.

추가 관련 검사 29개를 통과했다. 배포 커밋 `bf715f86f002570e44c64f2bbcec323ba54363ea`의 [Linux 전체 회귀](https://github.com/chan808/nvidia0927/actions/runs/36874344295)는 **804개 통과·Windows 전용 2개 건너뛰기·실패/오류 0개**, 167.192초다. 실제 PostgreSQL/pgvector를 포함한다.

[AWS 배포](https://github.com/chan808/nvidia0927/actions/runs/36874343793)는 이미지·의존성·씨드·전체 Compose·PostgreSQL·TLS·재시작 보존 검사를 통과하고 동일 커밋의 회귀 성공을 확인한 뒤 기존 서버를 업데이트했다. Compose의 TLS 경로에서도 실제 예제의 HTTP 500 → HTTP 200, 검사 `[1, 0, 0]`, 원본 적용과 `PASSED` 회복을 확인했다.

공개 주소 [TraceBridge](https://tracebridge.ckswhd.shop/)에서 인증 없는 HTTP 200 첫 화면과 예제 성공 결과를 실제 브라우저로 확인했다. `/owner/`와 `/v1/projects`는 인증 없이 HTTP 401을 반환했다. 실제 ZIP 다운로드는 HTTP 200·281,362바이트였고 연결 주소가 공개 HTTPS 도메인이며 기존 비밀 파일·DB를 포함하지 않았다. 체험 권한으로 소유자 API에 접근하면 401이다.

개인 실행 결과는 `output/reviewer-tests-03.xml`, `output/reviewer-full-01.xml`, `output/reviewer-final-target.xml`, `output/reviewer-fresh-bundle/`, `output/reviewer-ci-regression/`, `output/reviewer-ci-deployment/`, `output/reviewer-public-verification.json`에 둔다.

## 범위

기본 예제는 `DEMO_FIXED_PATCH`이며 NVIDIA 호출 0회다. 실제 조사·수정 검증·적용·회복 파이프라인을 보여주는 고정 합성 예제로, AI 수정 성능이나 실제 사용자 장애 해결을 입증하지 않는다. 다운로드 실행기는 Windows의 Python 3.12와 최초 인터넷 설치가 필요하다. 실행 파일 하나로 배포하는 포터블 설치기는 후속 범위다.
