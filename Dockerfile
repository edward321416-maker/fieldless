# 1) Build the web app.
FROM node:24-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# 2) Python server with ffmpeg; serves the API and web/dist.
FROM python:3.14-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY pipeline/ pipeline/
COPY server/ server/
COPY --from=web /web/dist web/dist
ENV THRESHOLD_DATA=/data \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["sh", "-c", "mkdir -p \"$THRESHOLD_DATA\" && exec uvicorn server.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8000}"]
