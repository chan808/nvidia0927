# 실검증을 위한 로컬 준비와 실행 절차

기준: 2026-09-28, A~D 병렬 작업 중 메인 세션의 기반 작업이다. **통합 완료나 실제 운영 사건 검증을 뜻하지 않는다.** B/C는 인계 문서상 소유 작업 완료이며, A/D는 이 점검 시작 시 진행 중이었다. 단계별 완료 판단은 각 인계와 편집 종료 후 전체 회귀로 확정한다.

후속 [사전 통합 점검](preintegration-checks.md)에서 고정 사본의 전체 사전 회귀·기억 검토/매뉴얼/재시작을 진행하고 C의 저장 재시도 결함을 국소 수정했다. 소스 고정 도구는 이제 테스트·문서·스킬·평가 이미지도 포함한다. 아래 116개 사본과 10개 검사 수치는 처음 기반 작업 당시의 기록이다.

## 지금 사용할 최소 도구

```powershell
.\.venv\Scripts\python.exe -B -m scripts.prepare_verification --smoke
```

도구는 다음을 수행한다.

1. Python·필수 패키지 버전과 NVIDIA 키의 설정 유무만 기록한다. 키 값은 출력·복사하지 않으며 인증 성공을 판단하지 않는다.
2. 현재 미커밋·미추적 런타임 소스도 `output/main-foundation/<실행>/snapshot`에 보존한다. `.env`, 기존 DB, 생성/임시 파일은 제외한다.
3. 복사 전후의 소스 집합·해시를 비교한다. 복사 중 변경이 있으면 `SOURCE_CHANGED`로 중단한다. 기존 사본을 덮어쓰거나 삭제하지 않는다.
4. 고정 사본의 공개 합성 `ledger-demo` 프로젝트를 독립 DB 두 개로 조사한다. 기억 끔/켬을 각각 실행하며 새 모델/OCR 호출을 하지 않는다.
5. 제보 500·실제 422의 분리, 호출자 결함 후보, SQLite의 해당 사건/실행 저장, 기억 모드의 저장 유지, 모델 0회, 원본 수정 미주장을 검사한다.

자식 CLI에 제공자 키·프로젝트 환경 변수·프록시 설정을 전달하지 않는다. Python audit hook으로 연결·DNS·bind를 막는다. **이것은 로컬 검사 보조 장치이고 OS/OpenShell 격리 검증이 아니다.** 신뢰 가능한 등록 씨드의 고정 검사 외에 임의 코드를 실행하는 데 사용하지 않는다.

`--smoke`를 빼면 소스 고정과 환경 기록만 한다. 환경 패키지가 표시되거나 키가 존재한다고 실서비스가 준비됐다는 뜻은 아니다.

| 상태 | 의미 |
| --- | --- |
| `FROZEN` | 소스 복사 시 일관성 확인. 서비스 동작 검사는 아직 안 함 |
| `SOURCE_CHANGED` | 복사 중 편집 감지. 새 실행으로 사본을 다시 만들 것 |
| `OFFLINE_SMOKE_PASSED` | 아래 합성 CLI 두 경로만 통과 |
| `OFFLINE_SMOKE_FAILED` | 개별 checks·stderr 파일을 확인. 제품 오류와 도구/환경 오류를 구분 |
| `BLOCKED` | 사본/환경 준비 실패. 기존 산출물은 보존 |

전체 파일 해시는 각 실행의 `foundation.json`에 있고, stdout은 요약이다. 검사 결과 JSON·SQLite·stderr는 그 사본의 `output/main-smoke`에 남는다. 이 경로는 다른 세션의 DB·ZIP·포트와 겹치지 않는다.

## 이번에 확인한 결과

고정 사본: `output/main-foundation/20260928T141654Z-7596e40b/snapshot`

소스 116개, 복사 중 변경 없음. SHA-256 manifest 식별자는 `ac0273b3610b9ca5f0052b78618da97db08d37d2f4f1bc754be82ef8959cb77a`다. 이후 다른 세션이 변경한 현재 작업 트리와 동일하다고 가정하지 않는다.

