FROM node:22-alpine AS web

WORKDIR /build
COPY prototype/package.json prototype/package-lock.json ./
RUN npm ci
COPY prototype/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

RUN apt-get update \
    && apt-get install --no-install-recommends -y curl git graphviz \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app/ app/
COPY model/ model/
COPY prompt/ prompt/
COPY AGENTS.md ./
COPY --from=web /build/dist/client/ prototype/dist/client/

ENV SDBP_REVIEW_HOST=0.0.0.0
ENV SDBP_REVIEW_PORT=8417
ENV SDBP_REVIEW_DATA_DIR=/data

EXPOSE 8417
HEALTHCHECK --interval=5s --timeout=3s --retries=10 \
    CMD curl --fail http://127.0.0.1:8417/healthz || exit 1

CMD [".venv/bin/python", "-m", "app.main"]
