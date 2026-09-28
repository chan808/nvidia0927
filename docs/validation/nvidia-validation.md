# NVIDIA 실제 연동 검증 기록

기준: 2026-09-28. 입력은 합성 오류 화면, `tests/fixtures/agolive_repo`의 작은 테스트 코드, `examples/agolive_error.log`의 합성 로그다. 실제 코드·운영 로그를 외부로 보내 검증한 결과가 아니다.

후속 [접수·판정 통합](../archive/stages/stage-2-integration.md)에서는 아래 네 성공 파일의 필요한 메타데이터를 다시 확인해 재사용했고 외부 호출을 새로 수행하지 않았다. 아래 시간·상태는 통합 전 기록이다. 현재는 로그 시각/서비스/환경과 실행 버전 검사를 강화했으므로 당시 `SUPPORTED_HYPOTHESIS`를 현재 실사건 지지 수준으로 자동 복사하지 않는다. 파일/선택 Docker 범위와 실행 label 조회 코드는 추가했지만 실제 Agolive 연결은 미검증이다.

| 경로 | 실제 결과 | 재현 명령 |
| --- | --- | --- |
| NeMo Retriever OCR NIM | `nvidia/nemotron-ocr-v2` 호스팅 호출 성공. `ROOM_FULL`과 `abc12345` 읽기 확인 | `python -m scripts.smoke_ocr` |
| 사진만 보낸 제보 → Nemotron 조사 | NeMo OCR 이후 Nemotron Super가 코드 검색 → 로그 조회 → 구조화된 결과를 실제 반환. 3회 모델 응답, 2회 읽기 도구, 전체 약 8.4초 | [사진 제보 명령](../archive/stages/multimodal-intake.md) |
| 자연어 증상 → 조사 | Nemotron Super의 첫 검색 실패 후 검색어 변경·로그 조회·한국어 결과 반환 성공. 4회 모델 응답, 약 4.5초. ID 없는 로그를 `LOG_CANDIDATE`로 유지 | `python -m scripts.investigate_report --report '방에 들어가려 하면 계속 실패해요. 확인하고 고칠 방법을 찾아줘.' --repo tests/fixtures/agolive_repo --logs-file examples/agolive_error.log --live` |
| 문구 없는 사진 → 보이는 증상·질문 | OCR에서 문구 없음, Nemotron Omni에서 로딩 표시 해석, Super에서 원인 후보를 만들지 않고 질문 반환. 전체 약 15.8초 | 합성 로딩 사진을 `--image`로 제출. 결과는 `output/validation/visual_report_agent.json` |

현재 사진 제보 성공 실행 시각: `2026-09-27T23:43:00.264100+00:00`. OCR는 1531 ms, 모델 응답은 약 3.3초·0.4초·2.8초였다. 사용량은 입력 6259·출력 409 토큰이다. 이전 Lightning 경로의 성공 관측은 약 19초였다. 이 숫자는 각각 한 번의 성공 실행 관측이며 속도·정확도 보장이 아니다.

초기 자유형 최종 JSON 요청에서 35초 시간 초과가 반복돼, 최종 결과를 강제된 `finish_investigation` 도구 인자로 받도록 수정한 뒤 성공했다. 시간 초과 시에는 확보한 근거와 실패 상태를 유지한다. 데이터베이스 변경·프로젝트 수정·배포는 수행하지 않았다. 실제 로그 플랫폼과 배포 버전 상관, NeMo Agent Toolkit의 새 조사 도구 등록, 사건 기억은 후속 작업이다.

한국어만 있는 입력에서는 Lightning의 시간 초과가 재발했다. 현재 새 조사 경로는 실제 검증한 `nvidia/nemotron-3-super-120b-a12b`를 기본으로 사용한다. 자연어 성공 실행 시각은 `2026-09-27T23:35:40.424347+00:00`, 입력 7431·출력 425 토큰이다. 모델 목록에 있더라도 호출이 거절될 수 있음도 확인했다(일부 Nano ID는 HTTP 404/410). 등록 목록만 보고 연동 성공으로 표시하지 않는다.

Omni의 자유형 응답은 초기 서비스/JSON 처리 오류가 있었고, `describe_screen` 도구 인자로 출력 형식을 제한한 뒤 로딩 사진 해석에 성공했다. 해당 성공 시각은 `2026-09-27T23:40:21.604220+00:00`이며 OCR 734 ms·Omni 6485 ms를 포함한다. 이미지에서 보이는 로딩 상태를 서버 장애 원인으로 확정하지 않았다.

## 4단계 씨드 수정 제안의 실제 호출

2026-09-28에는 [등록 씨드 1개의 격리 수정 준비](../archive/stages/stage-4-change.md)에서 기존 Super 연결을 실제 호출했다. 외부 호출 총 2회다. 첫 응답 `chatcmpl-287c4ee5-99db-4359-a25d-6b894f6ee49b`는 편집 리터럴 형식 위반으로 적용 전 거절해 별도 실패 기록을 보존했다. 제안 계약을 키 이름으로 명확히 한 뒤 새 씨드 사건에서 1회 호출 `chatcmpl-1799371f-ae9d-42fb-9b5d-1dda25adfbae`로 `user_id→userId` 제안을 받아 프로그램 검증 후 실제 후보 파일을 바꿨다. 수정 전 종료 1/422 → 동일 검사 종료 0/201, 회귀 3개 통과, 원본 snapshot 보존과 DB 연결을 확인했다.

성공 기록은 `output/changes/241fe70fb7bc46088407339f5bceffe2/result.json`이다. 모델은 3437ms·입력/출력 2315/212 tokens였고 작업은 3750ms였다. 합성 코드·입력·재현 실패만 전송했으며 결과는 `CHANGE_PREPARED/WAITING_REVIEW`, 씨드 사본 한정이다. 새 OCR/Omni 호출·실제 Agolive 자료 전송·원본 적용·배포·실사건 해결은 수행하지 않았다. 단위 검사의 TEST_DOUBLE/HTTP mock은 이 실제 호출 2회에 포함하지 않는다. OpenShell/OS 실행 격리는 미검증이다.
