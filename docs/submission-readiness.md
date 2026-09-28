# TraceBridge 제출 준비 확인표

기준: 2026-09-28, 세션 D. **초안 / 외부 제출 전**. 공개 공식 자료를 다시 읽었으며 참가자 전용 조건과 제품 실행 증거를 별도로 기록한다.

| 항목 | 이번 확인 | 남은 조건 |
| --- | --- | --- |
| 온라인 예선 기간 | [공식 예선 이미지](https://cdn.day1company.io/prod/uploads/202609/153942-1931/%E1%84%80%E1%85%A2%E1%84%8B%E1%85%AD-01.webp)의 9월 11~28일 | 정확한 마감 시각·연장 여부를 최신 신청/미션 공지에서 확인 |
| Skill API 미션 | 위 이미지가 Build NVIDIA Skill API 활용 데모를 요구 | 인정 endpoint/스킬 형식·필수 호출·증빙 방식 미확인. NIM 자동 인정 주장 금지 |
| 교육 미션 | 별도 참가자 미션 안내를 확보하지 못함 | 개인별 강의·실습·수료·업로드 증빙 및 적용 전형 미확인 |
| 신청서 필수 항목 | [공식 안내](https://fastcampus.co.kr/NVIDIA_hackathon)에서 [신청 폼](https://docs.google.com/forms/d/e/1FAIpQLScyZ5GYYaCOycNUzXVUTenliEUmSEIdXelVdYphvMvLeLuiHA/viewform) 링크 확인 | Google 로그인으로 이동해 폼 내부 미확인. 팀명·개인 정보·팀원별 제출·파일 수/크기/형식 추측 금지 |
| 기존 문안의 마감/업로드 안내 | 23:59 KST·파일 1개·파일명 형식은 기존 초안 기록 | 이번 공식 폼에서 재확인하지 못했으므로 확정 조건으로 전재하지 않음 |
| 트랙의 NeMo 요건 | 사용자 제공 이미지·기존 문서가 Framework 또는 Microservices를 요구 | 주최 측의 온라인 예선 적용/인정 범위 확인과 구분 |
| NeMo Retriever OCR 사용 | [공식 제품 설명](https://docs.nvidia.com/nim/ingestion/image-ocr/latest/overview.html)의 microservice 분류. [과거 실제 실행 기록](nvidia-validation.md) 보존 | 새 호출 아님. 교육/Skill API 인정·Framework 전체 설치의 증거로 확대하지 않음 |
| NAT | [공식 NAT 개요](https://docs.nvidia.com/nemo/agent-toolkit/latest/index.html)가 기존 도구/에이전트 계측 기능을 안내 | 설치·fixture 성공과 주 조사 하위 단계 연결 성공을 구분. 실제 NAT 종단 미검증 |

공개 페이지의 상세 미션은 이미지로 제공된다. 신청 폼 조회는 인증 페이지로 넘어갔고 자동 승인 검토가 `accounts.google.com`의 비공개 세션 경계 접근을 거부했다. 우회·로그인·입력·제출을 수행하지 않았다. 참가자가 가진 최신 안내의 조건을 받아 이 표를 갱신해야 한다.

## 실행 증거와 최종 게이트

현재 제품의 개발 씨드 실행·후보 diff·동일 검사·기록과 과거 OCR 성공은 [최종 단계 전 점검](pre-final-readiness.md)에 있다. 그 기록을 이번 세션의 새 성공으로 집계하지 않는다. 일반 조사에는 호스팅 모델 실패 기록이 있고 실제 사건·기억 효과·최종 새 환경 재현은 미완료다.

- D 산출물은 12건 합성 평가 자료, 현재 API의 로컬/doubles 러너, 실패·미관측을 보존하는 결과, 의존성 lock, 초안 ZIP, 제출 문안과 시연 순서다.
- A/B/C 편집 종료 후 메인이 코드를 고정하고 주 조사 연결·전체 회귀·새 환경 CLI/UI/재시작·최종 ZIP을 확인한다.
- 사용자의 실검증 재개 후 실제 모델 종단 반복·실사건·NAT 주 흐름·동일 조건 비교를 수행한다. 지금 원본 적용·배포·외부 제출은 하지 않는다.
- 정확도·시간·토큰·기억 효과 우위는 미측정이며 신청서에 쓰지 않는다.

## D 준비 결과와 지원 환경

- Windows amd64 / CPython 3.12.7에서 실제 설치된 dependency metadata를 읽어 `requirements-windows-py312.lock` 169개, test lock 6개, build lock 1개를 고정했다. `pyproject.toml`의 직접 의존성과 setuptools도 실제 버전으로 고정했다. 기존 `.venv`에 설치/업그레이드를 하지 않았고 `pip check`가 통과했다. wheel 해시·다른 OS/Python·새 환경 설치 성공은 미확인이다.
- 내장 평가의 실행 범위는 진단이며, 답변만 하는 단일 프롬프트 및 사람과 일반 코딩 에이전트의 수정 작업은 별도 관측을 가져온다. 시간은 수행 범위·원점별로 분리한다. 12건의 후보 수정/회귀/사람 시간 측정은 미실행이다.
- NAT 1.8의 실제 workflow runner에서 같은 `investigate_submission`을 감싸 로컬 메타데이터 전달을 확인했다. 도구 2건, 모델 0회, `WAITING_CONTEXT`, 실행 ID/종료 상태/호출 수 일치다. 자료 없는 요청을 보류한 경로이며, 실제 모델·실사건을 조사한 성공으로 확대하지 않는다.
- 초안 ZIP은 `output/parallel-d/packages`에만 만든다. 내부 `DRAFT_NOTICE.md`, 파일 해시 manifest, 합성 매뉴얼/평가/과거 OCR 메타데이터를 포함한다. DB와 기존 실행 결과는 포함하지 않는다.

코드 고정 뒤 새 환경에서 수행할 명령(이번에 설치하지 않음):

```powershell
python -m pip install -r requirements.txt
python -m pip install --no-deps --no-build-isolation -e .
python -m scripts.evaluate_incidents --mode local
python -m scripts.run_nat contract-001
python -m streamlit run app.py
```

CLI/UI/재시작·새 DB·오프라인 재생을 확인한 뒤 메인이 최종 ZIP을 만든다. `scripts.run_nat`는 기본 오프라인 fixture 경로이고, `--main-report`로 선택 NAT 래퍼를 사용한다. 실제 외부 모델 호출은 명시적인 실검증 재개 후 `--live`에서만 수행한다. 기존 README/current-state/문서 지도와 기존 검사 수정은 메인 소유로 인계한다.
