FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app:/app/core PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# Spark is only needed for full-history jobs, not for serving.
RUN grep -v '^pyspark' requirements.txt > /tmp/req.txt \
    && pip install --no-cache-dir -r /tmp/req.txt

COPY . .
EXPOSE 8000
CMD ["uvicorn", "serving.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
