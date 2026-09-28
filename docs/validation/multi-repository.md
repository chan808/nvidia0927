# 독립 저장소·로그 경로의 로컬 검증

2026-09-29. 배포 방향은 클라우드 웹/AI API와 프로젝트 PC의 로컬 실행기다. 이번 변경은 그 로컬 기반이며 원격 연결 완성을 뜻하지 않는다.

## 변경

- `ProjectProfile.repositories`에서 각각의 ID·서비스·루트·코드 하위 경로를 명시한다. 같은 부모 디렉터리나 같은 드라이브를 요구하지 않는다. 모델의 조회 인자는 경로를 받지 않는다.
- 로그는 등록한 repository 안의 JSON/JSONL/text 파일을 읽는다. 시간대 없는 text 로그에는 실제 시간대를 등록한다. 계약/DTO/호출자/버전 자료도 명시한 등록 루트 안에서 읽고 호출자 파일은 해시를 확인한다.
- 소스 근거에 저장소 ID·그 저장소의 로컬 Git 버전과 변경 여부를 보존한다. 기본 저장소의 실행 버전이 다른 저장소의 배포 근거를 대신하지 않는다.
- 로컬 관리자 등록 화면과 `output/project-profiles` 저장/재시작 조회를 추가했다. `TRACEBRIDGE_ALLOW_PROJECT_REGISTRATION=1`인 소유자의 로컬/비공개 실행 환경에서만 등록 화면을 켠다.

## 실제 수행한 검사

등록한 별도 임시 frontend/backend/log 디렉터리의 실제 파일을 읽었다. 같은 파일명이어도 두 저장소를 구분하고, 파일 내용을 바꾼 뒤 다음 조회에 반영하며 등록하지 않은 형제 폴더의 내용을 제외했다. 별도 호출자 파일의 해시, JSONL과 Spring 형태 text 로그, SQLite 출처, 등록 실패 시 이전 설정 보존, 등록 UI 저장·재시작·조사를 확인했다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_multi_repository.py
# 16 passed, 5.49 seconds

.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
# 515 passed, 123.32 seconds
```

실행 파일: `output/multi-root/new-final.xml`, `output/multi-root/full-final.xml`, `full-final.log`. 전용 신규 검사는 16개이며 전체 검사에 포함된다. 기존 499개를 유지했다. 첫 임시 폴더 상위 경로 부재와 개발 중 누락 인자/스키마 검사 실패는 최종 결과와 구분했다. 새 외부 모델/OCR 호출·실제 Agolive 변경·원격 실행·클라우드 배포는 0회다. 실모델의 조사 품질·실사건 해결·일반 프로젝트 수정은 이번 통과의 의미가 아니다.

## 남은 연결

클라우드 로그인/프로젝트와 로컬 실행기 페어링, 인증된 외부 연결, 작업 전달·중복/만료·취소, 읽기 전용 원격 도구, 클라우드 모델 조사 루프가 필요하다. 현재 Streamlit을 클라우드에 올려 PC의 경로를 입력하는 것으로는 로컬 프로젝트에 접근할 수 없다. [역할과 구현 순서](../guides/cloud-and-local.md)를 기준으로 진행한다. 일반 프로젝트 사본 수정·고정 검사 실행·원본 적용·배포는 별도 권한/격리 단계다.
