# Linux deployment of the existing app. A fresh Linux build is a release gate;
# the Windows dependency snapshot is deliberately not used here.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE}

ARG TRACEBRIDGE_REVISION=unrecorded
LABEL org.opencontainers.image.revision=${TRACEBRIDGE_REVISION}

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    HOME=/tmp/tracebridge-home \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 tracebridge \
    && useradd --uid 10001 --gid 10001 --no-create-home tracebridge

COPY pyproject.toml README.md ./
COPY tracebridge/ ./tracebridge/
# The optional test dependency is needed by the existing fixture demo's
# bounded reproduction commands; it is not the Windows test lock file.
RUN python -m pip install --no-cache-dir ".[test,service,postgres]" \
    && python -m pip check \
    && python -c "import sqlite3; c=sqlite3.connect(':memory:'); c.execute('CREATE VIRTUAL TABLE check_fts USING fts5(body)'); import tracebridge.report_agent"

COPY app.py nat_workflow.yml ./
COPY .streamlit/config.toml ./.streamlit/config.toml
COPY pages/ ./pages/
COPY scripts/ ./scripts/
COPY deploy/ ./deploy/
COPY examples/ ./examples/
COPY skills/tracebridge-triage/ ./skills/tracebridge-triage/
COPY tests/fixtures/agolive_repo/ ./tests/fixtures/agolive_repo/

RUN mkdir -p /app/output /app/generated \
    && chown 10001:10001 /app/output /app/generated

USER 10001:10001
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3).read()"]

CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
