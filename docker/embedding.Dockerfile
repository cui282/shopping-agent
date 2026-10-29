FROM ghcr.io/astral-sh/uv:0.13.6 AS uv

FROM python:3.10-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}" \
    HF_HOME=/models/huggingface

WORKDIR /app

RUN addgroup --system embedding && adduser --system --ingroup embedding embedding

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra index --no-install-project

COPY app ./app
RUN uv sync --frozen --no-dev --extra index \
    && mkdir -p /models/huggingface \
    && chown -R embedding:embedding /app /models

USER embedding

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=10 \
  CMD python -c "import json,urllib.request; data=json.load(urllib.request.urlopen('http://localhost:8090/health', timeout=8)); raise SystemExit(0 if data.get('status') == 'ok' else 1)"

CMD ["uvicorn", "app.embedding.server:app", "--host", "0.0.0.0", "--port", "8090"]
