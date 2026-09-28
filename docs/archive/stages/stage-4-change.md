# 한 가지 격리 수정·검증

> 과거 기록입니다. 당시 계획·검사·제약을 보존하며, 현재 상태는 [구현 상태](../../current-state.md)를 기준으로 확인하세요.

기준: 2026-09-28. 대회용 계획 4단계의 **등록된 씨드 개발 프로젝트 한 가지에 대한 A2 후보 준비**를 구현하고 실제 Nemotron 제안 → 파일 변경 → 동일 검사·회귀 통과를 확보했다. 완료 상태는 `CHANGE_PREPARED / WAITING_REVIEW`다. 실제 Agolive 장애 해결, 원본 적용, 병합·배포·운영 DB 변경·외부 알림과 비교 평가·제출 작업은 수행하지 않았다.

작업 시작 HEAD는 `34ce50b0cf6f214726e64a3fbee1f4195c397d4e`였다. 기존 날짜 보완 변경 6개를 보존했다. `report_agent.py`, `report_contract.py`, `test_message_dates.py`, `stage-3-memory.md`는 시작 시 내용과 동일하다. 기존 `incident_memory.py`와 `current-state.md`의 변경을 유지하며 이번 최소 저장 확장·현재 상태만 추가했다.

## 대상과 출처

[씨드 프로젝트](../../../examples/seed_signup/README.md)는 `SEEDED_DEVELOPMENT`, 프로젝트 `tracebridge-seed-signup`, 대상 `seed-signup-client`다. `client.py`의 요청 생성기는 `user_id`를 보내며 신뢰 가능한 인프로세스 씨드 API 계약은 `userId`를 요구한다. 반환 응답값 422와 필드 불일치가 기존 `triage_report → analyze/route_verdict`에서 `EXACT_ID / WORK_CANDIDATE`가 된다. 입력·코드·검사 결과·작업 기록에 씨드 표시가 있다. TCP 서비스나 Agolive 운영 요청을 재현한 것이 아니다.

| 버전/파일 | SHA-256 |
| --- | --- |
| 기준 `seed-signup-v1` 파일 snapshot | `3c11e6e23eec7ab7a91fd4322f68e055b20493b91323e5ff605644a5916c0e4e` |
| 기준 `client.py` | `0ea85e39cd33018c7bb32dd95ab85dd123bec11392fc495110e31a0835bca6ec` |
| 실제 모델 후보 snapshot | `9cef9bdd61fd50507cc9f9bf8302d733edf2f7dbde084b3a05ffd34a444ef583` |
| 후보 `client.py` | `2907a1bf151a12c09cfddc6e9381e513406a9712429048334c0a70512008b614` |

기준은 Git 커밋으로 가장하지 않은 **등록 파일 snapshot**이다. 정책에 출처와 파일별 해시가 있다. 작업마다 `output/changes/<work_id>/candidate`에 등록된 6개 파일만 복사한다. 원본 저장소 전체를 탐색·복사하지 않으며 `.env`, 등록 밖 파일·운영 자격 증명은 복사/모델 전송하지 않는다. LF 규칙은 [.gitattributes](../../../.gitattributes)에 한정 등록했다.

## 정책과 실제 실행 보장

[정책](../../../examples/change_policy/seed-signup-a2-v1.json) `seed-signup-a2-v1`, 버전 1, SHA-256 `e769a33c3b14ad798951bdc2c53746b018c47ca0dbbc239964f2421024f88635`를 [프로그램 등록부](../../../tracebridge/change_policy.py)가 해시로 고정한다. 제보·사진·과거 카드·모델은 정책 객체, 대상 경로, 실행 설정을 바꾸는 인터페이스를 갖지 않는다. `WORK_CANDIDATE`나 “고쳐줘”만으로 실행되지 않는다. 저장된 최신 출처 실행과 프로젝트·dev 범위·계약 진단·정확 상관·관측 완전성·씨드 snapshot을 정책과 검사한다. Agolive/미등록 정책/보류/충돌 사건은 작업 전에 거절한다.

