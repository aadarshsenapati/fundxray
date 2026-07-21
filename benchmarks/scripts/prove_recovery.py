"""Failure-recovery proof for the streaming consumer.

Claiming "exactly-once" without evidence is worthless. This script produces the
evidence:

  1. Replay a tick stream containing duplicates, late arrivals and malformed rows
  2. Start the consumer, let it process part of the stream, then KILL it mid-run
  3. Restart from the same checkpoint
  4. Assert: every distinct valid event landed exactly once, no loss, no
     double-counting, and malformed rows went to the dead-letter path

Exit code 0 only if all assertions hold. Wired into CI.
"""
from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path

from pipelines.spark.session import build_session
from pipelines.spark.streaming.tick_consumer import build_query, read_stream
from pipelines.spark.streaming.tick_producer import replay

BASE = Path("/tmp/fx_recovery")


def run_consumer(seconds: int, paths: dict, max_files: int = 2) -> int:
    """Run the consumer for a bounded time, then stop it — simulating a kill."""
    spark = build_session("recovery-proof", shuffle_partitions=4)
    good, bad = read_stream(spark, "file", paths["input"], max_files=max_files)

    dlq = (bad.writeStream.format("parquet").option("path", paths["dlq"])
           .option("checkpointLocation", paths["checkpoint"] + "_dlq")
           .outputMode("append").start())
    q = build_query(good, paths["output"], paths["checkpoint"]).start()

    time.sleep(seconds)
    q.stop()            # abrupt stop mid-stream; checkpoint is the only state
    dlq.stop()
    written = spark.read.parquet(paths["output"]).count() if Path(paths["output"]).exists() else 0
    spark.stop()
    return written


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--first-run", type=int, default=20)
    p.add_argument("--second-run", type=int, default=35)
    a = p.parse_args()

    if BASE.exists():
        shutil.rmtree(BASE)
    paths = {"input": str(BASE / "input"), "output": str(BASE / "output"),
             "checkpoint": str(BASE / "checkpoint"), "dlq": str(BASE / "dlq")}

    stats = replay(Path(paths["input"]), n_symbols=20, ticks_per_symbol=50)
    expected = json.loads((BASE / "expected.json").read_text())
    print(f"\nreplayed {stats['events_written']} events "
          f"({stats['distinct_valid_events']} distinct valid, "
          f"{stats['events_written'] - stats['distinct_valid_events']} dupes/malformed)")

    print(f"\n--- RUN 1: process for {a.first_run}s, then kill mid-stream ---")
    n1 = run_consumer(a.first_run, paths)
    print(f"    rows after kill: {n1}")

    print(f"\n--- RUN 2: restart from the SAME checkpoint for {a.second_run}s ---")
    n2 = run_consumer(a.second_run, paths)
    print(f"    rows after restart: {n2}")

    spark = build_session("recovery-verify", shuffle_partitions=4)
    out = spark.read.parquet(paths["output"])
    total = out.count()
    distinct = out.select("event_id").distinct().count()
    dlq_rows = spark.read.parquet(paths["dlq"]).count() if Path(paths["dlq"]).exists() else 0
    ids = {r["event_id"] for r in out.select("event_id").distinct().collect()}
    spark.stop()

    missing = set(expected) - ids
    extra = ids - set(expected)

    print("\n=== RECOVERY PROOF ===")
    print(f"  expected distinct valid events : {len(expected)}")
    print(f"  rows written                   : {total}")
    print(f"  distinct event_ids written     : {distinct}")
    print(f"  duplicates written             : {total - distinct}")
    print(f"  missing (data loss)            : {len(missing)}")
    print(f"  unexpected ids                 : {len(extra)}")
    print(f"  malformed -> dead-letter queue : {dlq_rows}")
    print(f"  progressed across restart      : {n1} -> {n2}")

    checks = {
        "no duplicates after restart": total == distinct,
        "no unexpected events": not extra,
        "malformed routed to DLQ": dlq_rows > 0,
        "progress resumed from checkpoint": n2 >= n1,
        "no data loss": not missing,
    }
    print()
    for name, ok in checks.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    if not all(checks.values()):
        raise SystemExit(1)
    print("\nALL RECOVERY ASSERTIONS PASSED")


if __name__ == "__main__":
    main()
