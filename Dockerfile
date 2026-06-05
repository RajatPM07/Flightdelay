# TripSecure+ Flight Delay — shareable demo image.
# Runs the FastAPI app in demo mode: flight data + messaging are mocked,
# storage falls back to on-container SQLite (no external DB / vendor keys needed).
FROM python:3.12-slim

# Faster, quieter, reproducible Python in containers.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# App code (see .dockerignore for what's excluded).
COPY app ./app

# Demo defaults — override at deploy time if needed.
ENV DEMO_MODE=True \
    MOCK_PROVIDERS=True \
    MOCK_MESSAGING=True \
    APP_ENV=demo \
    LOG_LEVEL=INFO

EXPOSE 8000

# Bind to the platform-provided $PORT (Render/Railway/Fly set this), default 8000 locally.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
