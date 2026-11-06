#!/bin/bash
set -euo pipefail

ENV="${1:-dev}"
IMAGE_TAG="${2:-latest}"
ENV_FILE="${3:-/home/vietnh/apps/mini-hermes/envs/backend.${ENV}.env}"

PROJECT_NAME="mini-hermes-${ENV}"
APP_CONTAINER="${PROJECT_NAME}-app"

echo "=== [DEPLOY] Starting deployment for environment: ${ENV} with image tag: ${IMAGE_TAG} ==="

PREV_TAG=""
if docker ps -a --format '{{.Names}}' | grep -q "^${APP_CONTAINER}$"; then
  PREV_TAG=$(docker inspect "${APP_CONTAINER}" --format '{{.Config.Image}}' | awk -F: '{print $2}')
  echo "Current active image tag: ${PREV_TAG}"
else
  echo "No existing container found for ${APP_CONTAINER}. Initial deployment."
fi

rollback() {
  echo "!!! [CRITICAL] Health check failed for ${PROJECT_NAME} !!!"
  if [ -n "${PREV_TAG}" ] && [ "${PREV_TAG}" != "${IMAGE_TAG}" ]; then
    echo "=== [ROLLBACK] Reverting to previous stable image tag: ${PREV_TAG} ==="
    export COMPOSE_PROJECT_NAME="${PROJECT_NAME}"
    export IMAGE_TAG="${PREV_TAG}"
    docker compose -p "${PROJECT_NAME}" --env-file "${ENV_FILE}" up -d --no-build
    echo "=== [ROLLBACK] Rollback command executed. ==="
  else
    echo "=== [ROLLBACK] No previous tag to revert to. ==="
  fi
  docker logs --tail 50 "${APP_CONTAINER}" || true
  exit 1
}

export COMPOSE_PROJECT_NAME="${PROJECT_NAME}"
export IMAGE_TAG="${IMAGE_TAG}"

echo "Executing docker compose up for ${PROJECT_NAME}..."
docker compose -p "${PROJECT_NAME}" --env-file "${ENV_FILE}" up -d --no-build

echo "=== [VERIFY] Waiting for service ${APP_CONTAINER} to become healthy... ==="
MAX_WAIT=60
WAIT=0
HEALTHY=false

while [ $WAIT -lt $MAX_WAIT ]; do
  STATUS=$(docker inspect --format='{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${APP_CONTAINER}" 2>/dev/null || echo "not_found")
  
  if [ "$STATUS" = "healthy" ]; then
    HEALTHY=true
    break
  fi
  
  if [ "$STATUS" = "running" ]; then
    HTTP_CODE=$(docker exec "${APP_CONTAINER}" python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/health').getcode())" 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" = "200" ]; then
      HEALTHY=true
      break
    fi
  fi

  echo "Waiting for ${APP_CONTAINER} (current status: ${STATUS})... (${WAIT}s/${MAX_WAIT}s)"
  sleep 5
  WAIT=$((WAIT + 5))
done

if [ "$HEALTHY" = true ]; then
  echo "=== [SUCCESS] Deployment healthy! ${APP_CONTAINER} is up and responding on /health ==="
else
  rollback
fi
