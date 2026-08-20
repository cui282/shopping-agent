FROM ghcr.io/astral-sh/uv:0.12.5 AS uv

FROM python:3.10-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

RUN addgroup --system shopping && adduser --system --ingroup shopping shopping

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --extra production --no-install-project

COPY app ./app
RUN uv sync --frozen --no-dev --extra production

RUN mkdir -p /app/output /app/uploaded /app/data \
    && chown -R shopping:shopping /app/output /app/uploaded /app/data

USER shopping

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=3)"

CMD ["uvicorn", "app.api.server:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
