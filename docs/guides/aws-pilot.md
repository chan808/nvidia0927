# 전용 AWS 파일럿 운영

2026-09-30. 기존 AWS 계정의 별도 EC2에 TraceBridge 웹·제어 API·PostgreSQL을 실행한다. 조사·수정 검사는 소유자 PC 실행기가 담당한다.

## 연결 정보

- 호스트: https://tracebridge.ckswhd.shop
- DNSZI: ckswhd.shop 영역의 A 레코드 tracebridge → 43.200.230.130, TTL 300
- CloudFormation: tracebridge-pilot / 서울 ap-northeast-2
- EC2: t3.small, Ubuntu 24.04, 2 vCPU / 2 GiB, 암호화 gp3 30 GiB
- 공개 포트: 80·443. SSH·DB·내부 API 포트는 공개하지 않는다.
- 운영자 로그인: owner. 암호는 서버에서 생성하고 /srv/tracebridge/private/settings.json에 보관한다.
- 운영자 API 토큰과 DB·쿠키 비밀값은 서버 파일에 생성하며 배포할 때 보존한다.
- 실모델 키 승인 전에는 NVIDIA_API_KEY를 비워 두고 임베딩 워커를 실행하지 않는다.

DNS 추가 후 Caddy가 해당 호스트의 인증서를 자동 발급한다. caddy_data 볼륨을 보존해 인증서와 갱신 상태를 유지한다. 기존 apex·www·daily 레코드는 유지한다. [Caddy HTTPS](https://caddyserver.com/docs/automatic-https)

## 배포와 확인

GitHub의 Deployment image check (manual)를 배포 브랜치에서 실행한다. deploy=true일 때 ECR 업로드와 전용 SSM 배포 문서를 실행한다.

```powershell
gh workflow run deployment-build.yml --repo chan808/nvidia0927 --ref codex/aws-free-tier-deploy -f deploy=true -f export_image=false
```

워크플로는 동일 커밋의 전체 회귀 성공, Linux 이미지, pip check, 네트워크 차단 씨드, 실제 PostgreSQL 접수·재시작 보존, 로컬 TLS·인증·요청 제한을 확인한다. OIDC 역할은 지정 저장소의 main·배포 브랜치만 신뢰하며 전용 ECR와 새 인스턴스의 배포 문서로 권한을 제한한다.

서버는 이미지 SHA 라벨과 ECR digest를 대조한다. 내부 건강 확인과 외부 DNS·HTTPS는 별도로 기록한다. 내부 검사 성공만으로 인터넷 배포 완료를 표시하지 않는다.

| 서버 경로 | 내용 |
| --- | --- |
| /opt/tracebridge/current | 성공한 릴리스 |
| /opt/tracebridge/releases/<Git SHA>/deploy | 이미지에서 추출한 배포 설정 |
| /srv/tracebridge/data | 사건·제어 API 데이터 |
| /srv/tracebridge/private | 운영자 암호·DB·런타임 설정 |
| /srv/tracebridge/backups | 배포 전 PostgreSQL dump |

SSM Session Manager로 접속해 current/deploy의 .env와 compose.yaml·control-plane.compose.yaml·postgres.compose.yaml·compose.pilot.yaml을 모두 지정해 관리한다. 비밀 파일이나 펼쳐진 Compose 설정을 GitHub·채팅·이슈·로그에 붙이지 않는다.

## PC 연결

DNS·HTTPS 확인 후 운영자 웹의 Remote Projects에서 프로젝트 등록과 일회용 페어링을 진행한다. 기존 runner 설정과 구분해 새 설정 경로를 지정한다.

```powershell
.\.venv\Scripts\python.exe -m scripts.local_runner --config output/local-runner/aws-pilot.json pair --url https://tracebridge.ckswhd.shop
.\.venv\Scripts\python.exe -m scripts.local_runner --config output/local-runner/aws-pilot.json run
```

프로젝트의 실제 경로와 실행 정책은 PC에 둔다. 공개 실모델 수정 후보 실행에는 Docker 격리가 필요하다. PC Docker와 실제 서비스 회복 검증을 확인한 뒤 공개 접수를 켠다. 운영자 웹 접속만으로 자동 수정이 활성화되지는 않는다.

## 비용과 종료

AWS Pricing API 확인값: 서울 t3.small $0.026/시간, gp3 $0.0912/GiB·월. 공인 IPv4 $0.005/시간을 더하면 30 GiB와 IP 1개의 기본 비용은 하루 약 $0.84, 30일 약 $25.06이다. ECR·통신·백업은 별도이며 실제 크레딧 적용 후 금액과 다를 수 있다. [EC2 요금](https://aws.amazon.com/ec2/pricing/on-demand/), [EBS 요금](https://aws.amazon.com/ebs/pricing/), [IPv4 요금](https://aws.amazon.com/vpc/pricing/)

계정 API의 Free plan 만료는 2026-10-22 22:39 KST, 조회 당시 크레딧은 $13.86이다. 콘솔의 $100 표시와 차이가 있어 운영 기간은 낮은 값으로 계산한다. 기존 서비스도 같은 잔액을 소비하므로 새 서버만 계산한 약 16일보다 짧아질 수 있다. 파이프라인의 $5 잔액 검사는 새 배포만 막으며 실행 중인 EC2를 중지하지 않는다. [AWS Free Tier 조건](https://aws.amazon.com/free/terms/)

파일럿을 쉬면 이 스택의 새 인스턴스만 중지한다. 중지 후에도 EBS·EIP·ECR 비용은 남는다. 종료 전에 PostgreSQL dump와 data·private를 소유자 저장소에 백업한다. 템플릿은 EC2 종료 시 root EBS, 스택 삭제 시 ECR를 보존하므로 마지막 정리 때 보존 리소스도 확인한다.

배포 전 dump는 같은 디스크에 있어 외부 백업을 대체하지 않는다. DB 마이그레이션 뒤에는 이전 이미지 자동 롤백을 하지 않는다. 복구할 때 dump·스키마·코드 버전을 함께 검토한다.

