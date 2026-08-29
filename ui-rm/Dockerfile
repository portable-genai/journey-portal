# syntax=docker/dockerfile:1
# --platform=$BUILDPLATFORM: the builder emits only the static export (copied below), so
# it runs on the build host's own architecture rather than under emulation. See
# ui-ops/Dockerfile for the crash this avoids on cross-arch builds.
FROM --platform=$BUILDPLATFORM node:26-bookworm-slim@sha256:cd565714d4da3e84bfd341e31448f81d47c6362198f152345297c9c1154e6341 AS builder
ENV PORTAL_STATIC_EXPORT=1
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS runtime
ENV PORT=8080 FRAME_ANCESTORS="'self'"
WORKDIR /app

# A digest pin freezes the base image, which means it also freezes its unpatched packages.
# Reproducible and vulnerable are not opposites. The BFF image learned this on 2026-08-26 and
# these two shells did not: they carry the same `python:3.14-slim` runtime and shipped the same
# 39 fixable HIGH Debian advisories, every one with a released fix, plus the two findings pip
# VENDORS (msgpack, setuptools live inside pip/_vendor). Neither vendored package is a
# dependency of a static file server, neither appears in any lockfile, and no constraint could
# reach them, because they were never resolved -- they arrived inside pip itself. A serving
# container installs nothing, so the package manager goes with them.
#
# Only a promotion-time scan said so, on both images, which is the argument for the scan being
# blocking rather than advisory.
RUN apt-get update \
 && apt-get upgrade -y --no-install-recommends \
 && rm -rf /var/lib/apt/lists/* \
 && rm -rf /usr/local/lib/python3.14/site-packages/pip \
           /usr/local/lib/python3.14/site-packages/pip-*.dist-info \
           /usr/local/bin/pip /usr/local/bin/pip3
COPY --from=builder /app/out ./
COPY static_server.py /static_server.py
RUN useradd --system --uid 10001 --home-dir /app app && chown -R app /app
USER 10001
EXPOSE 8080
CMD ["python", "/static_server.py"]
