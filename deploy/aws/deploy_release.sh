#!/usr/bin/env bash
set -euo pipefail
umask 077
revision="${1:?release revision required}"
image="${2:?immutable image required}"
release_dir="/opt/tracebridge/releases/$revision"
[[ "$revision" =~ ^[a-f0-9]{40}$ ]] || exit 2
[[ "$image" =~ @sha256:[a-f0-9]{64}$ ]] || exit 2
test -d "$release_dir/deploy"
private_dir=/srv/tracebridge/private
mkdir -p "$private_dir" /srv/tracebridge/backups
chmod 700 "$private_dir" /srv/tracebridge/backups
if aws ssm get-parameter --region "$AWS_REGION" --name /tracebridge/pilot/app-env --with-decryption \
  --query Parameter.Value --output text > "$private_dir/app.env.next" 2> "$private_dir/parameter-error.txt"; then
  mv "$private_dir/app.env.next" "$private_dir/app.env"
elif grep -q ParameterNotFound "$private_dir/parameter-error.txt"; then
  if ! test -f "$private_dir/app.env"; then
    printf 'NVIDIA_API_KEY=\nTRACEBRIDGE_EMBEDDINGS_ENABLED=0\n' > "$private_dir/app.env"
  fi
else
  echo 'Unable to load runtime configuration'; exit 1
fi
chmod 600 "$private_dir/app.env"
python3 "$release_dir/deploy/aws/prepare_runtime.py" --settings "$private_dir/settings.json" \
  --revision "$revision" --image "$image" --directory "$release_dir/deploy"
dc() {
  docker compose --project-name tracebridge --env-file "$release_dir/deploy/.env" \
    -f "$release_dir/deploy/compose.yaml" -f "$release_dir/deploy/control-plane.compose.yaml" \
    -f "$release_dir/deploy/postgres.compose.yaml" -f "$release_dir/deploy/compose.pilot.yaml" "$@"
}
dc config --quiet
available_mb=$(df -Pm /var/lib/docker | awk 'NR==2 {print $4}')
test "$available_mb" -ge 3000 || { echo 'Insufficient disk space for release'; exit 1; }
# Back up the existing DB before startup migrations. Never restore automatically.
if dc ps --status running --services | grep -qx postgres; then
  dc exec -T postgres pg_dump -U tracebridge -d tracebridge -Fc \
    > "/srv/tracebridge/backups/pre-$revision-$(date -u +%Y%m%dT%H%M%SZ).dump"
fi
dc pull postgres guard proxy
dc up -d --no-build --wait --wait-timeout 180 postgres control web guard
dc exec -T control python -c "import json,urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:8765/health',timeout=5)); assert r['storage']=='POSTGRESQL'"
dc exec -T web python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/owner/_stcore/health',timeout=5).read()"
dc up -d --no-build proxy
ln -sfn "$release_dir" /opt/tracebridge/current
python3 - "$revision" "$image" <<'PY'
import json, pathlib, sys, time
pathlib.Path("/srv/tracebridge/private/release.json").write_text(json.dumps({
 "revision":sys.argv[1],"image":sys.argv[2],"internal_health":"PASSED",
 "public_https":"REQUIRES_EXTERNAL_CHECK","recorded_at":time.time()}))
print(json.dumps({"revision":sys.argv[1],"internal_health":"PASSED","public_https":"REQUIRES_EXTERNAL_CHECK"}))
PY
dc stats --no-stream --format '{{.Name}} {{.MemUsage}}'
