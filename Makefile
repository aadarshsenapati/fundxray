.PHONY: help up down seed ingest analytics artifact serve test lint fmt backfill clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

up:            ## Start local stack (Spark, Hive Metastore, Kafka, MinIO, Postgres, Dagster)
	docker compose -f infra/compose/docker-compose.yml up -d

down:          ## Stop local stack
	docker compose -f infra/compose/docker-compose.yml down

seed:          ## Bootstrap reference data (schemes, cap classification, ISIN map)
	python -m pipelines.ingestion.reference.seed

ingest:        ## Ingest one disclosure month, e.g. make ingest MONTH=2026-06
	python -m pipelines.ingestion.amfi.run --month $(MONTH)

backfill:      ## Backfill a range, e.g. make backfill FROM=2016-01 TO=2026-06
	dagster job execute -j backfill_disclosures --config-json '{"from":"$(FROM)","to":"$(TO)"}'

analytics:     ## Build gold marts
	python -m pipelines.spark.analytics.run_all

artifact:      ## Build the compacted serving artifact
	python -m serving.artifacts.build

serve:         ## Run API + web locally
	uvicorn serving.api.main:app --reload --host 0.0.0.0 --port 8000

test:          ## Run the test suite
	pytest tests -v

lint:          ## Lint and type-check
	ruff check . && mypy core pipelines serving

fmt:           ## Format
	ruff format .

clean:         ## Remove local derived data
	rm -rf data/staging/* data/artifacts/* spark-warehouse metastore_db derby.log
