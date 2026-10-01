#!/usr/bin/env bash
set -euo pipefail
umask 077
export DEBIAN_FRONTEND=noninteractive
apt-get update -o Acquire::Retries=6
apt-get install -y ca-certificates curl gnupg unzip python3
install -m 0755 -d /etc/apt/keyrings
curl --retry 6 -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<'DOCKER'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Architectures: amd64
Signed-By: /etc/apt/keyrings/docker.asc
DOCKER
apt-get update -o Acquire::Retries=6
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
curl --retry 6 -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/tracebridge-awscli.zip
unzip -q /tmp/tracebridge-awscli.zip -d /tmp/tracebridge-awscli
/tmp/tracebridge-awscli/aws/install
install -d -m 0700 /opt/tracebridge/releases /srv/tracebridge/private /srv/tracebridge/backups
install -d -m 0700 -o 10001 -g 10001 /srv/tracebridge/data
cat > /opt/tracebridge/bootstrap-release.sh <<'RELEASE'
#!/usr/bin/env bash
set -euo pipefail
umask 077
exec 9>/var/lock/tracebridge-deploy.lock
flock -n 9 || { echo 'A deployment is already running'; exit 1; }
test "$#" -eq 2 || exit 2
revision="$1"
digest="$2"
[[ "$revision" =~ ^[a-f0-9]{40}$ && "$digest" =~ ^sha256:[a-f0-9]{64}$ ]] || exit 2
source /opt/tracebridge/aws-runtime
image="$ECR_URI@$digest"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY" >/dev/null
docker pull "$image"
label=$(docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')
test "$label" = "$revision" || { echo 'Image revision mismatch'; exit 1; }
directory="/opt/tracebridge/releases/$revision"
mkdir -p "$directory"
extract_name="tracebridge-extract-$(printf '%s' "$revision" | cut -c1-12)"
docker create --name "$extract_name" "$image" >/dev/null
trap 'docker rm -f "$extract_name" >/dev/null 2>&1 || true' EXIT
docker cp "$extract_name:/app/deploy/." "$directory/deploy"
bash "$directory/deploy/aws/deploy_release.sh" "$revision" "$image"
RELEASE
chmod 700 /opt/tracebridge/bootstrap-release.sh
touch /opt/tracebridge/bootstrap-ready
