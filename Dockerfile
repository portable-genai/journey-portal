# syntax=docker/dockerfile:1
FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS builder

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

FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:${PATH}" \
    PORT=8110
WORKDIR /app

# A digest pin freezes the base image, which means it also freezes its unpatched packages.
# Reproducible and vulnerable are not opposites, and the pin is otherwise cited as evidence of
# the first while quietly guaranteeing the second: this image shipped 39 fixable HIGH Debian
# advisories, every one of them with a released fix, because nothing here ever applied one.
# cdd-sow-research's Dockerfile has carried this reasoning and this layer for some time; the portal did not,
# and only a promotion-time scan said so.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY config ./config
# Remove pip from the RUNTIME image, in both the system prefix and the venv.
#
# Two reasons, and the second is what the promotion scan actually reported. First, a serving
# container installs nothing, so shipping a package manager in it adds an install capability an
# attacker can use and the application never can. Second, pip VENDORS its dependencies --
# msgpack and setuptools live inside pip/_vendor -- so a scanner reports pip's bundled copies as
# installed packages. Those were the only two findings left here once Debian updates were
# applied (msgpack 1.1.2, setuptools 70.3.0); neither is a dependency of this application,
# neither appears in any lockfile, and no constraint could reach them, because they were never
# resolved -- they arrived inside pip itself.
#
# The venv keeps its own setuptools, which some libraries still import as pkg_resources at
# runtime. Only pip goes. cdd-sow-research's image has done this since 2026-08-24; this one had not.
RUN rm -rf /usr/local/lib/python3.14/site-packages/pip \
           /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
           /opt/venv/lib/python3.14/site-packages/pip \
           /opt/venv/lib/python3.14/site-packages/pip-*.dist-info \
           /usr/local/bin/pip /usr/local/bin/pip3 /opt/venv/bin/pip /opt/venv/bin/pip3

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app app
USER 10001:10001
EXPOSE 8110
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8110')+'/healthz', timeout=2)"
CMD ["sh", "-c", "exec uvicorn journey_portal.api.app:app --host 0.0.0.0 --port ${PORT:-8110}"]
