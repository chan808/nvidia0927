# 기존 Agolive 서버에 추가 배포하는 방법

확인일: 2026-09-29. 사용자가 허가한 Agolive 저장소의 Terraform·배포 문서와 실제 AWS·SSM 상태를 읽어 확인했다. 원본 저장소와 서버 설정은 변경하지 않았다.

이후 사용자 선택은 [신규 AWS 계정](new-aws.md)의 별도 서버 준비로 바뀌었다. 이 문서는 이전 공유안의 기록이며 현재 배포 지시가 아니다. 기존 URL 접속 가능 여부는 별도로 확인하지 않았다.

## 결론

**이미 쓰는 AWS 서버의 Nginx를 공유하고 TraceBridge를 별도 Compose 프로젝트로 추가하는 경로가 있다.** 새 서버와 도메인을 구매할 필요를 줄일 수 있고, 기존 Free plan의 잔액 안에서 시연을 준비할 수 있다. 현재 자원 여유가 작으므로 실제 컨테이너 기동·메모리·응답 검증을 통과해야 공유 배포를 확정한다.

AWS 계정의 실제 플랜·잔액·만료일과 현재 서버 사용량은 로컬의 `output/deployment-reuse/`에 보존한다. 잔액은 변하므로 공개 배포 설정이나 고정 비용 약속으로 사용하지 않는다. Free plan은 기간 또는 크레딧 소진에 따라 종료되며 NVIDIA API 비용은 별도다. [AWS 무료 플랜](https://aws.amazon.com/free/free-tier-faqs/)

## 확인한 기존 기반

| 구성 | 확인 |
| --- | --- |
| AWS | 프로젝트 Terraform state와 현재 CLI 계정 일치, EC2 실행 중 |
| 서버 | 서울 리전, Ubuntu 24.04, x86_64, t3.small |
| 원격 관리 | SSM Online. 저장소 런북은 SSH 대신 SSM 사용 |
| 배포 | Docker Compose, GitHub Actions OIDC, ECR push, SSM deploy.sh |
| 공개 진입점 | `agolive-nginx`가 80/443 사용, 다른 서비스도 같은 Nginx에서 운영 |
| 라우트 추가 위치 | `/opt/agolive/infra/docker/nginx/conf.d`가 Nginx에 읽기 전용 마운트 |
| 인증서 | `/opt/agolive/certbot/conf`와 challenge 경로를 마운트 |
| Docker 네트워크 | 실행 중인 Nginx에서 `docker_agolive-net` 관측 |
| 자원 | 가용 RAM 약 654 MiB, 사용 중 swap 약 843 MiB, 디스크 여유 약 27 GiB인 시점의 관측 |

이 관측은 한 시점의 값이다. 기존 API Blue/Green 전환이나 트래픽 상승 때 여유가 줄어들 수 있다. 공유 배포가 확인되기 전에는 원래 서비스를 중지하거나 서버 사양을 바꾸지 않는다.

## 기존 서버에서 달라지는 설정

**Caddy를 새로 80/443에 띄우지 않는다.** 현재 Nginx가 해당 포트를 사용한다. TraceBridge 웹은 같은 Docker 네트워크의 별도 이름 `tracebridge-web:8501`로 연결한다. Nginx 컨테이너의 `127.0.0.1`은 서버 호스트나 TraceBridge 컨테이너가 아니므로 그 주소로 연결하지 않는다.

| 항목 | 공유 배포 |
| --- | --- |
| 코드 | `/opt/tracebridge` 등 별도 디렉터리 |
| 사건 DB·산출물 | `/srv/tracebridge/data`, 기존 PostgreSQL과 분리 |
| 비밀 설정 | TraceBridge 전용. 기존 Agolive `.env` 전체를 복사하지 않음 |
| Docker 프로젝트 | `tracebridge`, 기존 `docker` 프로젝트와 별도 |
| 웹 자원 | 첫 확인용 512 MiB·CPU 0.5 한도. 충분한지 실제 확인 필요 |
| 역방향 프록시 | 기존 Nginx의 새로운 hostname/server block |
| HTTPS | 새 hostname용 인증서. 기존 apex/www 인증서가 자동으로 새 이름을 포함한다고 가정하지 않음 |
| 이미지 | 서버 밖의 Linux Docker 환경에서 빌드한 검증 이미지 사용 |

## 준비 파일

- `deploy/compose.shared.yaml`: 기존 Nginx 연결, Caddy 기본 기동 제외, 웹 메모리 제한.
- `deploy/nginx.bootstrap.conf.template`: 새 이름의 인증서 발급 전 HTTP challenge만 허용.
- `deploy/nginx.shared.conf.template`: 인증서 발급 후 HTTPS·접근 암호·Streamlit WebSocket 전달.

이 파일들은 템플릿이다. 서버에 적용하거나 Nginx를 reload하지 않았다. 기존 Agolive Terraform·Compose·deploy.sh도 수정하지 않았다.

## 적용 순서

### 1. 이미지 먼저 확인

별도 Linux Docker 환경 또는 CI에서 `deploy/Dockerfile`로 이미지를 빌드한다. source SHA·Linux 의존성·이미지 digest를 기록하고 신뢰하는 레지스트리에 보관한다. 현재 PC Docker daemon은 연결되지 않아 아직 빌드하지 못했다.

운영 서버에서 pip/NAT 이미지를 직접 빌드하는 방식은 현재 메모리 여유에 맞지 않는다. 원격에 source를 가져왔다고 `docker compose up --build`부터 실행하지 않는다.

기존 GitHub OIDC의 신뢰 대상과 ECR 권한은 Agolive용이다. TraceBridge 저장소의 Actions가 같은 역할을 자동 사용할 수 있다고 가정하지 않는다. 새 CI 권한 연결은 실제 IAM 정책을 검토해 추가하거나, 우선 검증한 이미지의 수동 pull과 소유자의 SSM 경로를 사용한다.

### 2. 전용 데이터·설정과 도메인 준비

[기본 배포 가이드](deployment.md)의 코드·데이터·비밀값 설정 절차를 따르되 기존 서버에 별도 디렉터리를 사용한다. SSH가 닫힌 현재 서버에서는 SSM 경로로 관리한다.

`deploy/.env`에서:

```dotenv
TRACEBRIDGE_DOMAIN=tracebridge.your-existing-domain.com
TRACEBRIDGE_DATA_DIR=/srv/tracebridge/data
TRACEBRIDGE_IMAGE=your-registry/tracebridge@sha256:실제_검증한_digest
TRACEBRIDGE_INGRESS_NETWORK=docker_agolive-net
STREAMLIT_SERVER_COOKIE_SECRET=생성한_랜덤_비밀값
```

기본 Compose가 요구하는 다른 예시 값도 파일에 남기되, 이번에는 Caddy가 실행되지 않는다. Nginx의 인증 암호는 별도 `tracebridge.htpasswd` 파일을 사용한다.

기존 도메인의 `tracebridge` 하위 이름을 같은 고정 IP로 연결하면 도메인을 새로 살 필요가 없다. DNS 관리 권한과 현재 인증서의 이름 범위를 실제로 확인한다.

### 3. 웹 컨테이너만 기동

서버 bash, TraceBridge 저장소 루트:

```bash
dc() {
  docker compose --env-file deploy/.env \
    -f deploy/compose.yaml -f deploy/compose.shared.yaml "$@"
}

dc config --quiet
dc pull web
dc up -d --no-build web
dc ps
dc stats --no-stream
dc exec -T web python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=5).read().decode())"
```

SSM의 root 명령 또는 적절한 Docker 권한으로 실행한다. `web`만 선택하며 `proxy`는 실행하지 않는다. 메모리 한도 때문에 기동이 실패하면 그대로 외부 라우트를 열지 않는다. 기존 서비스 응답·swap·메모리 변화도 확인한다.

공유 네트워크에 연결하는 것이 Agolive의 DB·로그를 조사하도록 등록하는 것은 아니다. 원하는 개발자 PC 조사는 별도 실행기 연결을 사용한다.

### 4. Nginx와 인증서

1. 두 템플릿의 `__DOMAIN__`을 실제 새 hostname으로 교체한다.
2. HTTP 전용 bootstrap 설정을 기존 `conf.d`의 별도 파일로 배치한다.
3. 기존 Nginx에서 `nginx -t`가 통과한 뒤 reload한다. 기존 사이트 응답도 확인한다.
4. 기존 Certbot 웹루트 `/var/www/certbot` 매핑을 사용해 새 hostname의 인증서를 발급한다. Agolive의 전체 최초 배포/init-ssl 스크립트를 다시 실행해 기존 라우트를 재생성하는 방식은 피한다.
5. Nginx가 읽을 전용 `tracebridge.htpasswd`를 생성한다. 현재 Nginx 이미지가 해당 암호 해시 형식을 지원하는지 확인한다.
6. 인증서가 실제로 존재하고 새 hostname을 포함하면 HTTPS 템플릿으로 교체한다.
7. `nginx -t` 후 reload한다. 인증 전 401, 인증 후 웹·WebSocket·사진 업로드를 확인한다.
8. 인증서 갱신 스크립트가 새 인증서를 갱신하고 Nginx에 반영하는지 확인한다.

SSL 파일과 auth 파일이 없거나 upstream이 실행되지 않으면 Nginx 설정 검사가 실패할 수 있다. 새 파일만 되돌릴 수 있도록 기존 파일을 보존하고, 전체 Compose의 down/up이나 기존 upstream 교체로 처리하지 않는다.

### 5. 공유 운영을 확정할 확인

- TraceBridge의 최초 로딩·씨드 제보·저장·재시작 후 조회.
- 기존 사이트와 API의 응답이 유지되는지.
- TraceBridge의 실제 메모리·OOM·재시작 여부와 서버의 메모리/swap 변화.
- API Blue/Green 배포 때 추가 프로세스가 올라오는 경우의 여유.
- control API와 PC 실행기를 함께 연결할 때 전체 자원 재확인.
- Free plan 잔액/만료 전에 데이터 백업과 이후 운영 경로 결정.

실제 측정에서 부족하면 기존 서비스를 임의 중지하지 않는다. 허용된 크레딧 범위의 별도 서버나 Oracle 무료 자원으로 분리하는 선택을 검토한다. 공유 서버 배포는 새 VM 비용을 줄이지만 기존 VM·EBS·IP·저장·모델 비용이 영구 무료가 된다는 뜻은 아니다.

## 완료 상태

**완료:** 기존 저장소 읽기, AWS 계정 일치·활성 Free plan·서버 실행·SSM Online 확인, 실제 자원/컨테이너/Nginx 경로 조회, 공유 배포 템플릿 작성.

**이 공유 서버에서 미완료:** 최종 릴리스 이미지 설치·기동, 메모리 한도에서의 전체 서비스 검증, DNS·새 인증서·Nginx 적용, 공인 HTTPS를 통한 PC 연결. 서버에서는 읽기 전용 조사만 수행했다.

후속으로 [로컬 Linux 이미지](../validation/linux-deployment.md)와 [소유자 API·PC 실행기의 실제 GUI 연결](../validation/local-gui-final.md)을 별도로 확인했다. 공유 서버 배포 완료를 뜻하지 않으며, Linux 이미지는 최종 코드로 다시 빌드·검증해야 한다.
