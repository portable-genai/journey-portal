# syntax=docker/dockerfile:1
FROM node:20-bookworm-slim@sha256:2cf067cfed83d5ea958367df9f966191a942351a2df77d6f0193e162b5febfc0 AS builder
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
