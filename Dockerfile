FROM ghcr.io/astral-sh/uv:python3.13-alpine AS builder

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

FROM python:3.13-alpine

RUN addgroup -S app && adduser -S app -G app
WORKDIR /app

COPY --from=builder --chown=app:app /app /app
RUN mkdir -p /app/.data && chown app:app /app/.data

ENV PATH="/app/.venv/bin:$PATH" \
    SCENEQUEUE_DATA_DIR="/app/.data" \
    SCENEQUEUE_HOST="0.0.0.0"

VOLUME ["/app/.data"]
EXPOSE 8889
USER app

CMD ["python", "server.py"]
