# 실제 Linux 배포 이미지 확인

2026-09-29. 배포 준비 상태를 다시 확인하면서 로컬 Docker 엔진에 접근해 실제 이미지를 빌드하고 오프라인으로 실행했다. AWS 리소스와 기존 Agolive 컨테이너는 변경하지 않았다.

이 문서는 당시 고정 사본의 검증 기록이다. 이후 control API·PC 실행기·GUI 보완이 추가됐으며 최신 로컬 검증은 [GUI 최종 기록](local-gui-final.md)에 있다. 아래 이미지를 최종 코드의 검증 이미지로 사용하지 않는다. 배포할 Git 커밋으로 새 이미지를 빌드하고 다시 확인해야 한다.

## 소스와 이미지

- 당시 미커밋 작업 공간에서 배포 소스 135개를 별도 사본으로 복사했다. `.env`·DB·실행 출력·첨부 자료·개인키를 제외했다.
- 사본 manifest의 전체 SHA256은 로컬 `manifest.json`에 기록한다. 이미지 태그에 사용한 접두어는 `e5c57eb8b3f0`이다.
- 이미지 태그: `tracebridge:foundation-e5c57eb8b3f0`.
- 사본은 Git HEAD `f40023d`에 미커밋 소스를 포함한 상태다. 해당 커밋만 clone한 결과와 동일하지 않다.
- 원본 파일을 수정하지 않고 사본에서 Linux amd64·Python 3.12 이미지 빌드를 완료했다.

## 실제 확인

| 항목 | 결과 |
| --- | --- |
| 새 Linux 의존성 설치와 `pip check` | 통과. Windows 잠금 파일을 사용하지 않음 |
| 등록 씨드 원본 정책/소스 해시 | 통과 |
| FTS5·씨드 접수·사건 저장 | WORK_CANDIDATE, 저장·재조회 통과 |
| 새 컨테이너에서 같은 Docker 데이터 볼륨 읽기 | 저장된 실행 1개 복원, SQLite integrity_check 통과 |
| 1 GB·CPU 1·읽기 전용 실행 | 씨드 확인과 프로세스 시작 통과 |
| 초기 기본 화면 | Linux 컨테이너에서 Streamlit AppTest 로딩, 예외 없음 |
| 사용자·자격 증명 경계 | UID 10001, 이미지에 `.env`/app.env와 NVIDIA 키 없음 |
| Caddy | 테스트 암호와 외부 네트워크 차단 상태의 실제 config validate 통과 |

테스트 컨테이너는 호스트 포트를 공개하지 않았고 외부 네트워크도 차단했다. 빌드의 패키지·이미지 다운로드는 외부 네트워크를 사용했다. 임시 컨테이너와 테스트 용도로 만든 Docker 데이터 볼륨은 확인 후 정리했다.

메모리 기록의 `52.47 MiB / 1 GiB`는 AppTest 자식 프로세스 종료 후 남아 있는 웹 프로세스의 한 시점 값이다. 전체 사용자 흐름·사진·모델 실행의 최대 메모리나 실제 2 GB EC2의 수용량을 뜻하지 않는다.

## 서버 전송용 산출물

로컬 경로: `output/deployment-build/local-f5078cdc/`.

- `tracebridge-image.tar.gz`: 검증한 이미지. 492,146,796 bytes.
- `tracebridge-image.tar.gz.sha256`: `d3512d8ff0b5b333d48a4ee1eb1f7c252f80386b1de6b3d9ac250998f3932d97`.
- `deployment-files.zip`: 배포 설정·신규 AWS 템플릿·계정 확인 도구·운영 문서. 실제 비밀 설정 제외.
- `START-HERE.md`: 이미지 태그, checksum 확인, load 및 `up --no-build` 순서.
- `manifest.json`, `runtime-result.json`, `proxy-result.json`, `linux-python-freeze.txt`: 사본 해시와 실제 확인 기록.

이미지 산출물은 Git과 공개 레지스트리에 업로드하지 않았다. 소스와 배포 설정은 Git 릴리스로 관리하고, 이미지에는 그 릴리스 SHA를 기록한다. 이 기록의 사본은 당시 미커밋 상태였으므로 이후 커밋과 동일한 이미지로 간주하지 않는다.

## 남은 단계

1. 새 계정의 실제 무료 자격·프로필·계정 ID, 리전/인스턴스 조건 확인.
2. EC2 생성, Docker·데이터 디렉터리·도메인·비밀값 준비.
3. 실제 공인 HTTPS·인증·브라우저 업로드·저장·재시작 확인.
4. 최종 릴리스의 control API·NVIDIA 게이트웨이·PC 실행기를 공인 HTTPS로 연결한 뒤 실사건 확인. localhost 연결과 공개 예시의 실제 NVIDIA 호출은 [후속 검증](local-gui-final.md)에서 확인했다.

이번 확인은 배포 이미지와 오프라인 기본 동작이다. 실제 버그 해결, 공인 HTTPS, 원격 PC 연결의 성공을 증명하지 않는다.
