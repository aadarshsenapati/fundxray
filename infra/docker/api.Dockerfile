FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app:/app/core PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN grep -v '^pyspark' requirements.txt > /tmp/req.txt \
    && pip install --no-cache-dir -r /tmp/req.txt

COPY . .

# Build the demo artifact at image build time, not at every boot.
RUN python -m pipelines.ingestion.reference.seed \
    && python -m serving.artifacts.build

EXPOSE 8000
CMD uvicorn serving.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
