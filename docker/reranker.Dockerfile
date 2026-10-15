FROM ghcr.io/astral-sh/uv:0.13.1 AS uv

FROM python:3.10-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}" \
    HF_HOME=/models/huggingface \
    RERANKER_MODEL=BAAI/bge-reranker-v2-m3 \
    RERANKER_DEVICE=cpu \
    RERANKER_MAX_LENGTH=512 \
    RERANKER_BATCH_SIZE=16

WORKDIR /app

RUN addgroup --system reranker && adduser --system --ingroup reranker reranker

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra index --no-install-project

COPY app ./app
RUN uv sync --frozen --no-dev --extra index \
    && mkdir -p /models/huggingface \
    && chown -R reranker:reranker /app /models

USER reranker

EXPOSE 8091

HEALTHCHECK --interval=30s --timeout=10s --start-period=180s --retries=10 \
    CMD python -c "import json,urllib.request; data=json.load(urllib.request.urlopen('http://localhost:8091/health', timeout=8)); raise SystemExit(0 if data.get('status') == 'ok' else 1)"

CMD ["uvicorn", "app.reranker.server:app", "--host", "0.0.0.0", "--port", "8091"]
