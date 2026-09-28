# 무료 우선 배포: AWS Free Tier와 대안

확인일: 2026-09-29. 기존 Python·Streamlit·SQLite 구조를 유지하는 것이 기준이다. 서버 비용의 무료 조건과 NVIDIA API 사용료는 별개다.

## 추천 순서

**현재 선택은 [신규 AWS 계정 배포 기반](new-aws.md)이다.** 새 계정의 무료 자격과 CLI 계정 일치를 확인한 뒤 독립 EC2 경로를 사용한다. [기존 서버 재사용](existing-server.md)은 이전에 검토한 별도 선택지로 남긴다. 아래는 무료 후보의 비교다.

1. **신규 AWS 고객 또는 아직 유효한 Free plan이면 EC2 `t3.small` + 기존 Compose.** 무료 크레딧 안에서 시연을 시작하고, 계정이 Free plan인지 확인한다.
2. **기존 AWS 계정이면 Lightsail 2 GB의 90일 무료 체험 대상인지 확인.** 생성과 관리가 간단하고 기존 Compose를 사용할 수 있다. 이미 혜택을 사용했으면 다시 무료라고 가정하지 않는다.
3. **기간 제한 없는 무료 할당이 필요하면 Oracle Always Free A1.** 서버 생성 가능 여부와 ARM 의존성 빌드를 먼저 확인한다. 시간이 촉박할 때는 용량 확보를 기다리는 문제가 있다.

계정 상태를 확인하기 전에는 1번을 조건부 추천한다. 신규 무료 혜택을 받기 위해 과거 AWS 고객이 새 계정을 만드는 것은 대상 조건을 만족하지 않는다.

## 비교

| 후보 | 무료 조건 | 현재 구조와의 적합성 | 판단 |
| --- | --- | --- | --- |
| **AWS EC2 / Free plan** | 신규 고객 크레딧, 최대 6개월 또는 잔액 소진까지 | x86_64 Ubuntu·Compose·영속 디스크 유지 | 무료 혜택이 있으면 우선 |
| **AWS Lightsail 2 GB** | Paid plan의 대상 번들 90일 체험, 계정별 대상 여부 확인 | 현재 템플릿 그대로, 2 GB용 자원 제한 추가 | 기존 계정의 체험 대상이면 빠른 선택 |
| **Oracle Always Free A1** | 현재 공식 무료 계정 할당: 총 2 OCPU·12 GB RAM, 총 200 GB 블록 저장소 | SQLite 유지 가능. ARM에서 새 이미지 빌드 필요 | 장기 무료 후보, 용량·호환성 확인 필요 |
| **Render Free** | 무료 웹 서비스, 휴면·파일 저장 제약 | 재시작·재배포·휴면 때 SQLite와 파일 산출물 손실 | 현재 상태 저장 구조로는 적합하지 않음 |
| **Streamlit Community Cloud** | 무료 앱 호스팅 | 로컬 파일 지속성 미보장. DB 외부화 등의 변경 필요 | 단기 화면 확인용 후보 |

