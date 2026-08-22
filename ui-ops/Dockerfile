# syntax=docker/dockerfile:1
FROM node:24-bookworm-slim@sha256:3638d9a6fe4030bd716be989438248074489337ba3275657f93595428be4fc03 AS builder
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM python:3.14-slim@sha256:ce40764625a4ff50df3548277632e7f96c4e77fe75fa848aae9885476e7df5a4 AS runtime
ENV PORT=8080 FRAME_ANCESTORS="'self'"
WORKDIR /app
COPY --from=builder /app/dist/ops-journey-shell/browser ./
COPY static_server.py /static_server.py
RUN useradd --system --uid 10001 --home-dir /app app && chown -R app /app
USER 10001
EXPOSE 8080
CMD ["python", "/static_server.py"]
