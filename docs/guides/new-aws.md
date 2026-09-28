# 신규 AWS 계정 배포 기반

2026-09-29 사용자 선택: 기존 Agolive 서버를 공유하는 대신 새 AWS 계정의 Free plan을 확인한 뒤 별도 EC2에 배포한다. 이번 작업은 기반 준비이며 계정 개설·리소스 생성·기존 서버 수정·실제 배포는 수행하지 않는다.

## 먼저 확인할 무료 자격

AWS는 기존 또는 과거 AWS 고객에게 신규 Free plan/크레딧 자격을 제공하지 않는다고 안내한다. **새 계정을 만든다는 사실만으로 무료 혜택이 새로 지급되는 것은 아니다.** 가입 화면에서 실제 자격과 플랜을 확인한다. [공식 Free Tier FAQ](https://aws.amazon.com/free/free-tier-faqs/)

신규 자격이 인정되면 가입 크레딧 $100, 활동에 따른 최대 $100 추가, Free plan 최대 6개월 또는 크레딧 소진까지라는 현재 조건을 적용한다. 계정의 실제 잔액·만료일을 기준으로 한다. Nvidia API 비용은 별도다. [현재 무료 조건](https://aws.amazon.com/free/free-tier-faqs/)

## 준비된 파일

| 파일 | 역할 |
| --- | --- |
| `deploy/aws/ec2.yaml` | 새 계정의 기본 VPC에 EC2 한 대와 보안 그룹을 생성할 CloudFormation 템플릿 |
| `deploy/check_aws_account.py` | 명시한 CLI 프로필·계정 ID·활성 Free plan·크레딧·만료를 읽기 전용으로 확인 |
| `.github/workflows/deployment-build.yml` | 수동 Linux 이미지 빌드, 의존성·오프라인 씨드·SQLite·프로세스 상태 확인, 선택적 이미지 export |
| `deploy/offline_smoke.py` | 이미지 안의 등록된 씨드 해시, FTS5, 실제 사건 저장과 DB 재조회 확인 |
| `deploy/compose.yaml` + `deploy/compose.small.yaml` | 기존 앱·Caddy·영속 데이터, 2 GB VM용 초기 제한 |

소유자 전용 control API·PC 실행기·서버 모델 게이트웨이는 구현했고 localhost 실제 GUI 연결을 확인했다. 실제 프로젝트를 연결할 배포는 `deploy/control-plane.compose.yaml`을 추가한다. [현재 운영 명령](real-projects.md)과 [GUI 최종 검증](../validation/local-gui-final.md)을 따른다. 아래 소형 서버 제한은 기본 웹 미리보기 기준이므로 control API를 추가할 때 전체 메모리를 다시 측정한다.

## 이후 진행 순서

### 1. 새 계정 프로필 분리

새 계정용 자격 증명을 별도 이름으로 등록한다. 아래 명령은 사용자가 계정 준비 후 실행할 명령이며 이번에는 실행하지 않았다. root 계정의 장기 액세스 키를 배포 파일에 넣지 않는다.

```powershell
aws configure --profile tracebridge-new

.\.venv\Scripts\python.exe deploy/check_aws_account.py `
  --profile tracebridge-new `
  --expected-account '새_계정의_12자리_ID'
```

계정 ID가 다르거나 Free plan이 비활성·잔액 없음·만료 상태면 확인이 실패한다. 스크립트는 서버를 만들지 않는다. 기본 AWS 프로필을 조용히 재사용하지 않으며 기존 Agolive의 계정·역할·인스턴스 ID를 가져오지 않는다.

CLI가 `freetier get-account-plan-state`를 지원하지 않거나 조회 권한이 없으면 AWS CLI와 읽기 권한을 확인한다. 오류를 무료 자격 확인 성공으로 처리하지 않는다.

### 2. Linux 이미지 검증

필요한 변경이 포함된 SHA를 커밋·push한 뒤 GitHub **Actions → Deployment image check (manual) → Run workflow**에서 해당 ref를 선택한다.

- 기본 실행은 이미지 빌드·검사·작은 검증 기록 저장이다.
- `export_image`를 체크하면 검사에 성공한 이미지 tar.gz도 export한다. 이미지가 크므로 artifact 저장량을 확인하고 보관 기간은 1일로 제한했다.
- workflow는 `workflow_dispatch`로만 시작한다. AWS 자격 증명·NVIDIA 키가 필요하지 않고 레지스트리 push·EC2 배포는 수행하지 않는다.
- 검사 컨테이너는 외부 네트워크가 없는 상태로 실행한다. 이미지 빌드 단계의 패키지 다운로드는 네트워크를 사용한다.
- 씨드/SQLite 검사와 1 GB 제한의 프로세스 상태 확인이 모두 통과해야 이후 배포 대상으로 사용한다. 웹의 전체 사용자 흐름과 실모델은 별도 확인이다.

작성한 workflow는 아직 GitHub에서 실행하지 않았다. 이전 고정 사본에서 **Linux 이미지 빌드·오프라인 사건 저장·새 컨테이너의 재조회·초기 화면·Caddy 설정 검증**을 완료했다. 그 이미지에는 후속 API·GUI 변경이 모두 포함되지 않으므로 최종 Git 릴리스로 다시 빌드·확인한다. [당시 Linux 실행 기록](../validation/linux-deployment.md), [GitHub Actions checkout](https://github.com/actions/checkout), [artifact 기능](https://github.com/actions/upload-artifact)

### 3. 신규 EC2 생성용 템플릿

자격과 이미지가 확인된 뒤 새 계정의 CloudFormation 콘솔에서 `deploy/aws/ec2.yaml`을 업로드할 수 있다. 이번에는 스택을 생성하지 않았다.

| 입력/설정 | 값 |
| --- | --- |
| 리전 | 서울 `ap-northeast-2`, Free plan에서 실제 선택 가능한지 확인 |
| `VpcId` | 새 계정의 기본 VPC |
| `SubnetId` | 같은 VPC의 인터넷 연결 가능한 public subnet |
| `KeyPairName` | 새 계정·같은 리전에서 만든 SSH 키 이름 |
| `AdminSshCidr` | 관리자 공인 IPv4 하나 + `/32` |
| AMI | Canonical 공개 SSM 파라미터의 Ubuntu 24.04 amd64 |
| 인스턴스 | `t3.small` 한 대, CPU 크레딧 Standard |
| 디스크 | 암호화 gp3 30 GiB |
| 공개 포트 | HTTP 80, HTTPS 443, 관리자 IP의 SSH 22 |

템플릿은 기존 VPC를 사용하고 IMDSv2를 요구한다. NAT Gateway·로드밸런서·RDS·ECR·GitHub OIDC 역할을 추가하지 않는다. 기존 Agolive Terraform을 새 계정에 그대로 apply하는 방식은 사용하지 않는다.

Ubuntu AMI 참조는 Canonical의 공개 파라미터 구조에 맞춰 24.04·amd64·gp3를 선택했다. 실제 대상 리전에서 값이 조회되는지는 새 계정 준비 후 확인한다. [Canonical AMI 조회](https://ubuntu.com/aws/docs/aws-how-to/instances/find-ubuntu-images/)

루트 EBS는 사건 데이터 보호를 위해 인스턴스 종료 시 자동 삭제하지 않도록 했다. **남은 EBS는 비용/크레딧 항목으로 유지되므로**, 시연 종료 후 백업과 보존 여부를 확인하고 별도로 정리한다. CloudFormation의 VPC/서브넷 관계·리전 AMI 해석·계정별 할당량은 실제 생성 전 콘솔에서 확인해야 한다. [EC2 CloudFormation 속성](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-ec2-instance.html)

### 4. 서버에 검증 이미지와 설정 배치

[기본 배포 가이드](deployment.md)의 Docker 설치, 소스 SHA 선택, `/srv/tracebridge/data` 소유 UID 10001, 비밀 설정을 따른다. 이번 서버는 독립 서버이므로 Caddy가 80/443을 사용한다. 공유 서버용 `compose.shared.yaml`은 선택하지 않는다.

이미지 export를 사용했다면 artifact를 내려받아 서버로 전송한다. tar.gz와 checksum 파일을 같은 디렉터리에 두고:

```bash
sha256sum -c tracebridge-image.tar.gz.sha256
sudo docker load -i tracebridge-image.tar.gz
```

`deploy/.env`의 `TRACEBRIDGE_IMAGE`를 artifact의 `source-commit.txt`에 대응하는 `tracebridge:<SHA>`로 지정한다. 재빌드하지 않고 확인한 이미지를 사용한다.

이전 로컬 검증 압축 파일은 동봉된 `START-HERE.md`의 사본 revision에만 대응한다. 최종 릴리스 배포에는 해당 Git SHA로 새로 빌드·확인한 이미지와 설정을 사용한다.

```bash
dc() {
  sudo docker compose --env-file deploy/.env \
    -f deploy/compose.yaml -f deploy/compose.small.yaml "$@"
}

dc config --quiet
dc up -d --no-build
dc ps
dc stats --no-stream
```

도메인이 없으면 [무료 배포 가이드](free-deployment.md)의 DuckDNS 후보를 사용한다. 자동 할당 공인 IP는 stop/start 후 변경될 수 있다. 실제 DNS와 HTTPS를 확인하기 전에는 배포 주소가 열린다고 안내하지 않는다.

### 5. 배포 후 완료 기준

1. HTTPS 인증·웹·사진 업로드·WebSocket 확인.
2. 사건 접수·저장·웹 재시작 후 같은 사건 조회.
3. 실제 메모리·OOM·재시작 확인.
4. control API와 PC 실행기를 공인 HTTPS로 연결한 뒤 실제 프로젝트 한 사건 확인. 원격 제보는 현재 텍스트만 지원한다.
5. 무료 잔액/만료 전에 서버 밖 백업과 이후 운영 결정.

## 이번 작업에서 완료한 것과 남은 것

**완료:** 새 계정용 템플릿·계정 확인 코드·수동 Linux 이미지 workflow·배포 순서, 실제 로컬 Linux 이미지 빌드·오프라인 저장/재조회·초기 화면·Caddy 설정 확인, 전송용 이미지 압축 및 설정 ZIP.

**남음:** 실제 신규 무료 자격, 새 계정의 CLI 연결, CloudFormation 서버 검증/생성, 최종 릴리스의 Linux 이미지 workflow 실행, DNS·HTTPS와 해당 서버를 통한 실모델·PC 연결. localhost의 실제 NVIDIA·GUI·PC 재연결 확인과 구분한다. URL 접속 문제의 원인은 이 기반 작업에서 조사·확정하지 않았다.
