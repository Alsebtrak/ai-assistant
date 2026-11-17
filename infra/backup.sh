#!/bin/bash
# Ежедневный бэкап Postgres в Object Storage (rclone).
set -euo pipefail

cd "$(dirname "$0")/.."
set -a
. ./.env
set +a

STAMP=$(date +%Y%m%d-%H%M)
FILE="/var/backups/assistant/db-${STAMP}.sql.gz"
mkdir -p "$(dirname "$FILE")"

docker compose exec -T postgres pg_dump -U assistant assistant | gzip > "$FILE"

export RCLONE_CONFIG_ASSISTANT_TYPE=s3
export RCLONE_CONFIG_ASSISTANT_PROVIDER=Other
export RCLONE_CONFIG_ASSISTANT_ENDPOINT="$S3_ENDPOINT"
export RCLONE_CONFIG_ASSISTANT_ACCESS_KEY_ID="$S3_ACCESS_KEY"
export RCLONE_CONFIG_ASSISTANT_SECRET_ACCESS_KEY="$S3_SECRET_KEY"
export RCLONE_CONFIG_ASSISTANT_REGION="$S3_REGION"

rclone copy "$FILE" "assistant:$S3_BUCKET/backups/"

find /var/backups/assistant -name 'db-*.sql.gz' -mtime +7 -delete
