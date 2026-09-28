# 세션 D 인계 — 평가·제출 준비·계측

기준: 2026-09-28. **D 소유 구현·전용 검사 완료 / 평가·ZIP·신청 문안은 DRAFT**. D는 서비스·판정·기억·화면·기존 검사·공용 README/current-state/문서 지도를 편집하지 않았다. 전용 산출물·DB·ZIP은 `output/parallel-d` 아래에 있다. 시작 상태·파일 해시는 `output/parallel-d/baseline.json`, 최종 검사/ZIP 정보는 `output/parallel-d/verification.json`에 기록한다.

시작 HEAD는 `34ce50b0cf6f214726e64a3fbee1f4195c397d4e`, 작업 중 관측한 최신 HEAD는 `17003f839d305b1c79ff5b4d4ed1b77cad61812b`다. D는 commit/staging/reset/clean을 실행하지 않았다. 각 평가 manifest가 당시 HEAD·코드/lock 파일 해시를 별도로 보존하며 편집 중 결과를 최종 회귀로 사용하지 않는다.

## 먼저 확인한 제출 조건

- [공식 안내](https://fastcampus.co.kr/NVIDIA_hackathon)와 [공식 예선 이미지](https://cdn.day1company.io/prod/uploads/202609/153942-1931/%E1%84%80%E1%85%A2%E1%84%8B%E1%85%AD-01.webp)를 다시 조회했다. 온라인 예선 기간은 9월 11~28일이며 Build NVIDIA Skill API를 활용한 데모 제출을 안내한다.
- 공개 이미지로 Skill API의 인정 endpoint/형식·필수 호출 증빙·개인별 교육 완료 증빙을 확정할 수 없다. 일반 NIM 호출·로컬 SKILL.md·SkillSpector 검사만으로 자동 인정된다고 적지 않는다.
- [공식 신청 링크](https://docs.google.com/forms/d/e/1FAIpQLScyZ5GYYaCOycNUzXVUTenliEUmSEIdXelVdYphvMvLeLuiHA/viewform)는 로그인으로 넘어가 내부를 읽지 못했다. 자동 승인 검토가 계정 인증/비공개 세션 경계 접근을 거부했다. 기존 문안의 23:59 KST, 파일 수/형식, 팀원별 업로드 항목은 이번 폼에서 재확인되지 않았다.
- NeMo Retriever OCR의 마이크로서비스 분류와 저장소의 **과거** 실제 호출 기록은 기술 증거다. 대회 Skill API 인정 및 교육 수료 증거와 구분한다. 이번 세션의 새 모델/OCR 호출은 0회다.

## 공개 계측 API와 A 연결 상태

`tracebridge.nat_observability.InvestigationObserver(run_id, output_dir, *, origin="main_investigation", nat_sink=None)`를 구현했다. NAT 의존성이 없어도 실행별 `events.jsonl`/`summary.json`을 기록한다. 같은 실행 ID를 재사용하면 거부한다.

```python
observer = InvestigationObserver(run_id, output_dir)
observer.record_tool("find_logs", phase="correlation", status="success", elapsed_ms=elapsed)
observer.record_model(model, phase="investigation", status="failed", elapsed_ms=elapsed,
                      usage=None, error_type="TimeoutError")
summary = observer.finish(run_status, stop_reason=reason,
                          expected_tool_calls=len(steps), expected_model_calls=model_calls)
```

- 결과 본문·로그·코드·프롬프트·도구 인자는 받지 않는다. 짧은 이름·단계·성공/실패·실측 시간·반환된 사용량·오류 종류만 기록한다.
- 실패·사용량 미반환은 호출에 포함하고 토큰은 `None`/미관측으로 보존한다. 미반환 사용량을 0으로 바꾸지 않는다.
- `NATEventSink(manager)`는 NAT 1.8의 `push_intermediate_step`에 메타데이터를 전달할 선택 어댑터다. sink 없음/실패·하위 이벤트 수 불일치를 연결 성공으로 표시하지 않는다. fixture 원점과 주 조사 원점을 구분한다.
- `observe_result(result, output_dir, *, origin=...)`는 이미 반환된 결과의 요약만 기록하는 fallback이다. 하위 도구/모델 이벤트가 계측됐다는 증거로 사용하지 않는다.

현재 A의 `investigate_submission(..., memory_enabled=True, observer=None, observer_output_dir=None)`가 공개 API를 소비한다. 모델 usage와 실패·도구 단계·실측 시간을 이벤트로 보내고 `finish`의 기대 호출 수와 결과 실행 ID를 연결한다. D 전용 검사에서 기억 끔/켬의 저장 유지·실행 ID·도구/모델 수와 timeout usage 미반환을 확인했다. A 소유 파일은 D에서 수정하지 않았다.

`tracebridge.nat_plugin.MainInvestigationConfig(name="tracebridge_main_investigation")`는 명시적인 repo/profile과 전용 DB를 사용하는 직접 NAT wrapper다. `repo_path`, `profile_path`, `output_dir`, `memory_enabled=True`, `use_nvidia=False`를 받는다. `NATEventSink(Context.get().intermediate_step_manager)`를 주 함수에 전달하며 framework를 교체하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m scripts.run_nat contract-001
.\.venv\Scripts\python.exe -m scripts.run_nat --main-report '가입이 안 돼요. 확인해 주세요.' --repo examples/evaluation/project --memory-off
```

첫 명령은 오프라인 fixture이며 NAT workflow/주 흐름 성공으로 세지 않는다. 두 번째는 실제 NAT 1.8 runner→현재 주 함수의 로컬 호출이다. `output/parallel-d/nat/main/e530b2875bb54e17a8c0d51935d850e4/result.json`: `WAITING_CONTEXT`, get_version/search_code 도구 2건, 모델 0회, NAT 완료 메타데이터 2건 전달, ID·호출 수 일치. 별도 `nat-main-proof/3e05e37311c048b68b77617c572c4785` 실행은 HTTP send를 거부하는 guard 아래 같은 로컬 경로를 확인했다. 실제 모델 단계·실사건 종단은 미검증이므로 `main_flow_verified=False`를 유지한다. 모델 payload와 미반환 usage 전달은 doubles 전용 검사로만 확인했다.

## 고정 평가 자료·러너·결과

`examples/evaluation/suite.json`의 `tracebridge-6x2-v2`, SHA-256 `72ab84e0ee3156d725b9889b459a5170e592d61e194c1af462adc5cadf4c3588`를 고정했다. 입력/기대/공통 자원/초기 기억/코드/합성 PNG 등 35개 파일 해시를 검사한다. v1 파일럿 뒤 입력의 중첩 probe 기대 문구를 제거해 oracle 전달을 막았고 **기대값은 낮추거나 바꾸지 않았다**. 최종 비교는 v2다.

고정된 6유형×2변형 12건에 `rules`, `single_prompt`, `coding_agent`, `tracebridge_memory_off`, `tracebridge_memory_on` 조건을 둔다. 모든 사건은 합성/통제 자료이며 실제 장애가 아니다. 주 조사 A, 공통 판정 B, 기억 끔/켬 C의 준비된 공개 API를 소비한다. 기대값은 어댑터에 전달하지 않으며 파일이 바뀌면 실행을 거부한다. 현재 자료·허용 명령·고정 재현/회귀·예산은 `resources.json`에 같다.

공개 API:

```python
load_suite(path=DEFAULT_SUITE) -> dict
evaluate_suite(*, suite_path=DEFAULT_SUITE, output_root=..., conditions=None,
               mode="local", adapters=None, initial_db=None) -> dict
AdapterContext(condition, mode, memory_enabled, db_path, output_dir,
               suite_root, resource_contract)
AdapterResult(result, execution_scope="diagnosis_only", measurements=None,
              provenance="local", artifact_root=None)
ImportedAdapter(path, suite_fingerprint)(material, context) -> AdapterResult
```

`--mode local/doubles`만 지원한다. 단일 프롬프트·일반 코딩 에이전트는 원자료 ref/해시·같은 suite/resource 해시·기억 조건·수행 범위·LIVE/REPLAY/DOUBLE/HUMAN 원점이 붙은 실제 관측을 가져온다. `examples/evaluation/observation-template.json`은 **빈 형식 예시**이며 결과가 아니다. 누락은 BLOCKED, 실패는 FAILED다. 토큰은 호출별 반환값/결측 수를 보존하고, 사람 시간과 수작업은 기록/해시가 없으면 미관측이다. 답변·진단·수정 및 원점별 시간을 섞지 않는다.

현재 내장 어댑터는 `diagnosis_only`다. 12건의 후보 변경은 실행하지 않았다. 조치 집계는 before/after의 같은 command/입력/검사/설정, 수정 전 실패·후 성공·회귀 성공, 증거 파일/해시, diff와 원본 보존을 요구한다. 기록 증거 검사가 실제 작업자의 정책/재현 실행을 대신하지 않는다. 주 수정 작업자는 등록 씨드의 호출자 작업자이므로 이 평가 프로젝트의 실제 수정 비교는 메인에서 같은 권한/검사를 등록한 뒤 수행해야 한다.

```powershell
.\.venv\Scripts\python.exe -m scripts.evaluate_incidents --mode local
.\.venv\Scripts\python.exe -m scripts.evaluate_incidents --mode doubles
.\.venv\Scripts\python.exe -m scripts.evaluate_incidents --mode local --import-observations output/parallel-d/recorded-observations.json
```

마지막 파일은 참가자가 실제 측정한 자료로 채우는 입력이며 지금 생성된 측정 결과가 아니다.

현재 자료·명령·검사·예산과 초기 기억은 조건별로 같은 manifest를 사용하고 실행마다 DB를 복제한다. 기대값은 어댑터에 넘기지 않는다. 편집 중 평가는 코드 snapshot과 함께 **DRAFT**로 남긴다.

초기 DB는 C의 공개 save/review API로 고정 합성 단서를 만들고 SQLite read-only backup으로 매 사건/조건에 새 복제본을 준다. WAL을 포함한 복제/격리 검사를 통과했다. 현재 결과가 다음 평가의 기억에 유입되지 않는다. 기억 끔에서도 사건 저장은 유지한다.

| 조건 | 최종 local | 최종 doubles | 해석 |
| --- | --- | --- | --- |
| rules | 기대 일치 10, 불일치 2 | 동일 | 적응형 검색/서빙 실패 주입을 규칙에서 실행하지 못한 항목을 실패로 보존 |
| single_prompt | 12 BLOCKED | 12 BLOCKED | 새 응답을 만들지 않음; 토큰/사람 시간 미관측 |
| coding_agent | 12 BLOCKED | 12 BLOCKED | 실제 코딩/수작업 측정 없음 |
| TraceBridge 기억 끔 | 기대 일치 10, 불일치 2 | 기대 일치 12; 실행 COMPLETED 11/FAILED 1 | timeout double 1건은 실패 상태와 자료를 보존해 기대된 안전 행동과 일치 |
| TraceBridge 기억 켬 | 동일 | 동일 | 고정 검사 12쌍 동률. 실제 기억 효과·시간 절감 미입증 |

- local: `output/parallel-d/evaluations/20260928T142759-2db71f9067`.
- doubles: `output/parallel-d/evaluations/20260928T142759-e6b257eff3`.
- 각 실행은 12건×5조건=60행, 모두 DRAFT이고 당시 코드/lock 해시 전후 변화는 없었다. CLI exit 1은 실제 실패 상태 또는 기대 불일치를 보존한 결과다. 실패를 성공으로 바꿔 통과시키지 않았다.
- doubles의 TraceBridge 각 조건은 transport double 10회, usage 미반환 10회다. 실제 NIM 호출은 0회이며 미반환 토큰은 None이다. 사람 시간 관측은 없다.
- 실행된 조건의 고정 진실성 검사에서 잘못된 원인/회복 확정은 발견하지 않았다. BLOCKED는 평가하지 않았다. 수정/회복·실사건 정확도·일반 조사 모델 품질의 완료 주장으로 확대하지 않는다.

## 의존성·패키징·제출 문안

- Windows amd64 / CPython 3.12.7. 현재 dependency metadata의 전이 폐쇄를 runtime lock 169개/test lock 6개/build lock 1개로 고정했다. 직접 의존성/추가 사용 중인 PyYAML·pydantic·setuptools 버전도 고정했다. `.venv` 설치/업그레이드는 하지 않았고 `pip check`가 통과했다. wheel 해시·다른 OS/Python·새 환경 설치/종단 재현은 미확인이다.
- `create_package(team_name=None, *, output_dir=None, root=ROOT)`는 DRAFT만 만들고 기본 경로가 `output/parallel-d/packages`다. 팀명은 주어졌을 때만 넣는다. 잠금 파일·합성 평가 PNG/자료·C API로 만든 합성 검토 매뉴얼·필요 소스를 포함한다. `.env`/자격 증명/숨김 시크릿 파일·SQLite magic의 위장 JSON·DB sidecar·캐시·임시 산출물·실행 결과는 제외한다.
- ZIP 내부 DRAFT 안내와 파일 SHA-256 manifest, 생성 중 변경 여부·미완료 새 환경 게이트를 기록한다. 최신 ZIP 및 포함/제외/CRC/manifest 확인은 `output/parallel-d/verification.json`에 있다. 최종 ZIP은 메인 소유다.
- [신청 초안](../../SUBMISSION_DRAFT.md)은 자연어/사진→현재 관측→조회 선택→후보 diff/동일 검사→사건 기억 중심이다. 과거 OCR는 [비식별 메타데이터](../../examples/evaluation/prior-ocr-evidence.json)로 **재사용 기록**임을 표시했고 request ID 미반환을 보존했다. 새 호출 성공으로 재포장하지 않았다.
- 합성 매뉴얼 `examples/evaluation/reviewed-manual.md`은 C의 `export_manual`로 생성한 과거 단서/검증 미완료 예시다. 승인·원인 확인·후보 검증·원본 적용·회복을 분리한다. [제출 준비 확인표](../submission-readiness.md)에 미확인 조건과 2분 시연/새 환경 게이트를 연결했다.

```powershell
.\.venv\Scripts\python.exe -m scripts.package_submission
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_parallel_d_evaluation.py --junitxml=output/parallel-d/pytest-d-results-final.xml
git diff --check
```

## 전용 검사 결과와 파일 범위

최종 D 검사 **26개 통과**. 주요 근거는 고정 세트 변조 거절·oracle 비노출, WAL/실행별 DB 분리, A/C 공개 API의 기억 선택/저장 유지/ID·호출 수, 실패·결측 사용량·기록 없는 사람 시간, 미검증 조치 거부, NAT payload/오류/부분 계측, 위장 DB·시크릿/임시 파일 제외와 실제 설치 버전 대조다. 전체 tests는 실행하지 않았고 A/B/C 검사 수를 합산하지 않았다.

초기 pytest의 기본 임시 폴더는 Windows sandbox의 mode-0700 접근 제한으로 실패했다. D 검사만 전용 정상-mode UUID 폴더를 사용해 해결했으며 당시 실패 기록과 이후 기능 검사 결과를 구분한다. 실행 중 확인한 D 어댑터/검사 오류도 고쳐 재검사했다. `git diff --check` 및 staged diff check는 exit 0(줄 끝 변환 경고만 있었음), 등록 기준 7개 해시는 시작과 같다.

수정/추가 파일: `tracebridge/evaluation.py`, `nat_observability.py`, `nat_plugin.py`, `scripts/evaluate_incidents.py`, `run_nat.py`, `package_submission.py`, `nat_workflow.yml`, `pyproject.toml`, `requirements.txt`, `requirements-*.lock`, `examples/evaluation/**`, `tests/test_parallel_d_evaluation.py`, `SUBMISSION_DRAFT.md`, `docs/submission-readiness.md`, 이 문서.

## 메인 연결점·남은 조건

1. A/B/C 편집 종료 후 코드/의존성을 고정하고 공통 README/current-state/문서 지도·기존 검사 정합성을 확인한다. 기본 NAT CLI가 이제 오프라인이라는 실행법 변경을 문서에 반영한다. 전체 회귀는 메인이 한 번 수행한다.
2. R1의 짧은/약 481 KB/6,000줄 절단 재현은 A 전용 검사와 통합 화면/저장 게이트로 확인한다. D의 합성 partial/conflict 사례로 대체하지 않는다.
3. 실제 프로젝트 핵심 동작·현재 로그/실행 버전·등록 계약/작업 정책을 연결하고, 같은 조건의 후보 재현/회귀 및 사람 수집·검토 시간을 실제로 기록한다. 미재현/회귀 실패 scenario의 실제 작업자 실행은 미완료다.
4. 사용자가 실검증을 재개한 뒤 실제 모델의 조회 변경→최종 반환 반복·서빙 장애·OCR/NAT 하위 모델 이벤트·실사건을 검증한다. 그 전에는 actual NIM/OCR 호출을 추가하지 않는다.
5. 새 환경 설치·CLI/UI·재시작·새 DB/매뉴얼·최종 ZIP 재현, 참가자 전용 Skill API 인정/교육/제출 조건을 확인한다. 기록이 없으면 우위·자가학습·전체 대회 요건 충족 문구를 넣지 않는다.

이번 세션의 새 NIM/OCR 호출 **0회**, 실사건 검증·원본 적용·운영 접속·배포·외부 문의/제출 **0회**다.
