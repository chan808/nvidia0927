# 실제 프로젝트 운영 연결 검증

2026-09-29. [사용 절차](../guides/real-projects.md)와 [원래 개선안](../design/cloud-local-operations.md)을 구분한다. 새 기능은 소유자 전용 로컬/원격 시범이다.

## 구현 범위

- 독립 저장소·서비스별 로그/계약/버전 등록, 명시적 모노레포 Git 루트, 코드 파일 목록·제한된 파일 읽기.
- 소유자 정책의 사본 검사·모델 제안·수정 전후·회귀·diff 검토, 허용한 프로젝트의 검토 후 원본 적용.
- 로그 없는 제보는 별도 허용한 재현 검사로 시작하고 실서비스 사건 상관·원인 확인과 분리.
- HTTP API의 일회용 페어링·범위 제한 credential·DB 큐·임대·모델 단계 캐시·폐기·취소 확인·결과 전송 재시도.
- PC 실행기의 Windows DPAPI credential 보호, 로컬 저널·확보한 결과 재전송, 서버 모델/기억 연결.
- 서버의 소유자 화면, 기존 Caddy 비공개 배포 구성에 연결하는 Compose overlay.

## 실제 daily 연결

소유자가 제공한 `C:/Users/freetime/Desktop/projects/daily`를 읽어 `daily-local`로 등록했다. frontend와 backend 하위 경로의 등록된 Git root는 daily 루트다. 두 저장소의 코드 출처 HEAD는 `c5803d01b678`이며 검사 당시 dirty는 false다.

현재 프론트는 3100, 백엔드는 8081의 개발 프로세스이며 Docker의 daily PostgreSQL/Redis와 구분한다. 프론트·백엔드 health·API 프록시의 GET 응답 200, 백엔드와 프록시의 request ID 제공을 확인했다. 백엔드 콘솔 로그는 파일로 연결하지 않았고 런타임 SHA는 관측하지 않았다. 연결 상태는 `DEGRADED`다. 다른 Agolive 컨테이너를 조회 대상이나 수정 대상으로 사용하지 않았다.

마지막 확인에서 `frontend/next-env.d.ts`의 Next 개발 경로 생성 변경을 관측했다. 이 작업에서 daily 파일 편집을 수행하지 않았으며 해당 변경은 그대로 보존했다. 앞의 코드 출처/dirty 관측과 독립 사본의 원본 동일성 검사는 각 검사 시점의 기록이다.

원본과 node_modules를 독립 사본으로 준비해 기존 검사를 실행했다.

| 검사 | 결과 | 범위 |
| --- | --- | --- |
| `scripts/check-demo-boundary.mjs` | 통과, 32개 파일 | 데모의 운영 API 비접근 경계 |
| Vitest 전체 | 24개 파일·229개 테스트 통과 | 등록된 프론트 회귀 |
| 원본 snapshot 비교 | 동일 | 프론트 코드·설정 미변경 |

결과는 로컬 `output/daily-connection/checks.json` 및 `output/project-checks/7e42448021a1494da1e190e2588f5e07/checks.json`에 있다. 원본 daily의 데이터 쓰기, 로그인, DB 변경, 서버 재기동, 실제 버그 수정은 수행하지 않았다. 현재 재현 검사는 모든 daily 증상을 다루지 않으므로 실제 제보에 해당하는 검사로 정책을 조정한다.

## 실제 NVIDIA 게이트웨이

실자료 유출 없이 공개 작은 경계값 프로젝트에서 HTTP 제보 → 로컬 조회 → 서버 NVIDIA 수정 제안 → PC 사본 검사 경로를 실행했다. 테스트 더블이 아닌 `nvidia/nemotron-3-super-120b-a12b` 호출이다.

| 실행 | 결과 | 기록 |
| --- | --- | --- |
| 첫 호출 | 코드 문자열을 한 줄로 반환, 수정 후 import 검사 실패. 후보 미검증·원본 보존 | `output/daily-connection/nvidia-gateway-validation.json` |
| 줄 배열 형식 변경 후 | NVIDIA 서빙 InternalServerError, 모델 실패 기록·원본 보존 | `output/daily-connection/nvidia-gateway-validation-v2.json` |
| 인라인 도구 스키마 보완 후 | `CHANGE_PREPARED`, 전후·회귀 exit code 1/0/0, 원본 동일 | `output/daily-connection/nvidia-gateway-validation-v3.json` |

최종 성공 실행은 1회 실제 모델 호출, prompt 1083 + completion 240 = 1323 tokens를 기록했다. 최종 상세 기록은 `output/project-repair-live/3167c878b45443e38e1420b19e54e77a/validation.json`이다. 실패 기록을 삭제하거나 성공으로 덮지 않았다. 합성 사례 하나의 성공이며 일반 품질·반복 안정성·실서비스 버그 해결의 증거는 아니다.

## 회귀와 검증 한계

최종 전체 회귀 **547개 통과(100.57초)**를 확인했다. 실제 사본 subprocess 검사와 HTTP 인증·임대·중복 전송·서비스/등록 변경 경계를 포함한다. 테스트 더블 경로와 실제 NVIDIA/실제 프로젝트 검증을 구분해 같은 폴더의 [검증 메타데이터](real-project-operations.json)에 기록했다. 생성된 과거 snapshot을 중복 수집하지 않도록 pytest의 testpaths를 tests로 고정했다.

추가로 `127.0.0.1:8765`의 실제 HTTP API와 DPAPI 페어링된 PC 실행기에 daily 연결 확인 제보를 보내 `SUCCEEDED` 결과를 받았다. 원인·사건 연결은 미확인으로 유지했으며 NVIDIA 호출은 0회였다. 기록은 `output/daily-connection/local-http-validation.json`에 있다. 로컬/연결 프로젝트 화면의 AppTest는 예외 0개로 daily 선택을 확인했고 `output/daily-connection/ui-validation.json`에 기록했다. Compose overlay는 합성 환경의 `docker compose config --quiet`를 통과했다.

공개 도메인/HTTPS, 새 서버의 이미지 build/배포, 실제 Docker 격리의 악성 코드 차단, 원격 사진 처리, 다중 사용자/조직 인증, 자동 배포·회복은 미검증/미구현이다. 소유자 전용 원격 시범과 공개 운영 제품을 구분한다.
