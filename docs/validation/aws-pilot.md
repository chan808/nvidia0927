# AWS 전용 파일럿 첫 배포

2026-09-30 KST. 별도 EC2에 TraceBridge를 배포하고 공개 DNS·HTTPS까지 연결했다. 실모델 키 등록은 남아 있다.

## 배포 식별자

- 앱 릴리스: dd0a37874d5bf2a25106b96334c4667aa73103f0
- ECR digest: sha256:3e7b29f7958ea3d6455f026c1a31cd2e751a6a97c531ea3c82944eb97ccff680
- [전체 배포 실행](https://github.com/chan808/nvidia0927/actions/runs/36592426910)
- [동일 커밋 회귀](https://github.com/chan808/nvidia0927/actions/runs/36592426163)
- 스택: tracebridge-pilot / 서울 ap-northeast-2
- 새 IP: 43.200.230.130 / 호스트: tracebridge.ckswhd.shop

CloudFormation 리소스 9개 생성이 완료됐다. 이후 수정은 새 GithubRole 한 개의 신뢰 조건이며 리소스 교체가 없었다. 실제 GitHub OIDC API와 AWS 감사 이벤트의 불변 저장소 ID를 신뢰하고 main·배포 브랜치만 허용한다. Agolive 제공자와 역할은 수정하지 않았다.

## 확인한 범위

| 검사 | 결과 |
| --- | --- |
| Linux 새 의존성 설치와 pip check | 통과. 실제 설치된 177개 패키지 버전을 고정 |
| 전체 회귀 | 758개 통과 / 153.37초 / 실패·오류·건너뜀 0, 실제 PostgreSQL 포함 |
| 전체 Compose | 접수·토큰 범위·멱등성·PostgreSQL 재시작 보존 통과 |
| CI의 로컬 HTTPS | 테스트 CA 인증서 검증, 운영자 웹·API 인증 통과 |
| 실제 공인 DNS·HTTPS | tracebridge A → 43.200.230.130; TLS 1.3·호스트명 검증 통과, 인증서 만료 2026-12-29 22:20 KST |
| 공인 운영자 로그인 | 보호된 서버 생성 자격증명으로 HTTPS 응답 200·HTML 확인, 무자격 웹·API는 401 |
| 요청 제한 | 합성 malformed 요청의 429 응답 확인 |
| OIDC → ECR → 전용 SSM | 실제 실행 성공, 이미지 revision·digest 대조 |
| 실제 2 GiB EC2 | 웹 AppTest·API 인증·PostgreSQL·guard HTTP·308 리다이렉트 통과 |
| 컨테이너 상태 | 5개 정상, OOM 없음, restart count 0 |
| 실제 호스트 여유 | 검사 직후 available 1,163 MiB, 디스크 여유 약 22 GiB |
| 기존 ckswhd.shop / www | 인증서 검증 성공, HTTPS 301 / 200 |

실제 서버의 컨테이너 메모리 합계는 검사 직후 약 277 MiB였다. 이 값은 전체 사진·모델·고부하 경로의 최대 사용량을 증명하지 않는다. web/control/PostgreSQL/guard/proxy 한도는 각각 512/640/256/32/96 MiB이며 조사·코드 실행은 PC에 둔다. CPU credit mode는 실제 API에서 standard를 확인했다.

운영자 암호·토큰·DB·쿠키 비밀값은 서버에서 생성하고 배포 간 보존한다. 새 로그인 정보는 SSM 출력에서 RSA 암호문으로 전송해 소유자 PC의 Git 제외 파일에 사용자 전용 ACL로 저장했다. 키나 평문 암호를 GitHub·채팅에 출력하지 않았다. 기존 NVIDIA_API_KEY는 전송하지 않았다.

## 공인 연결과 남은 확인

1. DNSZI 화면에서 tracebridge.ckswhd.shop A → 43.200.230.130을 확인했다. 네 권한 네임서버와 일반 DNS 질의가 같은 IP를 응답했다.
2. 공인 HTTPS가 검증된 TLS 1.3 인증서를 제공한다. HTTP는 308로 HTTPS로 이동하고, 인증 없는 운영자 화면·/v1/projects는 401이다. 기존 apex·www는 각각 HTTPS 301·200으로 정상이다. 실제 브라우저의 화면 클릭 여정은 아직 확인하지 않았다.
3. 소유자 승인 뒤 기존 NVIDIA_API_KEY를 같은 AWS 계정의 암호화 SSM /tracebridge/pilot/app-env에 등록하고 새 서버의 모델 연결을 확인한다. 현재 키는 비어 있고 임베딩 워커는 비활성이다.
4. 새 서버용 PC 페어링·실제 프로젝트 처리·Docker 격리와 실제 서비스 회복을 확인한다. 이번 EC2 배포 검사에 실모델·실제 프로젝트 원본 적용을 포함하지 않았다.

[운영 안내](../guides/aws-pilot.md)에 DNS, 배포, 로그인 위치, PC 연결, 하루 약 $0.84의 기본 운영비, Free plan 만료와 중지·백업 절차를 기록했다. 현재 Free plan API 만료는 2026-10-22 22:39 KST이다. 서버 내 dump는 별도 저장소 백업을 대체하지 않는다.

개인 검증 산출물은 소유자 PC의 output/aws-deployment-preflight/에 보존하고 Git에 넣지 않는다.

