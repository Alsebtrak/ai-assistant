#!/bin/bash
# Ежедневный бэкап Postgres в Object Storage.
set -euo pipefail

cd "$(dirname "$0")/.."
set -a
. ./.env
set +a

STAMP=$(date +%Y%m%d-%H%M)
FILE="/var/backups/assistant/db-${STAMP}.sql.gz"
mkdir -p "$(dirname "$FILE")"

docker compose exec -T postgres pg_dump -U assistant assistant | gzip > "$FILE"

AWS_ACCESS_KEY_ID="$S3_ACCESS_KEY" \
AWS_SECRET_ACCESS_KEY="$S3_SECRET_KEY" \
aws --endpoint-url "$S3_ENDPOINT" s3 cp "$FILE" "s3://$S3_BUCKET/backups/$(basename "$FILE")"

find /var/backups/assistant -name 'db-*.sql.gz' -mtime +7 -delete
