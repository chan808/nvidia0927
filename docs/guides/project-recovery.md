# 원본 적용 결과와 서비스 확인

PC에서 검토한 후보를 원본에 적용하면 실행기가 저장된 적용 기록을 서버에 전송한다. 중앙 화면은 **후보 검사**, **원본 적용**, **서비스 확인**, **사건 상태**를 따로 표시한다. 전송 실패 후에는 확보한 기록만 다시 보내며 원본을 다시 적용하지 않는다.

서비스 확인은 후보 준비 전에 소유자가 등록한 API 정책으로 실행한다. 신고한 API의 같은 method·path와 기대 결과, 별도의 회귀 검사, 적용 snapshot에 대응하는 실행 버전, 관측 기간이 필요하다. 헬스 응답만으로 사건을 해결 처리하지 않는다.

| 결과 | 사건 상태 | 판단 |
| --- | --- | --- |
| `PASSED` / `VERIFIED` | `RESOLVED` | 모든 표본의 신고 동작·회귀 기대 결과와 실행 snapshot 일치 |
| `FAILED` | `REOPENED` | 적용 버전에서 하나 이상의 기대 결과 실패 |
| `INCONCLUSIVE` | `OPEN` | 응답·실행 버전 부족/불일치, 제한 초과 또는 검사 중 원본 변경 |

`SUCCEEDED`는 검사 기록을 저장했다는 작업 상태다. 검사 결과가 `FAILED`여도 기록 저장 작업 자체는 `SUCCEEDED`일 수 있다. 원인 확정은 이 검사로 승격하지 않는다.

## 정책 등록

기존 수정 정책 JSON에 다음 `recovery`를 추가하고 `scripts.project_tools policy --file`로 등록한다. 변경한 정책은 새로운 조사·후보부터 사용한다. 이미 준비하거나 적용한 후보의 정책 해시와 다르면 보류한다.

```json
{
  "recovery": {
    "journey_check_id": "journey",
    "regression_check_id": "regression",
    "samples": 3,
    "interval_seconds": 2,
    "timeout_seconds": 2,
    "checks": [
      {
        "id": "journey",
        "method": "POST",
        "url": "http://127.0.0.1:8081/quotas",
        "read_only": true,
        "json_body": {"count": 5},
        "expected_status": 200,
        "expected_json": {"accepted": true}
      },
      {
        "id": "regression",
        "url": "http://127.0.0.1:8081/limits",
        "read_only": true,
        "expected_status": 200,
        "expected_json": {"ok": true}
      }
    ]
  }
}
```

위 endpoint는 공개 검증 예시이며 daily endpoint가 아니다. `read_only: true`는 소유자가 해당 요청의 무변경 동작을 확인했다는 명시 등록이다. GET/POST만 지원하며 계정·업무 데이터 생성/변경, 인증 헤더·쿠키, 임의 셸 명령, 외부 주소는 지원하지 않는다. loopback HTTP 주소와 명시 포트만 허용한다. query·fragment·URL 자격 증명·리다이렉트를 차단한다. 응답 JSON은 16 KB, 검사 2~4개, 표본 2~6회, 간격 1~10초, 요청 제한 1~5초다. 기대값은 최상위 JSON의 문자열·정수·불리언·null을 타입까지 비교한다.

각 응답은 실제 로드한 빌드의 `X-TraceBridge-Snapshot-SHA256`을 반환해야 한다. 정책의 `snapshot_header`로 헤더명을 지정할 수 있다. 적용 기록의 `applied_snapshot_sha256`과 정확히 같아야 한다. Git SHA만 같거나 현재 디스크 값을 요청 시 다시 계산하는 방식은 실행 버전 증거로 사용하지 않는다. 배포/기동 과정에서 해당 snapshot manifest와 실제 로드한 코드의 대응을 서비스가 보장해야 한다.

snapshot은 등록한 수정 저장소의 상대 경로→파일 SHA256 맵을 정렬한 JSON의 SHA256이다. 기존 snapshot 제외 규칙과 파일 제한을 사용한다. 바뀌는 로그·데이터는 수정 저장소 바깥에 두거나 별도 repository로 등록한다. 개발 기동·배포와 헤더 계측은 서비스별 연결 작업이며 자동으로 수행하지 않는다.

## 실행

PC 실행기가 사용한 사건 DB를 지정하여 검토 적용한다. `--diff-sha256`은 중앙 화면에서 확인한 후보 해시다.

```powershell
.\.venv\Scripts\python.exe -m scripts.project_tools --project PROJECT --db output/local-service/runner/runner-state/local-incidents.sqlite3 apply --work-id WORK --diff-sha256 REVIEWED_HASH
```

실행기는 자신의 중앙 후보 결과 파일과 연결되는 적용 기록을 자동 전송한다. 이전 버전의 PC 적용 기록도 같은 후보·run·diff·snapshot·revision을 검증해서 전송한다. 다른 프로젝트/실행기, 변경된 등록, 새 사건 입력, 변조된 후보 또는 충돌 기록은 받아들이지 않으며 PC 기록은 보존한다. 중앙에 없는 로컬 후보는 원격 후보로 자동 등록하지 않는다.

소유자가 서비스에 해당 빌드를 기동한 뒤 중앙 **연결된 프로젝트**의 후보 작업을 열고 **적용 후 등록된 API 동작 확인**을 누른다. 검사 결과 화면에서 같은 적용 버전을 다시 확인할 수 있다. 실패한 사건은 후속 답변으로 같은 사건의 새 조사에 이어진다.

로컬 화면에도 원본 적용 이후 검사 버튼이 있다. CLI로 수행하려면:

```powershell
.\.venv\Scripts\python.exe -m scripts.project_tools --project PROJECT --db PATH_TO_INCIDENTS verify-recovery --work-id WORK
.\.venv\Scripts\python.exe -m scripts.project_tools --project PROJECT snapshot --policy POLICY
```

두 번째 명령은 로컬 소스 해시 조회다. 서비스가 실행 중이거나 그 소스를 로드했다는 확인은 아니다.

API는 `POST /v1/runner/projects/{project}/applications`로 적용 기록을 보고하고, 소유자는 `POST /v1/projects/{project}/recovery-checks`에 후보 작업 ID·최신 source run ID·정책 해시와 `Idempotency-Key`를 보낸다. 주소·입력·기대값은 PC가 게시한 정책에서만 가져온다. 실행기는 디스크의 정책·프로필 해시를 다시 대조한다. 취소와 lease/epoch 검증, 중단 예약, 저장 결과 재전송을 기존 작업 큐와 공유한다.

응답 본문·입력 값·인증 정보는 검사 결과에 저장하지 않는다. 상태 코드, 기대 결과 일치 여부, 실행 snapshot, 관측 시각/단조 시계 간격, 정책 해시를 기록한다. 중앙 PostgreSQL에서는 사건 run과 검사 작업·이력의 커밋을 함께 처리한다. 기존 SQLite 서버는 사건 DB와 제어 DB가 별도 파일이므로 저장된 사건 결과를 재전송하여 확인 응답을 복구한다.

현재 범위는 소유자 전용 로컬 API 확인이다. 실제 브라우저의 클릭 여정, 운영 릴리스·롤백·상시 관측, 실제 사용자 장애 회복은 별도의 연결과 검증이 필요하다. daily의 원본 적용 권한은 꺼진 상태이며 이 작업에서 변경하지 않았다. [검증 기록](../validation/project-recovery.md)을 확인한다.