| 제한 | 등록 값·실제 검사 |
| --- | --- |
| 행동 | `A2 PREPARE_CHANGE`; 원본 적용·배포 실행 경로 없음 |
| 편집 | `client.py` 1개, `build_signup_request`의 반환 사전 키 문자열 2곳 이내 |
| 크기 | 삭제+추가 합산 6줄, 편집 리터럴 합산 256 bytes, diff 4096 bytes 이하 |
| 제안 | `old_key/new_key`, 기준 파일 해시, 현재 출처 근거 ID, 정책/대상/검사 ID. 추가 필드 거절 |
| 파일 검증 | 경로 이탈·절대/드라이브/ADS 경로·링크·junction/reparse point·hardlink, 원본/검사 파일 해시 검사 |
| 실행 코드 | 프로그램이 키 이름을 JSON 문자열 리터럴로 변환하고 전체 AST의 나머지가 동일한지 검사. 값·표현식·import·문장 변경과 중복 키 거절 |
| 명령 | `signup-contract`, `signup-regression`만 고정 argv로 실행. 모델의 쉘·SQL·URL·검사 변경은 실행하지 않음 |
| 시간/시도 | 검사마다 고정 10초, 모델 최대 45초, 작업 예산 90초, 출처 실행당 제안 1회, HTTP 자동 재시도 0회 |
| 자식 환경 | `SystemRoot/WINDIR`, 작업별 `TEMP/TMP`만 전달. API 키·`PYTHONPATH`·기존 샘플 수정 환경 변수 제외 |

**OS 수준 격리는 없다.** 별도 사본, Python `-I -B`, 허용 목록 환경, 시간 제한과 검증 가능한 리터럴 편집이 실제 범위다. 네트워크·CPU·메모리·파일시스템 접근을 OS 정책으로 막았다고 주장하지 않는다. 그래서 해시로 등록한 신뢰 가능한 작은 씨드 코드와 검사만 실행한다. 임의 프로젝트·모델이 작성한 새 표현식을 실행하는 기능은 제공하지 않는다. 로컬 파일 소유자를 신뢰하며 동시 공격자의 파일 교체 경쟁을 방어하는 경계는 아니다. OpenShell 연결·정책·실행을 검증하지 않았고 모든 실행에 `openshell_verified=false`, `os_sandbox=false`를 기록한다.

HTTP timeout은 통신 단계별 제한이고 파일 I/O·프로세스 시작/정리까지 강제하는 실시간 상한은 아니다. 잔여 작업 예산이 고정 검사 시간보다 작으면 새 검사를 시작하지 않는다. 기준·후보의 입력/argv/환경 설정 해시/timeout은 동일해야 한다. `tracebridge/repro.py`의 환경 변수 기반 샘플 red/green은 이번 완료 증거로 사용하지 않았다.

## 실제 수정과 같은 검사

실제 후보 `output/changes/241fe70fb7bc46088407339f5bceffe2/candidate/client.py`에 다음 diff가 있다. 원본 `examples/seed_signup/client.py`에는 씨드 결함을 그대로 보존했다.

```diff
-    return {"user_id": user_id, "displayName": display_name}
+    return {"userId": user_id, "displayName": display_name}
```

| 단계 | 고정 명령 ID와 argv | 실제 결과 |
| --- | --- | --- |
| 수정 전 | `signup-contract`: Python `-I -B checks.py contract` | 종료 1, 422, `seed-user_id-vs-userId` 실패 signature, 78ms |
| 후보 적용 후 | 같은 `signup-contract`, 같은 입력/설정 | 종료 0, 201, `userId` 관측, 78ms |
| 회귀 | `signup-regression`: Python `-I -B checks.py regression` | 종료 0, 서로 다른 ID/표시 이름 보존과 빈 ID의 422 등 3개 통과, 78ms |

검사 입력은 등록된 `cases.json`, ID `signup-cases-v1`, SHA-256 `301d837e358e9b0cf79cd1c0b834e414af5f9372e8aa5933f9b5f535cdd6d6dc`다. 관련 검사의 설정 해시는 전후 모두 `16aeeebe9c97c0462efec14e96cd5489e263680b06ab3063f526e08c75611a70`이었다. diff는 1파일·삭제/추가 합산 2줄·편집 리터럴 합산 17 bytes다. diff SHA-256은 `c81371ffaf33c48b368bbbe0c9578f099cc3108549c2da13fee7d7e810bcaedf`다.

## 실제 모델과 실패 보존

기존 NVIDIA 연결과 `nvidia/nemotron-3-super-120b-a12b`를 재사용했다. 등록된 합성 코드 2개, 계약/입력, 현재 출처 근거와 수정 전 실패만 전송한다. 제보 원문·사진·과거 카드·자격 증명·다른 저장소 코드는 전달하지 않는다. 강제된 `propose_change` 구조화 응답을 받고 프로그램이 검증한다. 운영 경로에 고정 성공 패치나 가짜 모델 fallback이 없다. 테스트의 주입 제안자는 항상 `TEST_DOUBLE`, 실제 모델 호출 0회로 기록한다.

