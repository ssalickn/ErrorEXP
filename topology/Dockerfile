# topology/Dockerfile
# Multi-stage image for the topology pipeline.
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . /app/topology
RUN pip install -e .

# Default: run the scheduler once and exit (CronJob pattern)
ENTRYPOINT ["python", "-m", "topology.refresh.scheduler"]
CMD ["--config-dir", "/app/topology/config", "--var-dir", "/app/var"]