근거: [AWS EC2 무료 조건](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html), [Lightsail/EC2 무료 비교](https://aws.amazon.com/free/compute/lightsail-vs-ec2/), [Oracle 무료 자원](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm), [Render Free](https://render.com/docs/free), [Streamlit 저장 제약](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data).

## 1. 신규 AWS Free plan: 이번 시연의 우선 경로

### 무료 조건

현재 신규 고객은 가입 시 **$100 크레딧**을 받고, 활동을 통해 **최대 $100 추가**를 받을 수 있다. 처음부터 $200가 지급되는 것은 아니다. Free plan은 **최대 6개월 또는 크레딧 소진 중 빠른 시점**까지이며, 이 기간의 서버 사용이 무제한인 것은 아니다. Paid plan과는 과금 경계가 다르다. 계정 화면의 플랜·잔액·만료일을 기준으로 한다. [AWS Free Tier FAQ](https://aws.amazon.com/free/free-tier-faqs/)

2025-07-15 이전 개설 계정의 구형 EC2 무료 혜택은 개설 후 12개월이었다. 현재 날짜에는 그 12개월이 이미 지났으므로 예전의 “micro 750시간 무료” 설명을 그대로 적용하면 안 된다. 새 Free Tier의 `Free tier eligible` 표시도 크레딧 소모가 없는 서버라는 뜻으로 해석하지 않는다. [계정 개설일별 EC2 조건](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html)

Free plan이 끝나면 서비스에 접근할 수 없으므로, 만료 전에 사건 DB·산출물을 백업하고 이후 운영 경로를 정한다. 콘솔의 잔액·만료일을 확인하고 Paid plan 전환은 별도로 판단한다. [플랜 만료](https://aws.amazon.com/free/free-tier-faqs/)

### EC2 생성값

| 설정 | 선택 |
| --- | --- |
| 리전 | 서울. 해당 인스턴스와 Free plan 사용 가능 여부 확인 |
| AMI | 라이선스 추가 요금 없는 Ubuntu Server 24.04 LTS, **64-bit x86** |
| 인스턴스 | **t3.small: 2 vCPU·2 GiB RAM**, Free plan 대상 표시 확인 |
| 저장소 | 암호화된 gp3 EBS, 우선 30 GB |
| 네트워크 | 기본 VPC의 인터넷 연결 가능한 public subnet, 공인 IP |
| 보안 그룹 | SSH 22는 My IP, HTTP 80·HTTPS 443만 웹에 공개 |
| CPU 크레딧 | 비용 예측을 우선하면 **Standard**. 크레딧 소진 시 성능 제한 가능 |
| 그 외 | 첫 구성은 인스턴스 한 대·디스크 한 개. NAT Gateway·로드밸런서·별도 RDS 없이 시작 |

`t3.small`은 현재 Free plan 대상에 포함된다. `t3.micro`의 1 GB를 현재 무거운 Python 설치의 기본으로 잡지 않는다. 2 GB로도 Linux 빌드/실행을 보장한 것은 아니며 실제 확인이 필요하다. T3는 기본 Unlimited로 추가 CPU 사용 비용이 발생할 수 있고 Standard를 선택할 수 있다. [대상 인스턴스](https://aws.amazon.com/free/compute/lightsail-vs-ec2/), [T3 사양·크레딧](https://aws.amazon.com/ec2/instance-types/t3/)

인스턴스뿐 아니라 EBS·공인 IPv4·전송량·스냅샷도 비용/크레딧 항목으로 확인한다. 생성 후 Billing의 실제 사용 내역을 기준으로 잔액을 관리한다. 중지한 EC2의 EBS와 별도로 보유한 IP는 계속 남으므로, 시연 종료 후 서버를 중지만 하고 모든 자원 비용이 끝났다고 판단하지 않는다. [VPC 가격](https://aws.amazon.com/vpc/pricing/)

### 도메인도 무료로 시작

소유한 도메인이 없다면 [DuckDNS](https://www.duckdns.org/about.jsp)의 무료 하위 도메인을 사용할 수 있다. 등록한 `your-name.duckdns.org`를 EC2의 현재 공인 IP에 연결하고, `TRACEBRIDGE_DOMAIN`에 그 이름을 넣는다. DNS 전파와 외부 80/443을 확인하면 기존 Caddy로 HTTPS를 발급한다.

자동 할당된 EC2 공인 IP는 stop/start 때 바뀔 수 있다. 이때 DuckDNS IP를 갱신한다. 고정 주소가 필요하면 Elastic IP의 요금과 크레딧 적용을 확인해 사용한다. DuckDNS 토큰은 Git·공개 URL·로그에 넣지 않는다.

### 실행 순서

1. Free plan·크레딧·만료일을 확인하고 위 값으로 EC2를 한 대 만든다.
2. SSH 키를 보관하고 Ubuntu 사용자로 접속한다.
3. [기존 배포 가이드](deployment.md)의 Docker 설치와 코드·설정 배치 절차를 따른다. Lightsail 생성 단계만 위 EC2 생성 단계로 교체한다.
4. 2 GB 서버에서는 아래 Compose 오버라이드를 사용한다.
5. 기존 가이드의 빌드·상태·HTTPS·저장·재시작 확인을 수행한다.

저장소 루트의 서버 bash:

```bash
dc() {
  sudo docker compose --env-file deploy/.env \
    -f deploy/compose.yaml -f deploy/compose.small.yaml "$@"
}

dc config --quiet
dc build web
dc run --rm --no-deps web python -m pip check
dc up -d
dc ps
dc stats --no-stream
```

오버라이드는 웹 메모리를 1 GB, 프록시를 128 MB로 제한해 OS에 여유를 둔다. 이것은 **첫 단일 사용자 시연용 설정**이며 성능 검증 결과가 아니다. 빌드에는 이 실행 메모리 한도가 적용되지 않는다. `dc logs --tail=80 web`과 메모리 사용량을 확인한다.

빌드 OOM이나 웹 재시작이 생기면 같은 상태로 완료 처리하지 않는다. 4 GB가 필요하면 Free plan에서 실제 선택 가능한 대상(예: `c7i-flex.large`)과 예상 크레딧 소비를 확인해 조정한다. `t3.medium`이 이 Free plan에서 허용된다고 임의로 가정하지 않는다. 연결 API가 추가될 때는 전체 메모리와 프로세스별 한도를 다시 확인한다.

EC2에서도 SQLite는 `/srv/tracebridge/data`에 유지된다. 컨테이너 교체는 데이터 경로를 보존하지만 인스턴스 삭제·루트 볼륨 삭제는 별개다. 기존 가이드의 서버 밖 백업을 수행한다.

## 2. 기존 AWS 계정: Lightsail 90일 체험

AWS 공식 비교 페이지는 **Paid plan의 90일 무료 체험**으로 Linux 공인 IPv4 번들 **$5·$7·$12**를 안내한다. 현재 구조에는 **$12 / 2 GB** 번들이 가장 적절한 시작점이다. 이전에 추천한 **$24 / 4 GB는 이 90일 대상 목록에 없다.** [공식 무료 비교](https://aws.amazon.com/free/compute/lightsail-vs-ec2/)

1. Lightsail 생성 화면에서 Ubuntu 24.04·IPv4·2 GB 번들에 무료 체험 표시가 있는지 확인한다.
2. 계정의 이전 사용과 무료 기간·시간 한도를 확인한다.
3. 대상이면 인스턴스 한 대, 연결된 고정 IP, 기존 Caddy·Compose·`compose.small.yaml`로 배포한다.
4. 추가 디스크·스냅샷·전송량 등 체험에 포함되지 않는 항목을 구분한다.
5. 무료 기간이 끝나기 전에 백업하고 유료 유지/이전을 결정한다.

이 경로는 Free plan처럼 과금이 막힌 계정이라고 설명하면 안 된다. 생성 화면이 유료 전환을 요구하면 비용 없이 계속되는 단계로 취급하지 않는다. 무료 체험 표시가 없으면 만들기부터 진행하지 않는다.

## 3. 장기 무료: Oracle Always Free

현재 Oracle 공식 문서의 **Always Free 계정** 기준 A1 무료 할당은 월 1,500 OCPU 시간·9,000 GB 시간, 즉 총 **2 OCPU·12 GB RAM**이며 부트·블록 볼륨 합계 **200 GB**다. 예전 4 OCPU·24 GB 안내를 현재 신규 무료 계정의 기준으로 사용하지 않는다. 홈 리전의 무료 자원을 사용해야 한다. [현재 무료 자원](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)

서버가 생성되면 Ubuntu **ARM64**에 기존 Compose를 적용할 수 있는 후보다. 다만 Python/NAT의 ARM 간접 의존성과 새 이미지 빌드는 미검증이다. x86 이미지를 강제로 지정해 호환 문제를 덮지 않는다. 현재 2 GB 웹 한도는 이 큰 RAM 할당 안에서 그대로 시작할 수 있다.

공식 문서는 **out of host capacity**와 유휴 인스턴스 회수 가능성을 명시한다. 따라서 시간에 쫓기는 첫 시연에서는 생성에 성공했는지부터 확인하고, 장기 운영에서도 서버 밖 백업을 유지한다. [용량·유휴 회수 조건](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)

## 현재 서비스에서 무료 PaaS가 어려운 이유

Render Free는 15분간 수신 트래픽이 없으면 휴면에 들어가고, 재시작·재배포·휴면 때 파일 변경을 잃는다. 무료 영속 디스크가 없고 무료 Postgres도 30일 후 만료된다. 사건 기억과 후보 산출물을 유지하려면 DB·파일 저장 구조를 바꿔야 한다. [Render 무료 제약](https://render.com/docs/free)

Streamlit Community Cloud도 로컬 파일 저장을 보장하지 않는다. 화면 공개는 빠를 수 있지만 현재 SQLite를 지속적인 사건 기억으로 쓰는 전체 서비스를 그대로 보존하는 배포는 아니다. [Streamlit 파일 지속성](https://docs.streamlit.io/develop/concepts/connections/connecting-to-data)

이번에는 무료 VM의 영속 디스크를 쓰는 편이 기존 구조에 맞는다. 서버리스/무료 PaaS에 맞춘 DB 이식은 현재 다른 세션의 조사 로직 작업과 별도로 필요한 변경이다.

## 현재 확인 범위

무료 조건은 위 공식 문서로 조사했다. 새 계정의 체험 자격·잔액·리전 자원은 아직 확인하지 않았다. 이전 고정 사본에서 로컬 Linux 이미지와 1 GB 제한의 오프라인 실행·저장·초기 화면·Caddy 설정을 확인했다. 이후 소유자 API·PC 실행기를 구현하고 [localhost 실제 GUI·NVIDIA 연결](../validation/local-gui-final.md)을 확인했다. 실제 2 GB EC2의 메모리·공인 HTTPS·해당 서버를 통한 실모델·PC 연결은 아직 확인하지 않았다. [Linux 실행 기록](../validation/linux-deployment.md)은 당시 이미지에만 해당하며 최종 릴리스로 다시 빌드·검증한다.
