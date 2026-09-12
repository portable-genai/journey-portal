# syntax=docker/dockerfile:1
# --platform=$BUILDPLATFORM: the builder emits only the static export (copied below), so
# it runs on the build host's own architecture rather than under emulation. See
# ui-ops/Dockerfile for the crash this avoids on cross-arch builds.
FROM --platform=$BUILDPLATFORM node:26-bookworm-slim@sha256:cd565714d4da3e84bfd341e31448f81d47c6362198f152345297c9c1154e6341 AS builder
# WHICH journey this image serves, chosen at BUILD time and not at runtime. Next.js inlines
# every NEXT_PUBLIC_* value into the compiled bundle, so a static export cannot be re-pointed
# afterwards: one image per persona host is the shape, not a deployment-time environment
# variable. `docker build --build-arg JOURNEY=mkt ui-rm` is the Marketing shell; the default is
# the journey this image has always served, so an unchanged build command is an unchanged shell.
#
# Terraform cannot read this back out of a digest, which is why `var.shells` records the journey
# as the operator's claim and `e2e/app_coverage.py` is what holds a hostname to the journey it
# actually renders: a shell whose journey is absent from the catalog falls back to the first one
# and serves a perfectly healthy page under the wrong persona's name.
ARG JOURNEY=rm
ENV PORTAL_STATIC_EXPORT=1 NEXT_PUBLIC_JOURNEY=$JOURNEY
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
