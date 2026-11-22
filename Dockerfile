FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DB_PATH=/data/tt_bot.db

RUN useradd --create-home --shell /usr/sbin/nologin botuser \
    && mkdir -p /data \
    && chown -R botuser:botuser /app /data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=botuser:botuser src/ ./src/

USER botuser
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, sqlite3; p=os.getenv('DB_PATH','/data/tt_bot.db'); assert os.path.exists(p); sqlite3.connect(p).execute('SELECT 1').fetchone()"

CMD ["python", "-m", "src.main"]
