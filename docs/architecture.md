# Architecture

## Design principles

1. **Compute offline, serve cheap.** All heavy Spark work runs on a schedule and publishes a compacted artifact. The user-facing API never runs a distributed job.
2. **Raw data is immutable.** Bronze is append-only. Every downstream table is reproducible from bronze plus code.
3. **Correctness over coverage.** A scheme that fails reconciliation is quarantined, not shown with a caveat.
4. **Every number is traceable.** Source file, disclosure date, and ingestion run ID travel with every row.

## Planes

### Batch plane (authoritative)
`Bronze` raw disclosure files and parse output, partitioned by AMC and disclosure month, untouched.
`Silver` conformed holdings — one schema, ISIN-resolved, corporate-action adjusted, reconciled against reported AUM.
`Gold` analytical marts — overlap matrices, active share, drift series, crowding, DTL.

Tables are Apache Iceberg over object storage, catalogued in the Hive Metastore so both Spark and DuckDB can read them. Iceberg is chosen for schema evolution (a decade of changing AMC formats), snapshot isolation, and time travel — see `adr/0001`.

### Speed plane
Angel One SmartAPI WebSocket ticks for the ~500 stocks that dominate MF holdings, produced to Kafka, consumed by Spark Structured Streaming with watermarking and checkpointing. Used only for revaluing look-through exposure — it never writes to the authoritative marts.

### Serving plane
An artifact builder collapses gold marts into a single DuckDB file (tens of MB for the whole industry) pushed to object storage. FastAPI loads it into memory and resolves a user's portfolio in milliseconds. This is why hosting costs nothing.

## Orchestration

Dagster software-defined assets, partitioned by disclosure month. Backfilling the full history is a partitioned, resumable run rather than a bespoke script. Sensors watch AMFI for new disclosure publication.

## Data quality

Contracts declared per source in `pipelines/quality/contracts/`. Enforced in CI and at runtime:
- schema conformance and type checks
- reconciliation of parsed holdings against each scheme's reported AUM within tolerance
- freshness SLAs per source
- referential integrity on ISIN resolution
Violations route to quarantine tables and fail the asset — they never propagate to serving.

## Local development

Docker Compose brings up Spark, Hive Metastore, Kafka, MinIO, Postgres and Dagster. Start with a single month and the ten largest AMCs before attempting a full backfill.
