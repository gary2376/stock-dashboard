# Demo image: synthetic data only, no AI CLI inside. For real use, run scripts/run_local.sh on the host
# where your own `claude` / `codex` CLI is logged in.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    STOCK_DASHBOARD_DATA=/app/data_demo STOCK_DASHBOARD_DEMO=1

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend backend
# only what the app imports (backend -> generate_analysis) plus the demo builder; no other scripts
COPY scripts/generate_analysis.py scripts/load_demo.py scripts/
COPY frontend frontend
COPY profiles profiles

RUN python scripts/load_demo.py /app/data_demo && useradd -m app && chown -R app /app/data_demo  # code stays root-owned; only the data dir is writable
USER app

EXPOSE 8013
WORKDIR /app/backend
CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8013"]
