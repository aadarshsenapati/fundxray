# Streaming Layer — Correctness and Recovery

The speed plane revalues look-through exposure in real time from Angel One SmartAPI
ticks. It never writes to the authoritative marts — the batch plane remains the
source of truth.

## Design

```
SmartAPI WebSocket ──▶ Kafka (marketdata.ticks) ──▶ Spark Structured Streaming ──▶ serving
                                                            │
                                                            └──▶ dead-letter queue
```

Source is pluggable (`--source kafka` in production, `--source file` for
reproducible tests), so the exact same query is exercised in CI without a broker.

**Correctness properties, all implemented in `pipelines/spark/streaming/tick_consumer.py`:**

| Property | Mechanism |
|---|---|
| Late arrivals still counted | event-time watermark (`withWatermark`, 1 minute) |
| At-least-once → effectively-once | `dropDuplicates(["event_id", "event_time"])` inside the watermark |
| Resume after failure | `checkpointLocation` — the only state that survives a kill |
| Malformed rows don't kill the job | routed to a dead-letter parquet path |
| Bounded state | watermark evicts old keys from the state store |

## The replay harness

`pipelines/spark/streaming/tick_producer.py --mode replay` generates a tick
stream that **deliberately injects the failure modes**: exact duplicates (~5%),
out-of-order arrivals backdated 30 s (~5%), and unparseable payloads (~2%). A
clean stream would prove nothing.

## Measured result

Run via `python benchmarks/scripts/prove_recovery.py`:

```
replayed 1,119 events  (1,054 distinct valid, 65 duplicates/malformed)
consumer run 1         →  1,054 rows written
```

**Verified — deduplication and dead-lettering.** The consumer wrote exactly
1,054 rows from 1,119 delivered events. Every injected duplicate was dropped and
every malformed payload was excluded from the main path.

**Verified — the kill lands mid-stream.** With `--first-run 6` the consumer had
written **196 of 1,054 rows** when it was killed, so the checkpoint genuinely
holds partial progress rather than a completed stream:

```
--- RUN 1: process for 6s, then kill mid-stream ---
    rows after kill: 196
```

**Not yet verified — the restart assertions.** Run 2 (resume from the same
checkpoint) did not finish inside the time budget available on a 2 vCPU box;
Structured Streaming's state-store and file-source log recovery on restart is
slow on this rig. The five assertions — no duplicates, no unexpected events,
DLQ populated, progress resumed, no data loss — remain unevaluated.

**To complete the proof on real hardware:**

```bash
python benchmarks/scripts/prove_recovery.py --first-run 8 --second-run 60
```

The script asserts five properties and exits non-zero if any fail: no
duplicates after restart, no unexpected events, malformed routed to DLQ,
progress resumed from checkpoint, and no data loss. Until that run is green,
**do not claim exactly-once recovery on your resume** — claim deduplication and
dead-lettering, which are measured.

## Running with a real Kafka broker

```bash
docker compose --profile stream up -d kafka
python -m pipelines.spark.streaming.tick_producer --mode live      # needs SmartAPI creds
python -m pipelines.spark.streaming.tick_consumer --source kafka
```

Note the Kafka source requires `spark-sql-kafka-0-10` from Maven, so the machine
needs package-repository access at first run.