이 작업의 외부 호출은 총 **2회**, 서로 다른 씨드 출처 실행에 1회씩이었다. 첫 실행은 응답을 받았으나 인용한 리터럴 형식이 편집 계약과 맞지 않아 `POLICY_REJECTED`로 보존했다. 키 이름을 제안하고 프로그램이 문자열을 만드는 계약으로 명확히 한 뒤 신규 씨드 사건에서 한 번만 확인해 성공했다. 같은 작업의 자동 재시도는 하지 않았다.

| 실제 호출 | 증거 |
| --- | --- |
| 첫 거절 | work `66f00275afd84d048035a4ce4692892e`, response `chatcmpl-287c4ee5-99db-4359-a25d-6b894f6ee49b`; 수정 전 실패만, diff 없음, 후보 검증 false, 원본 보존 |
| 후보 성공 | work `241fe70fb7bc46088407339f5bceffe2`, response `chatcmpl-1799371f-ae9d-42fb-9b5d-1dda25adfbae`; 모델 3437ms, 입력/출력 2315/212 tokens, 작업 3750ms |

위 시간은 한 로컬 실행의 관측이며 비교 평가·효율 우위를 뜻하지 않는다. 실패 기록 `output/changes/66f00275afd84d048035a4ce4692892e/result.json`, 성공 기록 `output/changes/241fe70fb7bc46088407339f5bceffe2/result.json`에 응답 ID·응답/제안/context 해시·호출 횟수와 결과가 있다. 성공 폴더의 `proposal.json`, `candidate.diff`, `before/after/regression.json` 및 stdout/stderr 파일을 검토할 수 있다. 실패 응답의 원문은 보관하지 않고 해시와 거절 이유를 남겼다. 로컬 산출물·DB는 Git 제외이며 새 checkout에서는 아래 명령으로 다시 생성한다.

## 사건 기록과 검토

최소 `change_jobs` 테이블 1개를 추가하고 SQLite schema를 1→2로 확장한다. 기존 사건·실행·카드·FTS와 검토 이력을 유지한다. 작업은 원 사건/출처 run과 새 결과 run을 외래 키로 연결하며 작업과 새 실행·PENDING 카드를 한 트랜잭션으로 저장한다. 검토된 이전 카드·실행을 다시 쓰지 않는다. 같은 출처의 재호출은 저장된 작업을 반환해 검사/모델을 재실행하지 않는다.

실제 DB `output/validation/stage4-nemotron-20260928/state.sqlite3`의 성공 연결:

- 사건: `8e2279db57a64f09babcde7f5d1e357c`
- 원 접수 실행: `c1a27855a2514792ba28989ae1eb80f3`
- 수정 준비 실행/새 카드: `409088b07ddd4e90a99de300970ff2a1`
- 작업: `241fe70fb7bc46088407339f5bceffe2`, 카드 `PENDING`, 작업 `WAITING_REVIEW`

baseline/candidate 버전·파일 해시, 출처 최소 기록 해시, 정책 버전/해시/판정, 작업자 코드 해시, diff 참조/해시, 명령 ID/입력/argv/종료 코드/시각/시간/결과/출력 참조와 한계를 남긴다. SQLite에는 원본 코드·모델 제안·stdout 본문을 추가하지 않는다. 원 접수 관측은 출처 실행에서 조회하며 수정 실행에 새 현재 관측으로 복제하지 않는다.

`candidate_fix_verified=true`는 `REGISTERED_SEED_COPY_ONLY`다. 원 실행/카드의 `cause_confirmed/fix_applied/fix_verified`는 false를 유지하며 `original_applied=false`, 배포 `NOT_ATTEMPTED`, 서비스 회복 `NOT_VERIFIED`다. 새 카드도 기존 `approve/edit/reject` 검토를 거쳐야 검색되며 승인이 원본 적용·배포 권한이 되지 않는다. 실제 DB 재연결에서 두 작업/각 출처·결과 연결, 새 카드 PENDING, 외래 키 위반 0개를 확인했다.

저장 실패는 작업 상태와 분리한 `persistence=FAILED`다. 이미 확보한 JSON의 저장만 재시도하며 실행을 다시 하지 않는다. SQL 실패에는 새 실행·카드·최신 사건 투영을 함께 롤백한다. 이 경로도 인증 없는 **로컬 내부 CLI**다. `--project`는 범위 확인이며 공개 서비스 인가를 대신하지 않는다.

