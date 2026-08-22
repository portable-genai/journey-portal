# syntax=docker/dockerfile:1
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"
COPY requirements-build.lock requirements-gcp.lock pyproject.toml README.md ./
RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && pip install -r requirements-build.lock \
 && pip install -r requirements-gcp.lock \
 && apt-get purge -y git \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*
COPY src ./src
RUN pip install --no-build-isolation --no-deps .

FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PORT=8110
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY config ./config
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app app
USER 10001:10001
EXPOSE 8110
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8110')+'/healthz', timeout=2)"
CMD ["sh", "-c", "exec uvicorn journey_portal.api.app:app --host 0.0.0.0 --port ${PORT:-8110}"]
