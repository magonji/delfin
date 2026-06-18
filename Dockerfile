# Delfin — personal finance web app (FastAPI + static frontend + SQLite)
FROM python:3.12-slim

# Keep Python output unbuffered and skip .pyc files inside the container
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# tzdata lets the TZ env var set the container's local time, so the nightly
# maintenance runs at the configured wall-clock hour (e.g. 02:28 local, not UTC).
RUN apt-get update && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# WORKDIR must be the project root so the app's relative paths keep working:
#   - DB:       sqlite:///./data/finance.db   -> /app/data/finance.db
#   - frontend: StaticFiles(directory="frontend") -> /app/frontend
WORKDIR /app

# Install dependencies first so this layer is cached across code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# The SQLite DB lives here; mount a volume to persist it across container restarts
RUN mkdir -p /app/data
VOLUME ["/app/data"]

EXPOSE 8422

# Verify the app is actually serving (slim image has no curl, so use Python)
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8422/app/index.html', timeout=4).status==200 else 1)"

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8422"]
