#!/bin/sh
set -eu
# Auditar también dependencias y observabilidad. No subir informes de secretos.
scanner=${CHAT_TRIVY:-artifacts/n9/tools/trivy}
mkdir -p artifacts/n9
docker compose --profile observability config --images | sort -u > artifacts/n9/images.txt
index=0
failed=0
while IFS= read -r image; do
    index=$((index + 1))
    if ! "$scanner" image --scanners vuln --severity CRITICAL --exit-code 1 \
        --format json --output "artifacts/n9/image-vuln-$index.json" "$image"; then
        failed=1
    fi
    if ! "$scanner" image --scanners secret --exit-code 1 \
        --format json --output "artifacts/n9/image-secrets-$index.json" "$image"; then
        failed=1
    fi
done < artifacts/n9/images.txt
exit "$failed"
