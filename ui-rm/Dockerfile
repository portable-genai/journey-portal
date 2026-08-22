# syntax=docker/dockerfile:1
FROM node:26-bookworm-slim@sha256:cd565714d4da3e84bfd341e31448f81d47c6362198f152345297c9c1154e6341 AS builder
ENV PORTAL_STATIC_EXPORT=1
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf AS runtime
ENV PORT=8080 FRAME_ANCESTORS="'self'"
WORKDIR /app
COPY --from=builder /app/out ./
COPY static_server.py /static_server.py
RUN useradd --system --uid 10001 --home-dir /app app && chown -R app /app
USER 10001
EXPOSE 8080
CMD ["python", "/static_server.py"]
