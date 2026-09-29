# 원본 적용 보고·서비스 확인 검증

2026-09-29 구현. 공개 quota 프로젝트를 테스트마다 새로 만들고 실제 파일 사본 검사·원본 적용과 실제 loopback HTTP를 실행했다. 패치 제안은 명시적인 test double이며 NVIDIA 실호출을 하지 않았다.

최종 전체 **689개 통과(240.36초)**. 실제 PostgreSQL **32개**, 회복 전용 **37개**를 포함한다. 최종 재검토의 중단 복구 보완까지 현재 로컬 웹·API·페어링 실행기에 반영하고 HTTP 200, 실행기 온라인, 새 API의 인증·입력 검증, 기존 작업 4건과 사건·카드·감사 이력·프로필·정책·credential 파일 보존을 확인했다. 서버는 기존 SQLite를 계속 사용하며 daily 서비스는 이전과 같이 기동하지 않았다.

확인한 흐름은 다음과 같다.

1. 다섯 개 입력의 경계 실패 재현 → 사본 패치 → 동일 검사 통과·회귀 통과 → diff 검토 해시로 원본 적용.
2. PC에 저장한 적용 결과를 중앙에 보고. 후보 기록은 변경하지 않고 적용 run을 추가. 응답 유실 후 같은 기록의 재전송은 중복 적용·중복 이력을 만들지 않음.
3. 서버가 적용 코드를 실제로 로드한 snapshot을 고정하여 응답. 신고한 POST와 별도 GET 회귀 동작을 관측 기간에 두 차례 확인하고 `PASSED/RESOLVED` 기록.
4. 같은 버전의 기대 결과 실패를 발생시켜 `FAILED/REOPENED` 기록. 이 결과에서 후속 조사가 최신 run을 이어받음.
5. 구버전·헤더 부족·잘못된 JSON·연결 실패·검사 중 소유자 변경은 `INCONCLUSIVE/OPEN`. 변경된 파일을 보존.
6. 최종 재검토에서 SQLite의 사건 저장 후 중앙 커밋이 중단되면 감사 이벤트가 빠지는 상황을 재현하고 수정. SQLite와 실제 PostgreSQL에서 같은 결과 재전송으로 작업·사건·감사 이벤트를 복구하고, API 검사를 재실행하거나 run/이력을 중복 저장하지 않음을 확인.

프로젝트·실행기 범위, diff/이전·적용 snapshot/정책/파일 목록/후보 ID/사건 revision, 중복 기록 충돌, 정책·프로필 변경, health만 있는 정책, 독립 회귀·read-only 등록, URL 범위, 취소, 일반 조사에서 적용/회복 주장 차단, 변조된 검사 근거를 확인했다. 응답의 별도 private 값이 결과에 포함되지 않음을 검사했다. Streamlit AppTest로 중앙 검사 요청·재확인·재개 표시를 확인했다.

실제 PostgreSQL에서도 적용 보고 → 관측 성공 → 실패 후 재개 → 최신 기록 기반 후속 접수 및 감사 이벤트를 실행했다. 기존 SQLite/중앙 저장·검색 회귀도 함께 확인한다. 최종 검사 수와 소스 해시는 [메타데이터](project-recovery.json)에 기록한다.

재현 명령:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_project_recovery.py -q
# 별도로 준비한 격리 PostgreSQL URL을 TRACEBRIDGE_TEST_DATABASE_URL에 설정한 환경에서:
.\.venv\Scripts\python.exe -m pytest tests/test_postgres_storage.py::test_real_postgres_application_recovery_and_reopen -q
```

증거 범위는 공개 합성 프로젝트와 검사용 HTTP 서비스다. 실제 daily 장애 해결, daily의 원본 적용·서비스 재기동, 브라우저 클릭 여정, 운영 배포/롤백, 지속적인 정상 상태 보장은 검증하지 않았다. 실제 daily의 기존 권한·데이터·PC credential은 유지한다. 코드와 가이드는 [사용법](../guides/project-recovery.md)에 있다.
