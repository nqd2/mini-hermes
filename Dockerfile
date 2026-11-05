# syntax=docker/dockerfile:1
FROM node:22-alpine AS frontend
WORKDIR /src/packages/frontend
COPY packages/frontend/package.json packages/frontend/package-lock.json ./
RUN npm ci --ignore-scripts --force
COPY packages/frontend/ ./
RUN npm run build

FROM node:22-bookworm-slim AS js-tools
WORKDIR /opt/mini-hermes-js
RUN npm install --no-audit --no-fund --omit=dev --no-save docx@9.15.2 pptxgenjs@4.0.1

FROM python:3.12-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:0.12.20 /uv /uvx /bin/
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer libreoffice-calc libreoffice-impress \
    poppler-utils pandoc zip unzip libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=js-tools /usr/local/bin/node /usr/local/bin/node
COPY --from=js-tools /opt/mini-hermes-js/node_modules /opt/mini-hermes-js/node_modules
WORKDIR /app/packages/backend
COPY packages/backend/pyproject.toml packages/backend/uv.lock packages/backend/README.md packages/backend/LICENSE* packages/backend/CHANGELOG.md ./
COPY packages/backend/requirements-mini-hermes.lock ./
RUN uv pip install --system --no-cache -r requirements-mini-hermes.lock
COPY packages/backend/open_webui ./open_webui
COPY --from=frontend /src/packages/frontend/build ./open_webui/frontend
ENV PYTHONPATH="/app/packages/backend" \
    ENV=prod \
    DATA_DIR=/app/data \
    FRONTEND_BUILD_DIR=/app/packages/backend/open_webui/frontend \
    NODE_PATH=/opt/mini-hermes-js/node_modules \
    PORT=8080 \
    PYTHONUNBUFFERED=1
RUN mkdir -p /app/data
EXPOSE 8080
CMD ["uvicorn", "open_webui.main:app", "--host", "0.0.0.0", "--port", "8080"]
