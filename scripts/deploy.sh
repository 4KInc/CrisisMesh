#!/usr/bin/env bash
# Deploy CrisisMesh to Google Cloud Run
set -euo pipefail

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:?Set GOOGLE_CLOUD_PROJECT}"
REGION="${GOOGLE_CLOUD_REGION:-us-central1}"
SERVICE_NAME="crisismesh"
IMAGE="gcr.io/${PROJECT_ID}/${SERVICE_NAME}"

echo "=== Building container image ==="
gcloud builds submit --tag "${IMAGE}" --project "${PROJECT_ID}"

echo "=== Deploying to Cloud Run ==="
gcloud run deploy "${SERVICE_NAME}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --platform managed \
  --allow-unauthenticated \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID}" \
  --memory 1Gi \
  --cpu 1 \
  --timeout 300 \
  --project "${PROJECT_ID}"

echo "=== Deployment complete ==="
gcloud run services describe "${SERVICE_NAME}" \
  --region "${REGION}" \
  --project "${PROJECT_ID}" \
  --format "value(status.url)"
