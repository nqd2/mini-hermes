# syntax=docker/dockerfile:1
FROM node:22-alpine AS frontend
ARG VITE_WEAI_BACKEND_URL=""
ENV VITE_WEAI_BACKEND_URL=${VITE_WEAI_BACKEND_URL}
WORKDIR /src/packages/frontend
COPY packages/frontend/package.json packages/frontend/package-lock.json ./
RUN npm ci --ignore-scripts --force
COPY packages/frontend/ ./
RUN npm run build

FROM node:22-bookworm-slim AS js-tools
WORKDIR /opt/agent-runtime-js
RUN npm install --no-audit --no-fund --omit=dev --no-save docx@9.15.2 pptxgenjs@4.0.1

FROM python:3.12-slim AS runtime
ARG WEAI_RELEASE_VERSION=dev
ENV WEAI_RELEASE_VERSION=${WEAI_RELEASE_VERSION}
ENV UV_HTTP_TIMEOUT=180
COPY --from=ghcr.io/astral-sh/uv:0.12.20 /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer libreoffice-calc libreoffice-impress \
    poppler-utils pandoc zip unzip libstdc++6 curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=js-tools /usr/local/bin/node /usr/local/bin/node
COPY --from=js-tools /opt/agent-runtime-js/node_modules /opt/agent-runtime-js/node_modules
WORKDIR /app/packages/backend
COPY packages/backend/pyproject.toml packages/backend/uv.lock packages/backend/README.md packages/backend/LICENSE* packages/backend/CHANGELOG.md ./
COPY packages/backend/requirements-agent-runtime.lock ./
RUN uv pip install --system --no-cache -r requirements-agent-runtime.lock
COPY packages/backend/weai ./weai
COPY scripts/check_release_manifest.py /app/scripts/
RUN python /app/scripts/check_release_manifest.py
COPY --from=frontend /src/packages/frontend/build ./weai/frontend
ENV PYTHONPATH="/app/packages/backend" \
    ENV=prod \
    DATA_DIR=/app/data \
    FRONTEND_BUILD_DIR=/app/packages/backend/weai/frontend \
    NODE_PATH=/opt/agent-runtime-js/node_modules \
    PORT=8080 \
    PYTHONUNBUFFERED=1
RUN mkdir -p /app/data
EXPOSE 8080
CMD ["uvicorn", "weai.main:app", "--host", "0.0.0.0", "--port", "8080"]
