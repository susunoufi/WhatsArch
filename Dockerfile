FROM python:3.11-slim

# Install ffmpeg
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (CPU-only torch for smaller image)
COPY requirements.txt .
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

# Copy application
COPY run.py .
COPY chat_search/ chat_search/
COPY agent/ agent/

# Create chats directory
RUN mkdir -p chats

# WSGI entry point
RUN echo "from run import create_web_app; app = create_web_app()" > wsgi.py

# Create non-root user
RUN useradd --create-home --shell /bin/bash appuser && chown -R appuser:appuser /app
USER appuser

ENV PYTHONUNBUFFERED=1

# Configurable via env vars (defaults: 2 workers, 4 threads)
ENV GUNICORN_WORKERS=2
ENV GUNICORN_THREADS=4

# Railway sets $PORT dynamically
CMD gunicorn --bind 0.0.0.0:${PORT:-5000} --workers ${GUNICORN_WORKERS} --threads ${GUNICORN_THREADS} --timeout 300 wsgi:app

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT:-5000}/')" || exit 1
