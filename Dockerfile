FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    GRADIO_ANALYTICS_ENABLED=False \
    DATA_DIR=/app/data

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md frontend.py ./
COPY src ./src
COPY scripts ./scripts
RUN pip install --no-cache-dir --no-deps . \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /app/data && chown -R app:app /app/data
USER app
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/health/ready', timeout=4)"
CMD ["python", "-m", "uvicorn", "pbl_jobs_finder.server:create_app", "--factory", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