## 실행·조회·저장만 재시도

workspace에서 기존 Python 3.12 환경을 사용한다. 아래 `--live`는 등록된 합성 자료의 NVIDIA 전송을 선택한다. 키가 없거나 서비스가 실패하면 모델 미검증으로 기록한다. `--live`를 생략하면 수정 전 재현만 수행하고 `MODEL_NOT_REQUESTED`가 되며 성공 패치를 만들지 않는다.

```powershell
$stage4Db = 'output/validation/stage4-local/state.sqlite3'
$source = .\.venv\Scripts\python.exe -m scripts.prepare_change --db $stage4Db seed | ConvertFrom-Json
$prepared = .\.venv\Scripts\python.exe -m scripts.prepare_change --db $stage4Db prepare --source-run $source.run_id --live | ConvertFrom-Json
.\.venv\Scripts\python.exe -m scripts.prepare_change --db $stage4Db show $prepared.job.work_id
.\.venv\Scripts\python.exe -m scripts.prepare_change --db $stage4Db list --incident $source.incident_id
Get-Content -LiteralPath $prepared.job.diff.ref

# 별도 재조사/재실행 없이 얻은 결과 저장만 재시도
.\.venv\Scripts\python.exe -m scripts.prepare_change --db $stage4Db save --file $prepared.job.artifact_ref

# 기존 검토 절차; 검토자 문자열은 인증 신원이 아님
.\.venv\Scripts\python.exe -m scripts.incident_memory --db $stage4Db --project tracebridge-seed-signup card $prepared.job.result_run_id
```

실제 확보한 결과 조회에는 `--db output/validation/stage4-nemotron-20260928/state.sqlite3 show 241fe70fb7bc46088407339f5bceffe2`를 사용한다. 새 시도에는 새 `seed` 접수가 필요하다. 기존 실패를 덮어쓰거나 같은 출처 작업을 자동 재실행하지 않는다.

## 회귀와 변경 파일

기존 170개를 먼저 정상 권한에서 재확인했다(29.34초). [신규 검사](../../../tests/test_prepare_change.py)는 실제 사본 실행의 전후·회귀, 이미 통과/다른 실패/환경 오류, 정책 없음·실프로젝트·보류·충돌, 금지 경로/검사/명령/정책/근거/해시/크기, 링크·원본 불변, 관련·회귀 실패, 세 단계의 실제 프로세스 timeout/부분 stdout, 모델 실패/미연결, 비밀 파일 제외, 검사 후 무결성 실패, DB 저장 실패/저장만 재시도/원자성/schema 이행과 검토 보존, 내부 CLI를 검사한다. 이 테스트의 모델/HTTP 제안은 가짜이며 외부 호출을 하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_prepare_change.py
git diff --check
```

최종 코드로 **기존 170개 + 신규 57개 = 전체 227개 통과**를 확인했다(44.69초). 실행 명령은 위 전체 검사에 `--basetemp tmp/pytest-stage4-all-20260928 --tb=short`를 더한 것이며 검사 범위·기대값은 같다. `git diff --check`도 통과했다. 제한 Windows 실행에서 기존 테스트의 임시 폴더 접근 오류가 발생해 정상 권한으로 다시 확인했다. 테스트 기대값을 완화하지 않았다. 정식 범위는 `tests/`이고 기존 `generated/` 샘플 재현은 포함하지 않는다.

| 역할 | 이번 변경 |
| --- | --- |
| 정책·제안·작업 | `tracebridge/change_policy.py`, `change_proposal.py`, `change_worker.py` |
| 최소 기록 확장 | `tracebridge/incident_memory.py` |
| 내부 실행·조회 | `scripts/prepare_change.py` |
| 등록된 입력/원본/검사 | `examples/seed_signup/*`, `examples/change_policy/seed-signup-a2-v1.json`, `.gitattributes` |
| 회귀 | `tests/test_prepare_change.py` |
| 사용법·현재 상태 | 루트 README와 이 문서, 문서 지도·현재 상태·대회 MVP·정책·구조·자료 모델·NVIDIA 기록 |

범용 패치/임의 프로젝트의 실행 격리, 실제 Agolive 로그·배포 연결과 실사건 검증, 원본 적용·배포·회복은 남아 있다. 이 단계 보고로 종료하며 비교 평가·제출 준비는 시작하지 않는다.