| 실행 | 결과 | 검증 범위 |
| --- | --- | --- |
| `ledger-demo`, 기억 끔 | `WORK_CANDIDATE / COMPLETED`, 모델 0회 | 500 제보와 422 관측 분리, 현재 호출자 근거, `DISABLED`, 사건 저장·DB 행 확인 |
| `ledger-demo`, 기억 켬 | `WORK_CANDIDATE / COMPLETED`, 모델 0회 | 빈 초기 DB의 검색 경로와 사건 저장·DB 행 확인. 기억의 효율 효과 검증은 아님 |
| 씨드: “방금 가입이 안 돼요. 조사만 해줘.” | `CONTEXT_CANDIDATE / INVESTIGATE / WAITING_CONTEXT` | ID 없이 현재 동작 후보를 찾고 확인 대기. 원인/수정 성공을 자동 확정하지 않음 |
| 같은 사건 복원·동작 확인 | `EXACT_ID / WORK_CANDIDATE`, 모델 0회 | 같은 사건 유지, 후속 답변·명시적 후보 확인·저장, 원본 미적용 |

씨드의 고정 가입 검사는 **사본에서만** 실행했다. 실제 Agolive·운영 사건·실 HTTP 서비스 수정이 아니다. 새 외부 모델/OCR 호출·원본 적용·배포는 0회다.

기반 도구 전용 검사 **10개 통과(0.26초)**. 기록은 `output/main-foundation/pytest-results-final.xml`이다. 처음 pytest는 부모 폴더 미생성과 Windows 임시 폴더 ACL 오류로 중단됐다. 폴더 생성 후 정상 사용자 권한으로 전용 검사를 확인했다. 전체 제품 회귀는 다른 세션이 편집 중이므로 수행하지 않았다.

첫 smoke 도구는 제품의 `persistence.status` 대신 `storage.status`를 확인해 저장을 실패로 표시했다. 실제 사건은 저장돼 있었다. 도구를 정정하고 read-only SQLite 행 대조와 전용 검사를 추가한 뒤 새 사본에서 재실행했다. 첫 기록 `20260928T141559Z-34db910c`는 덮어쓰지 않았다.

## 돌아와서 화면부터 확인하기

먼저 새 사본을 만들고 그 실행의 상태가 `OFFLINE_SMOKE_PASSED`인지 확인한다. 기존 서버를 중단하지 않고 별도 로컬 포트를 사용한다.

```powershell
$verificationPython = (Resolve-Path .\.venv\Scripts\python.exe).Path
$verification = & $verificationPython -B -m scripts.prepare_verification --smoke | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'foundation.json의 실패 상태를 확인하세요.' }
$verificationRoot = $verification.snapshot_directory
$verificationRoot
```

### CLI로 거친 제보·후속 답변 확인

새 사본에서 직접 실행한 고정 씨드 동작을 확인하는 개발 검사다. 아래에서는 의도적으로 모델·수정 준비를 켜지 않는다. 실사건의 첫 후보를 자동 확정하는 절차로 사용하지 않는다.

```powershell
Set-Location -LiteralPath $verificationRoot
$verificationDb = Join-Path $verificationRoot ('output/manual-cli/' + [guid]::NewGuid().ToString('N') + '/incidents.sqlite3')
$roughCase = & $verificationPython -B -m scripts.investigate_report --registered-seed --capture-seed-action --report '방금 가입이 안 돼요. 조사만 해줘.' --db $verificationDb | ConvertFrom-Json
if ($LASTEXITCODE -ne 0 -or $roughCase.candidates.Count -ne 1) { throw '후보/실패 결과를 먼저 확인하세요.' }
$confirmedCase = & $verificationPython -B -m scripts.investigate_report --registered-seed --db $verificationDb --resume $roughCase.incident_id --answer '방금 가입한 동작이 맞아요. 조사만 해줘.' --confirm-candidate $roughCase.candidates[0].trace_id | ConvertFrom-Json
$confirmedCase | Select-Object incident_id, correlation, route, run_status, model_calls, fix_applied
```

