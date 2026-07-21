"""Structured Streaming consumer for the tick feed.

Correctness properties, all exercised by benchmarks/scripts/prove_recovery.py:
  * event-time watermarking, so late arrivals inside the window still count
  * dropDuplicates on event_id within the watermark, so at-least-once delivery
    from the source becomes effectively-once processing
  * checkpointing, so a killed job resumes exactly where it stopped
  * malformed records routed to a dead-letter path instead of killing the job

The source is pluggable: `kafka` in production, `file` for reproducible tests.
"""
from __future__ import annotations

import argparse

from fundxray_core.utils.logging import get_logger
from pipelines.spark.session import build_session

log = get_logger(__name__)

TICK_SCHEMA = ("event_id STRING, isin STRING, token STRING, ltp DOUBLE, "
               "seq INT, event_time TIMESTAMP")


def read_stream(spark, source: str, path: str = "", bootstrap: str = "",
                topic: str = "marketdata.ticks", max_files: int | None = None):
    from pyspark.sql import functions as F

    if source == "kafka":
        raw = (spark.readStream.format("kafka")
               .option("kafka.bootstrap.servers", bootstrap)
               .option("subscribe", topic)
               .option("startingOffsets", "earliest")
               .load()
               .selectExpr("CAST(value AS STRING) AS json"))
    else:
        reader = spark.readStream.format("text").option("path", path)
        if max_files:
            reader = reader.option("maxFilesPerTrigger", max_files)
        raw = reader.load().withColumnRenamed("value", "json")

    parsed = raw.withColumn("t", F.from_json(F.col("json"), TICK_SCHEMA))
    good = (parsed.filter(F.col("t").isNotNull()
                          & F.col("t.event_id").isNotNull()
                          & F.col("t.ltp").isNotNull())
                  .select("t.*"))
    bad = parsed.filter(F.col("t").isNull()
                        | F.col("t.event_id").isNull()
                        | F.col("t.ltp").isNull()).select("json")
    return good, bad


def build_query(good, out_path: str, checkpoint: str, watermark: str = "1 minute"):
    # Watermark bounds the state store; dropDuplicates within it turns
    # at-least-once source delivery into effectively-once processing.
    deduped = (good.withWatermark("event_time", watermark)
                   .dropDuplicates(["event_id", "event_time"]))
    return (deduped.writeStream
            .format("parquet")
            .option("path", out_path)
            .option("checkpointLocation", checkpoint)
            .outputMode("append")
            .trigger(processingTime="2 seconds"))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--source", choices=["file", "kafka"], default="file")
    p.add_argument("--path", default="/tmp/fx_stream/input")
    p.add_argument("--out", default="/tmp/fx_stream/output")
    p.add_argument("--checkpoint", default="/tmp/fx_stream/checkpoint")
    p.add_argument("--dlq", default="/tmp/fx_stream/dlq")
    p.add_argument("--bootstrap", default="localhost:9092")
    p.add_argument("--max-files", type=int, default=2)
    p.add_argument("--timeout", type=int, default=60)
    a = p.parse_args()

    spark = build_session("fundxray-ticks", shuffle_partitions=8)
    good, bad = read_stream(spark, a.source, a.path, a.bootstrap, max_files=a.max_files)

    dlq = (bad.writeStream.format("parquet").option("path", a.dlq)
           .option("checkpointLocation", a.checkpoint + "_dlq")
           .outputMode("append").start())
    q = build_query(good, a.out, a.checkpoint).start()

    q.awaitTermination(a.timeout)
    q.stop(); dlq.stop(); spark.stop()
