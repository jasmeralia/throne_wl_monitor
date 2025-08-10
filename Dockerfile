FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system deps for lxml/html parsing
RUN apt-get update && apt-get install -y --no-install-recommends     build-essential     libxml2-dev     libxslt1-dev     ca-certificates     && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY monitor_throne.py /app/monitor_throne.py

# Create a non-root user
RUN useradd -u 10001 -m appuser
USER appuser

VOLUME ["/data"]
ENV STATE_DB=/data/state.sqlite3

# Defaults
ENV MODE=daemon
ENV POLL_MINUTES=10

CMD ["python", "/app/monitor_throne.py"]