`incident_id` 유지, `EXACT_ID / WORK_CANDIDATE`, `model_calls=0`, `fix_applied=False`와 `persistence.status=SAVED`를 확인한다. 두 번째 명령도 종료 코드를 확인하며 오류 때 기존 결과를 성공으로 표시하지 않는다.

### 화면 확인

새 터미널에서 위에 표시된 사본 경로로 이동하고, 같은 Python 절대 경로를 사용한다.

```powershell
# 아래 두 경로는 앞 명령이 출력한 실제 값으로 입력한다.
$verificationPython = 'C:\Users\freetime\Desktop\nvidiatest\.venv\Scripts\python.exe'
$verificationRoot = 'C:\Users\freetime\Desktop\nvidiatest\output\main-foundation\<실행>\snapshot'
Set-Location -LiteralPath $verificationRoot
$env:TRACEBRIDGE_DB_PATH = Join-Path $verificationRoot 'output/main-ui/incidents.sqlite3'
$env:TRACEBRIDGE_SERVICE_PROJECT = 'seed'
& $verificationPython -m streamlit run app.py --server.address=127.0.0.1 --server.port=8514 --server.fileWatcherType=none --browser.gatherUsageStats=false
```

`http://127.0.0.1:8514`에서 외부 분석·자동 수정 준비를 끈 상태로 개발 가입 동작→거친 제보→후보 확인→사건 저장→재시작 후 조회를 확인한다. 이 화면 명령은 준비했으며 이번 메인 작업에서 새 UI 서버를 실행하거나 화면 성공을 확인하지 않았다. 포트가 이미 사용 중이면 비어 있는 포트를 선택한다.

사진의 실제 OCR와 Nemotron 조사/수정 제안은 실검증 재개 후 별도로 수행한다. 사본에 API 키가 복사되지 않으므로 키 설정이 필요하며, 연결 기록과 실패·사용량도 보존한다. 과거 실제 호출 기록을 새 실행으로 표시하지 않는다.

## 실제 프로젝트를 연결할 때 필요한 최소 자료

프로젝트 프로필의 정확한 구조는 [B 인계](parallel/B-handoff.md)와 [프로필 예시](../examples/parallel_b/ledger_demo/profile.json)를 사용한다. 아래 정보를 하나의 개발 사건에 연결한다.

| 자료 | 최소 내용 |
| --- | --- |
| 프로젝트/서비스/환경 | 실제 코드 루트와 검사할 동작 하나 |
| 제보 | 화면·동작·대략적인 발생 시각, 선택 사진. ID는 필수 입력 아님 |
| 현재 로그 | 등록 파일·JSON/JSONL, 요청/서비스/환경/시각·응답·입력의 관측 가능한 타입 |
| API 계약·호출자 근거 | 같은 메서드/경로의 OpenAPI, 필요 DTO·요청 생성 코드·출처 해시 |
| 실행 버전 | 실행 서비스에서 관측한 버전과 출처. 로컬 HEAD를 실행 SHA로 복사하지 않음 |
| 조치 검증 | 허용한 사본/파일 범위와 같은 재현·회귀 명령 |

프로필 등록은 실행 허가가 아니다. 처음에는 `--live`, Docker 조회, 수정 준비 없이 현재 자료 연결만 확인한다. 실제 프로젝트 CLI의 시각·요청값을 위 합성 사례의 값으로 채우지 않는다.

## 통합 전에 남은 점검

- A/D 편집 종료 및 인계 갱신. B가 보고한 기존 합성 검사에는 입력·호출자·버전 근거를 보강할 통합 작업이 남아 있다.
- 고정 통합 코드의 전체 `tests/` 회귀. 이 10개와 다른 세션 수치를 합산해 전체 통과로 대신하지 않는다.
- 실제 모델 도구 선택→반증→조회 변경→최종 반환, 실제 OCR, 정책 허용 수정안·동일 검사·회귀.
- 실제 개발 사건·실행 버전·로그 연결, 기억 켬/끔 비교와 최종 새 환경 설치·제출 묶음 재현.

메인 산출물과 소유 범위는 [기반 작업 인계](parallel/MAIN-foundation.md)에 있다. R1~R6 및 제출 조건의 완료 기준은 기존 [최종 설계](final-completion-plan.md)를 유지한다.
