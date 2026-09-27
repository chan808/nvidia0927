# TraceBridge

프론트 개발자의 짧은 오류 제보를 실제 요청, API 계약, 백엔드 로그, DB 마이그레이션 이력과 대조합니다. 증거가 있는 두 유형에 한해 격리된 샘플 코드·DB용 재현 테스트를 생성합니다. 실제 서비스나 운영 DB를 변경하지 않습니다.

## 데모가 하는 일

| Trace ID | 실제 증거 | 기대 결과 |
| --- | --- | --- |
| `contract-001` | 제보는 500, 실제 응답은 422. 요청은 `user_id`, 계약은 `userId` | 제보 정정, 계약 불일치, 테스트 실패→샘플 수정 후 통과 |
| `migration-002` | 실제 500, 백엔드 로그는 없는 `phone` 컬럼. DB는 V11, 코드는 V12 기대 | 마이그레이션 불일치, 테스트 실패→격리 DB에 V12 적용 후 통과 |
| `claim-003` | 제보는 500, 실제 응답은 정상적인 400 필드 검증 오류 | 제보 정정, 500 원인이나 테스트를 꾸며내지 않음 |
| `unknown` | 요청 식별 정보 없음 | 추가 정보 요청, 모델 호출 생략 |

에이전트는 **한 trace ID에 해당하는 읽기 전용 도구**만 쓸 수 있습니다. 최종 판정은 결정적 Python 검증이 만들고, NVIDIA 모델은 필요한 도구 선택과 설명에 사용됩니다. 결과에는 도구 호출 기록, 출처, 토큰 사용량과 경과 시간이 표시됩니다.

## Windows 실행

Python 3.12가 필요합니다. 이 컴퓨터의 기본 `python`은 3.9이므로 `py -3.12`를 사용하세요. `uv`가 있으면 다음 명령으로 준비할 수 있습니다.

```powershell
uv venv --python 3.12 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

`.env`의 `NVIDIA_API_KEY`에 본인 키를 넣습니다. **키를 Git에 올리거나 채팅에 붙여 넣지 마세요.** 저장소에는 합성 데이터만 있습니다.

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

브라우저에서 `http://localhost:8501`을 열고 사례를 고른 뒤 **NVIDIA NIM 실제 호출**을 선택합니다. 오프라인 자료 검증은 모델을 호출하지 않으며, 코드·데이터 검증용입니다.

CLI 실행:

```powershell
.\.venv\Scripts\python.exe -m scripts.demo_cli contract-001 --repro
.\.venv\Scripts\python.exe -m scripts.demo_cli migration-002 --repro
.\.venv\Scripts\python.exe -m scripts.demo_cli claim-003
```

제출용 소스 ZIP 만들기(정확한 팀명으로 교체):

```powershell
.\.venv\Scripts\python.exe -m scripts.package_submission "팀명"
```

`submission/`에 한 파일이 만들어집니다. 포함 목록을 명시적으로 제한해 `.env`와 가상 환경을 제외합니다. 폼의 최대 100 MB 제한보다 작은지 출력된 크기를 확인하세요.

GitHub 링크를 제출한다면 폼 요구사항에 맞는 PDF를 만듭니다:

```powershell
.\.venv\Scripts\python.exe -m scripts.create_portfolio_pdf "팀명"
```

`output/pdf/`의 PDF 한 파일을 업로드합니다. 먼저 공개 저장소의 코드가 실제로 올라와 있는지 확인하세요.

NeMo Agent Toolkit 워크플로 실행:

```powershell
.\.venv\Scripts\python.exe -m scripts.run_nat contract-001
```

`nat_workflow.yml`과 `tracebridge/nat_plugin.py`에는 네 개의 읽기 전용 도구와 NVIDIA NIM 엔드포인트가 정의돼 있습니다. 실제 실행에서 trace·계약·백엔드 도구 호출을 확인했습니다. 이 별도 Toolkit 경로는 재현 테스트를 실행하지 않습니다. 테스트는 UI 또는 CLI에서 별도로 실행합니다.

프로젝트용 에이전트 Skill은 `skills/tracebridge-triage/SKILL.md`에 있습니다. NVIDIA SkillSpector 정적 검사 요약은 `SKILL_SCAN_REPORT.md`에 있고, 전체 실행 결과는 `generated/skillspector-report.md`에 저장됩니다. `generated/`는 Git에서 제외됩니다.

검증:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests
```

## 구조

```text
app.py                     Streamlit 데모 화면
tracebridge/agent.py       NVIDIA NIM 도구 호출 루프와 사용량 기록
tracebridge/triage.py      제보 진위·계약·마이그레이션 결정적 판정
tracebridge/evidence.py    허용된 읽기 전용 도구
tracebridge/fixtures.py    합성 사례
tracebridge/demo_app.py    격리된 샘플 프론트 요청 생성기·SQLite 서비스
tracebridge/repro.py       고정 pytest 템플릿과 수정 전후 검증
tracebridge/nat_plugin.py NeMo Agent Toolkit 로컬 도구 등록
nat_workflow.yml           NeMo Agent Toolkit 도구 호출 워크플로
skills/tracebridge-triage/ 프로젝트용 에이전트 조사 스킬
DESIGN.md                  범위, 상태, 비용 한도, 한계 상세 설계
SUBMISSION_DRAFT.md        신청서 문안 및 두 사람 제출 체크리스트
SKILL_SCAN_REPORT.md        NVIDIA SkillSpector 정적 검사 요약
```

## 검증 범위와 한계

- `REPRODUCED`와 수정 후 통과는 샘플 코드·인메모리 SQLite DB에만 해당합니다. 실제 서비스 수정 완료를 뜻하지 않습니다.
- 모델이 출력한 설명과 결정적 판정이 다르면 결정적 판정과 원문 증거를 우선 확인합니다.
- 현재 데이터 어댑터는 합성 fixture입니다. 실서비스 연동에는 trace ID 전파, API 계약 원본, 읽기 전용 로그/마이그레이션 조회, 개인정보 제거가 필요합니다.
- 현재 재현 테스트 실행은 고정 템플릿과 별도 프로세스를 사용하며 OpenShell 보안 샌드박스가 아닙니다.
- SkillSpector는 프로젝트 스킬 파일을 정적 검사합니다. 앱 런타임 보안이나 모델 답변의 정확성을 보증하지 않습니다.
- `Skill API`와 홍보 이미지의 `NeMo Framework 또는 NeMo Microservices` 요건이 온라인 예선에 적용되는 방식은 주최 측 확인 전까지 미확정입니다. NVIDIA NIM 및 NeMo Agent Toolkit을 다른 제품과 혼동해 표기하지 않습니다.
