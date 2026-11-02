FROM python:3.12-slim

WORKDIR /app

# Install system dependencies if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 appuser

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install --retries 10 --timeout 300 -r requirements.txt

COPY app/ ./app/
RUN chown -R appuser:appuser /app

USER appuser

ENV PYTHONUNBUFFERED=1
ENV DB_HOST=nepal-postgis
ENV DB_PORT=5432

EXPOSE 8001

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
