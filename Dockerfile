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
COPY requirements.txt constraints.txt ./
# Runtime packages only (no pytest), at the tested versions.
RUN grep -v '^pytest' requirements.txt > runtime.txt \
    && pip install --no-cache-dir -r runtime.txt -c constraints.txt \
    && rm runtime.txt
COPY assist/ assist/
COPY eval/ eval/
COPY pipeline/ pipeline/
COPY server/ server/
COPY --from=web /web/dist web/dist
# Hugging Face Spaces run the container as uid 1000, so the data folder must be writable by any user.
RUN mkdir -p /data && chmod 777 /data
ENV THRESHOLD_DATA=/data \
    HF_HOME=/tmp/hf \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["sh", "-c", "mkdir -p \"$THRESHOLD_DATA\" && exec uvicorn server.app:create_app --factory --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
