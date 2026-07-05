#!/bin/sh
set -eu

MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://minio:9000}"
MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin123}"
BUCKET_NAME="${S3_BUCKET_NAME:-products}"
PREVIEWS_PREFIX="${S3_PRODUCT_IMAGES_PREFIX:-previews}"

echo "Configuring MinIO bucket '${BUCKET_NAME}' at ${MINIO_ENDPOINT}..."

until mc alias set local "${MINIO_ENDPOINT}" "${MINIO_ROOT_USER}" "${MINIO_ROOT_PASSWORD}" >/dev/null 2>&1; do
  echo "Waiting for MinIO..."
  sleep 2
done

mc mb "local/${BUCKET_NAME}" --ignore-existing
mc anonymous set download "local/${BUCKET_NAME}/${PREVIEWS_PREFIX}"

echo "MinIO ready: bucket=${BUCKET_NAME}, anonymous read=${PREVIEWS_PREFIX}/*"
