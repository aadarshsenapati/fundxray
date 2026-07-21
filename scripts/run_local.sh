#!/usr/bin/env bash
# One-command local bring-up. No cloud, no credentials required.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=".:core"

echo "==> installing dependencies"
pip install -q -r requirements.txt

echo "==> seeding sample warehouse (synthetic; swap for real AMFI ingestion later)"
python -m pipelines.ingestion.reference.seed

echo "==> building serving artifact"
python -m serving.artifacts.build

echo "==> starting API on http://localhost:8000"
uvicorn serving.api.main:app --reload --host 0.0.0.0 --port 8000
